"""The Vigilante (warden): the first code a Function tool's process runs (ADR-0005, ADR-0016).

Main starts it as ``python -I -m allin.confinement.warden <main pid> <channel fd>``, in a new
session, with ``/dev/null`` as input and a clean environment. It dies with the thread that
launched it, reads the call from its channel, confines itself, and forks the runner: the
process that imports and runs the function. When the runner answers, or Main cancels, or Main
dies, it kills every process of the call with ``kill(-1)``: once Landlock scopes signals, that
reaches only the processes of this call's own domain, grandchildren that left the session
included, and never Main or another call. Without that scope it kills only the runner, and Main
kills the rest of the process group.

It reads and writes the channel's messages (``messages.py``) with the standard library, not
pydantic, whose import costs some 35 ms of the call (#100): Main sends the call and parses with
pydantic what comes back, so the boundary that matters is still parsed there.
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
from typing import cast

from allin.confinement import landlock, seccomp
from allin.core.errors import ConfinementFailed

_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36
_ANSWER_MAX = 16 * 1024 * 1024  # as Main's side: past it, the answer is not one
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
    connect: tuple[int, ...] | None
    net_layer: int | None
    ip: bool
    unix: bool


@dataclass(frozen=True)
class _Runner:
    pid: int
    scoped: bool  # whether signals are scoped to the call's domain, so kill(-1) is safe


def main() -> None:
    main_pid, channel_fd = int(sys.argv[1]), int(sys.argv[2])
    # Before confining there is nothing to take down: Main's death just ends the warden.
    signal.signal(signal.SIGTERM, lambda _signum, _frame: os._exit(0))
    _die_with(main_pid, signal.SIGTERM)  # SIGTERM, so a handler can take the call down
    _libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)  # orphaned grandchildren come back here
    channel = socket.socket(fileno=channel_fd)
    call = _read_call(channel)
    try:
        scoped = _confine(call)
    except ConfinementFailed as error:
        channel.sendall(_line({"type": "unconfined", **error.fields}))
        os._exit(0)
    warden = os.getpid()
    reply_out, reply_in = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reply_out)
        channel.close()
        _run_function(call, reply_in, warden)
    os.close(reply_in)
    runner = _Runner(pid=pid, scoped=scoped)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: _take_down_and_exit(runner))
    reply = _wait(channel, reply_out, runner)
    if reply is not None:
        channel.sendall(reply)
    os._exit(0)


def _die_with(parent_pid: int, sig: signal.Signals) -> None:
    if _libc.prctl(_PR_SET_PDEATHSIG, sig, 0, 0, 0) != 0:
        os._exit(1)  # without it the call could outlive its parent
    if os.getppid() != parent_pid:  # it died before prctl took
        os._exit(1)


def _read_call(channel: socket.socket) -> _Call:
    try:
        call = _parse_call(json.loads(channel.makefile("rb").readline()))
    except ValueError:
        call = None
    if call is None:
        os._exit(2)  # Main sent no call: nothing to run
    return call


def _parse_call(message: object) -> _Call | None:
    if not isinstance(message, dict):
        return None
    fields: dict[object, object] = message  # pyright: ignore[reportUnknownVariableType] - JSON
    package, module, function, workspace = (
        fields.get(key) for key in ("package", "module", "function", "workspace")
    )
    arguments = fields.get("arguments")
    read, write = _paths(fields.get("read")), _paths(fields.get("write"))
    connect, net_layer = fields.get("connect"), fields.get("net_layer")
    ports = None if connect is None else _ports(connect)
    ip, unix = fields.get("ip"), fields.get("unix")
    if not (
        fields.get("type") == "call"
        and isinstance(package, str)
        and isinstance(module, str)
        and isinstance(function, str)
        and isinstance(workspace, str)
        and isinstance(arguments, dict)
        and read is not None
        and write is not None
        and (connect is None or ports is not None)
        and (net_layer is None or (isinstance(net_layer, int) and not isinstance(net_layer, bool)))
        and isinstance(ip, bool)
        and isinstance(unix, bool)
    ):
        return None
    return _Call(
        package=package,
        module=module,
        function=function,
        arguments=cast("dict[str, _Json]", arguments),  # decoded from JSON: JSON values
        workspace=workspace,
        read=read,
        write=write,
        connect=ports,
        net_layer=net_layer,
        ip=ip,
        unix=unix,
    )


def _ports(raw: object) -> tuple[int, ...] | None:
    if not isinstance(raw, list):
        return None
    items: list[object] = raw  # pyright: ignore[reportUnknownVariableType] - JSON
    ports = tuple(i for i in items if isinstance(i, int) and not isinstance(i, bool))
    return ports if len(ports) == len(items) else None


def _paths(raw: object) -> tuple[Path, ...] | None:
    if not isinstance(raw, list):
        return None
    items: list[object] = raw  # pyright: ignore[reportUnknownVariableType] - JSON
    paths = tuple(Path(item) for item in items if isinstance(item, str))
    return paths if len(paths) == len(items) else None


def _line(message: dict[str, _Json]) -> bytes:
    return json.dumps(message, allow_nan=False).encode() + b"\n"


def _confine(call: _Call) -> bool:
    """Confine this process for good; whether signals are scoped to its domain. net=True's
    shared layer goes last, which is cheaper (#99), and its descriptor is closed before the
    function is imported: it holds no more than its channel then (ADR-0005)."""
    restricted = landlock.restrict_self(call.read, call.write, call.connect)
    if call.net_layer is not None:
        landlock.apply_layer(call.net_layer)
        os.close(call.net_layer)
    seccomp.apply_filter(ip=call.ip, unix=call.unix)
    return restricted.signals_scoped


def _wait(channel: socket.socket, reply_out: int, runner: _Runner) -> bytes | None:
    """Wait for the runner's answer or Main's cancel; then take the call down. The line to
    send Main, as the runner wrote it (Main parses it); ``None`` when Main cancelled."""
    selector = selectors.DefaultSelector()
    selector.register(channel, selectors.EVENT_READ)
    selector.register(reply_out, selectors.EVENT_READ)
    answer = b""
    while True:
        for key, _ in selector.select():
            if key.fileobj is channel:  # a cancel, or Main closed its end
                _take_down(runner)
                os.waitpid(runner.pid, 0)
                return None
            chunk = os.read(reply_out, 1 << 16)
            answer += chunk
            if b"\n" in answer or len(answer) > _ANSWER_MAX:
                _take_down(runner)
                os.waitpid(runner.pid, 0)
                return answer.split(b"\n", 1)[0][:_ANSWER_MAX] + b"\n"
            if not chunk:  # the runner ended without answering
                _, status = os.waitpid(runner.pid, 0)
                _take_down(runner)
                return _line({"type": "died", "exit_code": os.waitstatus_to_exitcode(status)})


def _take_down(runner: _Runner) -> None:
    """Kill every other process of this call; without scoped signals, only the runner."""
    with contextlib.suppress(ProcessLookupError):  # nothing else was left
        if runner.scoped:
            os.kill(-1, signal.SIGKILL)
        else:  # kill(-1) would reach every process of the user
            os.kill(runner.pid, signal.SIGKILL)


def _take_down_and_exit(runner: _Runner) -> None:
    _take_down(runner)
    os._exit(0)


def _run_function(call: _Call, reply_in: int, warden: int) -> None:
    """The runner: import the function from its package, run it in the Carpeta de trabajo and
    write its answer. It never returns."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    _die_with(warden, signal.SIGKILL)
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
