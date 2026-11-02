"""The warden: the first code a Function tool's process runs (ADR-0005, ADR-0016, #99).

Main starts it as ``python -I -m allin.confinement.boot <main pid> <channel fd>``, in a new
session, with ``/dev/null`` as input and a clean environment. It dies with the thread that
launched it, reads the call from its channel, confines itself, and forks the worker that
imports and runs the function. When the worker answers, or Main cancels, or Main dies, it kills
every process of the call with ``kill(-1)``: once Landlock scopes signals, that reaches only
the processes of this call's own domain, grandchildren that left the session included, and
never Main or another call.

It reads and writes the channel's messages (``messages.py``) with the standard library, not
pydantic, which would add some 50 ms to every call (#100). Main parses what comes back.
"""

import contextlib
import ctypes
import importlib
import json
import multiprocessing
import os
import selectors
import signal
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from allin.confinement import landlock, seccomp
from allin.core.errors import ConfinementFailed

_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36
_libc = ctypes.CDLL(None, use_errno=True)

type _Json = bool | int | float | str | list["_Json"] | dict[str, "_Json"] | None


@dataclass(frozen=True)
class _Call:
    """``messages.CallTool``, as Main wrote it."""

    package: str
    module: str
    function: str
    arguments: dict[str, _Json]
    workspace: str
    read: tuple[Path, ...]
    write: tuple[Path, ...]


def main() -> None:
    main_pid, channel_fd = int(sys.argv[1]), int(sys.argv[2])
    _die_with(main_pid, signal.SIGTERM)  # SIGTERM, so the handler can take the call down
    _libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)  # orphaned grandchildren come back here
    channel = socket.socket(fileno=channel_fd)
    call = _read_call(channel)
    try:
        scoped = _confine(call)
    except ConfinementFailed as error:
        channel.sendall(_line({"type": "unconfined", **error.fields}))
        os._exit(0)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: _take_down_and_exit(scoped))
    reply_out, reply_in = os.pipe()
    worker = os.fork()
    if worker == 0:
        os.close(reply_out)
        channel.close()
        _run_function(call, reply_in)
    os.close(reply_in)
    reply = _wait(channel, reply_out, worker, scoped)
    if reply is not None:
        channel.sendall(reply)
    os._exit(0)


def _die_with(parent_pid: int, sig: signal.Signals) -> None:
    # Before confining there is nothing to take down: Main's death just ends the warden.
    signal.signal(signal.SIGTERM, lambda _signum, _frame: os._exit(0))
    if _libc.prctl(_PR_SET_PDEATHSIG, sig, 0, 0, 0) != 0:
        os._exit(1)  # without it the call could outlive Main
    if os.getppid() != parent_pid:  # it died before prctl took
        os._exit(1)


def _read_call(channel: socket.socket) -> _Call:
    try:
        message = json.loads(channel.makefile("rb").readline())
        return _Call(
            package=message["package"],
            module=message["module"],
            function=message["function"],
            arguments=message["arguments"],
            workspace=message["workspace"],
            read=tuple(map(Path, message["read"])),
            write=tuple(map(Path, message["write"])),
        )
    except (ValueError, KeyError, TypeError):
        os._exit(2)  # Main sent no call: nothing to run


def _line(message: dict[str, _Json]) -> bytes:
    return json.dumps(message, allow_nan=False).encode() + b"\n"


def _confine(call: _Call) -> bool:
    """Confine this process for good; whether signals are scoped to its domain."""
    restricted = landlock.restrict_self(call.read, call.write)
    seccomp.apply_filter()
    return restricted.signals_scoped


def _wait(channel: socket.socket, reply_out: int, worker: int, scoped: bool) -> bytes | None:
    """Wait for the worker's answer or Main's cancel; then take the call down. The line to
    send Main, as the worker wrote it (Main parses it); ``None`` when Main cancelled."""
    selector = selectors.DefaultSelector()
    selector.register(channel, selectors.EVENT_READ)
    selector.register(reply_out, selectors.EVENT_READ)
    answer = b""
    while True:
        for key, _ in selector.select():
            if key.fileobj is channel:  # a cancel, or Main closed its end
                _take_down(scoped)
                os.waitpid(worker, 0)
                return None
            chunk = os.read(reply_out, 1 << 16)
            answer += chunk
            if b"\n" in answer:
                _take_down(scoped)
                os.waitpid(worker, 0)
                return answer.split(b"\n", 1)[0] + b"\n"
            if not chunk:  # the worker ended without answering
                _, status = os.waitpid(worker, 0)
                _take_down(scoped)
                return _line({"type": "died", "exit_code": os.waitstatus_to_exitcode(status)})


def _take_down(scoped: bool) -> None:
    """Kill every other process of this call."""
    if not scoped:
        return  # unscoped, kill(-1) would reach every process of the user
    with contextlib.suppress(ProcessLookupError):  # nothing else was left
        os.kill(-1, signal.SIGKILL)


def _take_down_and_exit(scoped: bool) -> None:
    _take_down(scoped)
    os._exit(0)


def _run_function(call: _Call, reply_in: int) -> None:
    """The worker: import the function from its package, run it in the Carpeta de trabajo and
    write its answer. It never returns."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)  # dies with the warden
    os.chdir(call.workspace)
    # ``-I`` left the standard library and the venv; the package goes last, so it can never
    # shadow them. The Carpeta de trabajo is never on the path.
    sys.path.append(call.package)
    # forkserver, the default since 3.14, needs a unix socket the filter refuses (#99).
    multiprocessing.set_start_method("spawn", force=True)
    try:
        module = importlib.import_module(call.module)
        value = getattr(module, call.function)(**call.arguments)
        answer = _line({"type": "returned", "value": value})
    except BaseException as error:  # noqa: BLE001 - the function's error is its output, as data
        answer = _line({"type": "raised", "error": f"{type(error).__name__}: {error}"})
    sys.stdout.flush()
    sys.stderr.flush()
    with os.fdopen(reply_in, "wb") as out:  # writes it whole, however long
        out.write(answer)
    os._exit(0)


if __name__ == "__main__":
    main()
