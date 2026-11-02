"""Main's side of a Function tool call: one confined process per call (ADR-0005, ADR-0016).

The processes are started by one long-lived thread, the one that will also start the Procesos
adheridos: ``PR_SET_PDEATHSIG`` fires when the thread that started a process ends (ADR-0012).
Each process is born clean (#99): ``subprocess`` with a ``socketpair`` as its only channel, a
new session, ``/dev/null`` as input, its output kept as data, a clean environment and
``python -I``, so nothing is imported from its current folder. It is the Vigilante (``warden``),
which takes the call down; Main kills what is left of its process group after every call.
"""

import contextlib
import os
import queue
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from pydantic import JsonValue

from allin.confinement import landlock
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
from allin.confinement.reach import (
    Places,
    allowed_paths,
    code_folder,
    description_paths,
    install_root,
    installation,
    resolver_folder,
    x11_cookie,
)
from allin.core.errors import ConfinementFailed, ToolDied, ToolNotStarted, ToolTimedOut
from allin.decorators import MINIMUM, Reach

_OUTPUT_KEPT = 64 * 1024  # of stdout and of stderr, each; the rest is read and dropped
_ANSWER_MAX = 16 * 1024 * 1024
_STDERR_TOLD = 2000  # the end of stderr a ToolDied carries
_PATH = "/usr/local/bin:/usr/bin:/bin"
# What a graphical client needs from Main's environment, and only with ``display``.
# ``-I`` leaves PYTHONPATH out, so the warden is told where Main's ``allin`` is: it runs the
# same code, which is the code its confinement lets it read.
_START = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from allin.confinement.warden import main; main()"
)
_DISPLAY_ENV = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR")


@dataclass(frozen=True)
class ToolCall:
    """One call to a function: the folder of its package, its module and its name."""

    package: Path
    module: str
    function: str
    arguments: dict[str, JsonValue]
    workspace: Path  # the Session's Carpeta de trabajo: its current folder
    reach: Reach = MINIMUM  # as its description read it (#102), never by importing it


@dataclass(frozen=True)
class ToolOutcome:
    """What a call gave back: its value, or the error it raised, and what it printed."""

    value: JsonValue
    error: str | None
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _SharedLayer:
    """net=True's Landlock layer: every TCP port but ``forbidden``, the Servers'."""

    forbidden: frozenset[int]
    descriptor: int | None  # None on a kernel without network rules

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)


@dataclass(frozen=True)
class _Started:
    process: subprocess.Popen[bytes]
    net_layer: int | None  # the shared layer's descriptor, as the process got it


@dataclass(frozen=True)
class _Launch:
    """What the launcher thread needs to start one call's process, and where to hand it."""

    argv: list[str]
    env: dict[str, str]
    workspace: Path
    channel_fd: int
    forbidden: frozenset[int] | None  # with net=True, the ports its shared layer leaves out
    started: Future[_Started]


class ToolLauncher:
    """The Lanzador de tools: Main's long-lived thread that starts every call's process."""

    def __init__(
        self,
        data_dir: Path,
        server_ports: Callable[[], frozenset[int]] = frozenset,
        grace: float = 2.0,
    ) -> None:
        """``server_ports`` are the ports of AllIn's Servers, the observed one included, which
        no Alcance reaches (ADR-0016); asked at every call. ``grace`` is how long a call's
        warden has to take it down before Main kills its process group itself."""
        self._data_dir = data_dir
        self._server_ports = server_ports
        self._grace = grace
        self._requests: queue.Queue[_Launch | None] = queue.Queue()
        self._thread = threading.Thread(target=self._serve, name="tool-launcher", daemon=True)
        self._layer: _SharedLayer | None = None  # the launcher thread's alone

    def start(self) -> None:
        # Calls that a crashed Main left behind: no process of theirs is alive any more.
        for left in (self._data_dir / "tmp").glob("call-*"):
            shutil.rmtree(left, ignore_errors=True)
        self._thread.start()

    def stop(self) -> None:
        self._requests.put(None)
        self._thread.join()

    def run(self, call: ToolCall, timeout: float) -> ToolOutcome:
        """Run ``call`` in its own confined process and wait for its answer. Raises
        ``ToolTimedOut`` after ``timeout`` seconds, ``ToolDied`` if it ended without one and
        ``ConfinementFailed`` if it could not confine itself and ``ToolNotStarted`` if it could
        not be started."""
        tmp = self._data_dir / "tmp" / f"call-{secrets.token_hex(8)}"
        tmp.mkdir(parents=True)
        servers = self._server_ports() if call.reach.network else frozenset[int]()
        try:
            return self._converse(
                call.workspace,
                call.reach,
                tmp,
                servers,
                lambda net_layer: self._message(call, tmp, servers, net_layer),
                timeout,
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def describe(self, package: Path, module: str, timeout: float) -> ToolOutcome:
        """Import ``module`` from ``package`` in its own process under the encierro de
        descripción (ADR-0016) and give back what ``describer.describe`` makes of it. That
        process reads only the minimum (the system, AllIn's code and the package), with no
        Carpeta de trabajo, no writing, no network and no unix sockets. Raises as ``run``."""
        message = CallTool(
            package=str(package),
            module=module,
            function="",
            arguments={},
            workspace=str(package),
            read=[str(p) for p in description_paths(self._places(package, package, package))],
            write=[],
            connect=[],
            net_layer=None,
            ip=False,
            unix=False,
            describe=True,
        )
        return self._converse(package, MINIMUM, None, frozenset(), lambda _: message, timeout)

    def _converse(
        self,
        workspace: Path,
        reach: Reach,
        tmp: Path | None,
        servers: frozenset[int],
        message: Callable[[int | None], CallTool],
        timeout: float,
    ) -> ToolOutcome:
        """Start a warden, send it ``message`` (made once the shared layer is known) and wait
        for its answer, taking the call down after ``timeout``."""
        main_end, tool_end = socket.socketpair()
        try:
            started = self._launch(workspace, reach, tmp, tool_end, servers)
            process = started.process
            tool_end.close()
            out, err = _Capture(process.stdout), _Capture(process.stderr)
            main_end.sendall(encode(message(started.net_layer)))
            try:
                line = _read_line(main_end, time.monotonic() + timeout)
            except TimeoutError as error:
                self._cancel(main_end, process)
                raise ToolTimedOut(seconds=timeout) from error
            self._end(process)
            return _outcome(line, out.text(), err.text())
        finally:
            main_end.close()
            tool_end.close()

    def _places(self, workspace: Path, tmp: Path, package: Path) -> Places:
        return Places(
            workspace=workspace,
            tmp=tmp,
            package=package,
            data_dir=self._data_dir,
            installation=installation(),
            install_root=install_root(),
            home=Path.home(),
            resolver=resolver_folder(),
            xauthority=x11_cookie(),
        )

    def _message(
        self, call: ToolCall, tmp: Path, servers: frozenset[int], net_layer: int | None
    ) -> CallTool:
        paths = allowed_paths(call.reach, self._places(call.workspace, tmp, call.package))
        ports = call.reach.tcp_ports(servers)
        return CallTool(
            package=str(call.package),
            module=call.module,
            function=call.function,
            arguments=call.arguments,
            workspace=str(call.workspace),
            read=[str(p) for p in paths.read],
            write=[str(p) for p in paths.write],
            connect=None if ports is None else list(ports),
            net_layer=net_layer,
            ip=call.reach.network,
            unix=call.reach.display,
            describe=False,
        )

    def _launch(
        self,
        workspace: Path,
        reach: Reach,
        tmp: Path | None,
        channel: socket.socket,
        servers: frozenset[int],
    ) -> _Started:
        argv = [sys.executable, "-I", "-c", _START, str(code_folder()), str(os.getpid())]
        argv.append(str(channel.fileno()))
        env = {"PATH": _PATH, "LANG": "C.UTF-8", "HOME": str(workspace)}
        if tmp is not None:
            env["TMPDIR"] = str(tmp)
        if reach.display:
            env |= {name: os.environ[name] for name in _DISPLAY_ENV if name in os.environ}
        started: Future[_Started] = Future()
        if not self._thread.is_alive():
            raise ToolNotStarted(cause="stopped")
        forbidden = servers if reach.tcp_ports(servers) is None else None
        self._requests.put(_Launch(argv, env, workspace, channel.fileno(), forbidden, started))
        return started.result()

    def _serve(self) -> None:
        while (request := self._requests.get()) is not None:
            try:
                layer = None if request.forbidden is None else self._net_layer(request.forbidden)
                process = subprocess.Popen(
                    request.argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=request.env,
                    cwd=request.workspace,
                    start_new_session=True,
                    close_fds=True,
                    pass_fds=(request.channel_fd, *(() if layer is None else (layer,))),
                )
                request.started.set_result(_Started(process, layer))
            # This thread must outlive any call it fails to start: the error goes to that call,
            # which raises it.
            except Exception as error:  # noqa: BLE001 - see above
                request.started.set_exception(ToolNotStarted(cause=type(error).__name__))
        while not self._requests.empty():  # asked for while stopping: they will never start
            if (late := self._requests.get_nowait()) is not None:
                late.started.set_exception(ToolNotStarted(cause="stopped"))
        if self._layer is not None:
            self._layer.close()

    def _net_layer(self, forbidden: frozenset[int]) -> int | None:
        """net=True's shared layer, rebuilt only when the Servers' ports change (#99). Each
        process got its own copy at its start, so the old one can be closed."""
        if self._layer is None or self._layer.forbidden != forbidden:
            if self._layer is not None:
                self._layer.close()
            self._layer = _SharedLayer(forbidden, landlock.port_layer(forbidden))
        return self._layer.descriptor

    def _cancel(self, main_end: socket.socket, process: subprocess.Popen[bytes]) -> None:
        """Ask the warden to take the call down; kill its group if it does not in time."""
        with contextlib.suppress(OSError):  # it is already gone
            main_end.sendall(encode(Cancel()))
        self._end(process)

    def _end(self, process: subprocess.Popen[bytes]) -> None:
        """Give the warden ``grace`` to end, then kill what is left of its process group: all of
        it if the warden did not answer, and without scoped signals (ABI < 6), the descendants
        it could not reach. The group is killed before the warden is reaped, so its id cannot
        have been reused."""
        pidfd = os.pidfd_open(process.pid)
        try:
            select.select([pidfd], [], [], self._grace)  # readable once the warden has ended
        finally:
            os.close(pidfd)
        with contextlib.suppress(ProcessLookupError):  # nothing of the call was left
            os.killpg(process.pid, signal.SIGKILL)
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
        raise ToolDied(exit_code=None, stderr=stderr[-_STDERR_TOLD:])
    match parse_to_main(line):
        case Returned(value=value):
            return ToolOutcome(value=value, error=None, stdout=stdout, stderr=stderr)
        case Raised(error=error):
            return ToolOutcome(value=None, error=error, stdout=stdout, stderr=stderr)
        case Died(exit_code=code):
            raise ToolDied(exit_code=code, stderr=stderr[-_STDERR_TOLD:])
        case Unconfined(call=call, errno=errno):
            raise ConfinementFailed(call=call, errno=errno)


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
