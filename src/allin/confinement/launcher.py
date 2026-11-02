"""Main's side of a Function tool call: one confined process per call (ADR-0005, ADR-0016).

The processes are started by one long-lived thread, the one that will also start the Procesos
adheridos: ``PR_SET_PDEATHSIG`` fires when the thread that started a process ends (ADR-0012).
Each process is born clean (#99): ``subprocess`` with a ``socketpair`` as its only channel, a
new session, ``/dev/null`` as input, its output kept as data, a clean environment and
``python -I``, so nothing is imported from its current folder.
"""

import contextlib
import os
import queue
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from pydantic import JsonValue

from allin.confinement.messages import (
    CallTool,
    Cancel,
    Died,
    Raised,
    Returned,
    Unconfined,
    encode,
    parse_to_main,
)
from allin.confinement.reach import installation, minimum_reach
from allin.core.errors import ConfinementFailed, ToolDied, ToolTimedOut

_OUTPUT_KEPT = 64 * 1024  # of stdout and of stderr, each; the rest is read and dropped
_ANSWER_MAX = 16 * 1024 * 1024
_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class ToolCall:
    """One call to a function: the folder of its package, its module and its name."""

    package: Path
    module: str
    function: str
    arguments: dict[str, JsonValue]
    workspace: Path  # the Session's Carpeta de trabajo: its current folder


@dataclass(frozen=True)
class ToolOutcome:
    """What a call gave back: its value, or the error it raised, and what it printed."""

    value: JsonValue
    error: str | None
    stdout: str
    stderr: str


type _Launch = tuple[list[str], dict[str, str], Path, int, Future[subprocess.Popen[bytes]]]


class ToolLauncher:
    def __init__(self, data_dir: Path, grace: float = 2.0) -> None:
        """``grace`` is how long a cancelled call's warden has to take it down before Main
        kills its process group itself."""
        self._data_dir = data_dir
        self._grace = grace
        self._requests: queue.Queue[_Launch | None] = queue.Queue()
        self._thread = threading.Thread(target=self._serve, name="tool-launcher", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._requests.put(None)
        self._thread.join()

    def run(self, call: ToolCall, timeout: float) -> ToolOutcome:
        """Run ``call`` in its own confined process and wait for its answer. Raises
        ``ToolTimedOut`` after ``timeout`` seconds, ``ToolDied`` if it ended without one and
        ``ConfinementFailed`` if it could not confine itself."""
        tmp = self._data_dir / "tmp" / f"call-{secrets.token_hex(8)}"
        tmp.mkdir(parents=True)
        main_end, tool_end = socket.socketpair()
        try:
            process = self._launch(call.workspace, tmp, tool_end)
            tool_end.close()
            out, err = _Capture(process.stdout), _Capture(process.stderr)
            main_end.sendall(encode(self._message(call, tmp)))
            try:
                line = _read_line(main_end, time.monotonic() + timeout)
            except TimeoutError:
                self._cancel(main_end, process)
                raise ToolTimedOut(seconds=timeout) from None
            self._end(process)
            return _outcome(line, out.text(), err.text())
        finally:
            main_end.close()
            tool_end.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def _message(self, call: ToolCall, tmp: Path) -> CallTool:
        reach = minimum_reach(
            workspace=call.workspace,
            tmp=tmp,
            package=call.package,
            data_dir=self._data_dir,
            installation=installation(),
        )
        return CallTool(
            package=str(call.package),
            module=call.module,
            function=call.function,
            arguments=call.arguments,
            workspace=str(call.workspace),
            read=[str(p) for p in reach.read],
            write=[str(p) for p in reach.write],
        )

    def _launch(
        self, workspace: Path, tmp: Path, channel: socket.socket
    ) -> subprocess.Popen[bytes]:
        argv = [
            sys.executable,
            "-I",
            "-m",
            "allin.confinement.boot",
            str(os.getpid()),
            str(channel.fileno()),
        ]
        env = {"PATH": _PATH, "LANG": "C.UTF-8", "TMPDIR": str(tmp), "HOME": str(workspace)}
        started: Future[subprocess.Popen[bytes]] = Future()
        self._requests.put((argv, env, workspace, channel.fileno(), started))
        return started.result()

    def _serve(self) -> None:
        while (request := self._requests.get()) is not None:
            argv, env, workspace, channel_fd, started = request
            try:
                started.set_result(
                    subprocess.Popen(
                        argv,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env,
                        cwd=workspace,
                        start_new_session=True,
                        close_fds=True,
                        pass_fds=(channel_fd,),
                    )
                )
            except OSError as error:
                started.set_exception(error)

    def _cancel(self, main_end: socket.socket, process: subprocess.Popen[bytes]) -> None:
        """Ask the warden to take the call down; kill its group if it does not in time."""
        with contextlib.suppress(OSError):  # it is already gone
            main_end.sendall(encode(Cancel()))
        self._end(process)

    def _end(self, process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait(self._grace)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)  # the last resort: reaches only its group
            process.wait()


def _read_line(channel: socket.socket, deadline: float) -> bytes:
    """One line from the warden, or what came before it closed; TimeoutError at the deadline."""
    data = b""
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        channel.settimeout(remaining)
        chunk = channel.recv(1 << 16)
        if not chunk or len(data) > _ANSWER_MAX:
            break
        data += chunk
    return data.split(b"\n", 1)[0]


def _outcome(line: bytes, stdout: str, stderr: str) -> ToolOutcome:
    if not line:
        raise ToolDied(exit_code=None, stderr=stderr[-2000:])
    match parse_to_main(line):
        case Returned(value=value):
            return ToolOutcome(value=value, error=None, stdout=stdout, stderr=stderr)
        case Raised(error=error):
            return ToolOutcome(value=None, error=error, stdout=stdout, stderr=stderr)
        case Died(exit_code=code):
            raise ToolDied(exit_code=code, stderr=stderr[-2000:])
        case Unconfined(call=call, errno=errno, reason=reason):
            raise ConfinementFailed(call=call, errno=errno, reason=reason)


class _Capture:
    """Read a pipe to its end in its own thread, keeping the first bytes of it."""

    def __init__(self, pipe: IO[bytes] | None) -> None:
        self._kept = bytearray()
        self._pipe = pipe
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        if self._pipe is None:
            return
        with self._pipe:
            while chunk := os.read(self._pipe.fileno(), 1 << 16):
                room = _OUTPUT_KEPT - len(self._kept)
                if room > 0:
                    self._kept += chunk[:room]

    def text(self) -> str:
        self._thread.join(1.0)
        return self._kept.decode(errors="replace")
