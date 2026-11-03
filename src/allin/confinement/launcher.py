"""Main's side of a Function tool call: one confined process per call (ADR-0005, ADR-0016).

The processes are started by one long-lived thread, the one that will also start the Procesos
adheridos: ``PR_SET_PDEATHSIG`` fires when the thread that started a process ends (ADR-0012).
Each process is born clean (#99): ``subprocess`` with a ``socketpair`` as its only channel, a
new session, ``/dev/null`` as input, its output kept as data, a clean environment and
``python -I``, so nothing is imported from its current folder. It is the Vigilante (``warden``),
which takes the call down; Main kills what is left of its process group after every call.
"""

import contextlib
import itertools
import math
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
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from pydantic import JsonValue

from allin.confinement import landlock
from allin.confinement.messages import (
    Announce,
    Attached,
    AttachFproc,
    CallMethod,
    CallTool,
    Cancel,
    CancelCall,
    CommandResult,
    DescribeModule,
    Detach,
    Died,
    Initiative,
    InitiativeResult,
    Progress,
    Raised,
    Returned,
    RunCommand,
    Stale,
    ToMain,
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
from allin.core.errors import (
    AttachedCallCancelled,
    AttachedChanged,
    AttachedEnded,
    AttachFailed,
    ConfinementFailed,
    MalformedMessage,
    PackageNotSource,
    ToolCancelled,
    ToolDied,
    ToolNotStarted,
    ToolTimedOut,
)
from allin.core.ids import NO_PROMPT_ID, AttachedName, FprocName, PromptId, Revision
from allin.core.sources import compiled_files
from allin.decorators import MINIMUM, Reach

_OUTPUT_KEPT = 64 * 1024  # of stdout and of stderr, each; the rest is read and dropped
_ANSWER_MAX = 16 * 1024 * 1024
_STOP_POLL = 0.1  # how often a waiting call asks whether its Turn was cancelled
# What a call that cannot run Command sessions gets when it asks for one: a description.
_NO_COMMANDS: JsonValue = {"error": "this call cannot run Command sessions"}
_NO_INITIATIVE: JsonValue = {"error": "this Proceso adherido runs Command sessions only in a call"}
_CANCELLED: JsonValue = {"error": str(AttachedCallCancelled())}
_READ_AGAIN = "it changed since your last read: read it again, then write"

# Runs a Command session a tool asked for, as typed (``@name …``), and gives back its result.
type OnCommand = Callable[[str], JsonValue]
_WARDEN = b"allin.confinement.warden"  # in the command line of every warden
_ORPHAN_GRACE = 2.0  # how long a warden a dead Main left gets to take itself down
_STDERR_TOLD = 2000  # the end of stderr a ToolDied carries
_PATH = "/usr/local/bin:/usr/bin:/bin"
# What a graphical client needs from Main's environment, and only with ``display``.
_DISPLAY_ENV = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR")

# ``-I`` leaves PYTHONPATH out, so the warden is told where Main's ``allin`` is: it runs the
# same code, which is the code its confinement lets it read. Already on the path (an
# installed AllIn, in site-packages) it is left where it is, behind the standard library.
_START = (
    "import sys; code = sys.argv.pop(1); "
    "code in sys.path or sys.path.insert(0, code); "
    "from allin.confinement.warden import main; main()"
)


@dataclass(frozen=True)
class ToolCall:
    """One call to a function: the folder of its package, its module and its name."""

    package: Path
    module: str
    function: str
    arguments: dict[str, JsonValue]
    workspace: Path  # the Session's Carpeta de trabajo: its current folder
    reach: Reach = MINIMUM  # as its description read it (#102), never by importing it
    prompt_id: PromptId = NO_PROMPT_ID  # the Prompt whose Turn calls it (#116)


@dataclass(frozen=True)
class FprocStart:
    """What a Proceso adherido starts from: its fproc's package and module, its name, its own
    Carpeta de trabajo, its Alcance as approved, and the shared file chosen for it."""

    package: Path
    module: FprocName
    name: AttachedName
    workspace: Path
    reach: Reach = MINIMUM
    file: Path | None = None  # read and written beyond its Alcance, for this instance alone
    at_once: int | None = 1  # the calls it takes at a time, as ``@concurrent`` says; None: any
    # Its instance's Secretos, by name (#124): only its channel carries them.
    secrets: dict[str, str] = field(default_factory=dict[str, str], repr=False)


@dataclass(frozen=True)
class ToolOutcome:
    """What a call gave back: its value, or the error it raised, and what it printed."""

    value: JsonValue
    error: str | None
    stdout: str
    stderr: str
    revision: Revision | None = None  # a Proceso adherido's, as the call saw it


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
        # Calls and Procesos adheridos that a crashed Main left behind: they died with it.
        for pattern in ("call-*", "attached-*"):
            for left in (self._data_dir / "tmp").glob(pattern):
                shutil.rmtree(left, ignore_errors=True)
        self._thread.start()

    def stop(self) -> None:
        self._requests.put(None)
        self._thread.join()

    def run(
        self,
        call: ToolCall,
        timeout: float,
        stop: Callable[[], bool] = lambda: False,
        on_command: OnCommand | None = None,
    ) -> ToolOutcome:
        """Run ``call`` in its own confined process and wait for its answer. Each Command
        session it asks for goes to ``on_command``, whose result goes back to it; the time that
        takes is not counted in ``timeout`` (ADR-0005). Without ``on_command`` it is refused.
        Raises ``ToolTimedOut`` after ``timeout`` seconds, ``ToolCancelled`` as soon as ``stop``
        says so (it is asked a few times a second), ``ToolDied`` if it ended without one and
        ``ConfinementFailed`` if it could not confine itself, ``ToolNotStarted`` if it could
        not be started and ``PackageNotSource`` if its package holds compiled code."""
        _refuse_compiled(call.package)
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
                stop,
                on_command,
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def describe(self, package: Path, module: str, timeout: float) -> ToolOutcome:
        """Import ``module`` from ``package`` in its own process under the encierro de
        descripción (ADR-0016) and give back what ``describer.describe`` makes of it. That
        process reads only the minimum (the system, AllIn's code and the package), with no
        Carpeta de trabajo, no writing, no network and no unix sockets. Raises as ``run``."""
        _refuse_compiled(package)
        read = description_paths(self._places(package, package, package))
        message = DescribeModule(package=str(package), module=module, read=[str(p) for p in read])
        return self._converse(package, MINIMUM, None, frozenset(), lambda _: message, timeout)

    def attach(
        self,
        start: FprocStart,
        timeout: float,
        on_end: "OnEnd" = lambda _: None,
        release: float = 5.0,
        on_initiative: OnCommand | None = None,
    ) -> "AttachedProcess":
        """Start ``start``'s fproc as a Proceso adherido, confined for good with its Alcance and
        its shared file, and give it back once its class was made (ADR-0012). It dies with this
        launcher's thread, and so with Main; ``on_end`` hears, from another thread, that it
        ended; ``release``: the seconds a call past its timeout gets to end before Main kills
        the whole process; ``on_initiative`` runs, in a thread of its own, each Command session
        it asks between calls (ADR-0026), which without it are refused. Raises
        ``AttachFailed`` if making the class raised, and as ``run`` for the rest."""
        _refuse_compiled(start.package)
        tmp = self._data_dir / "tmp" / f"attached-{secrets.token_hex(8)}"
        tmp.mkdir(parents=True)
        servers = self._server_ports() if start.reach.network else frozenset[int]()
        main_end, tool_end = socket.socketpair()
        try:
            started = self._launch(start.workspace, start.reach, tmp, tool_end, servers)
        except BaseException:
            main_end.close()
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        finally:
            tool_end.close()
        attached = AttachedProcess(
            started.process,
            main_end,
            tmp,
            self._grace,
            on_end,
            start.at_once,
            release,
            on_initiative,
        )
        try:
            attached.send(self._attachment(start, tmp, servers, started.net_layer))
            attached.wait_attached(timeout)
        except BaseException:
            attached.detach()
            raise
        return attached

    def _attachment(
        self, start: FprocStart, tmp: Path, servers: frozenset[int], net_layer: int | None
    ) -> AttachFproc:
        paths = allowed_paths(start.reach, self._places(start.workspace, tmp, start.package))
        shared = [] if start.file is None else [str(start.file)]
        ports = start.reach.tcp_ports(servers)
        return AttachFproc(
            package=str(start.package),
            module=start.module,
            name=start.name,
            file=None if start.file is None else str(start.file),
            workspace=str(start.workspace),
            read=[*map(str, paths.read), *shared],
            write=[*map(str, paths.write), *shared],
            connect=None if ports is None else list(ports),
            net_layer=net_layer,
            ip=start.reach.network,
            unix=start.reach.display,
            secrets=start.secrets,
        )

    def _converse(
        self,
        workspace: Path,
        reach: Reach,
        tmp: Path | None,
        servers: frozenset[int],
        message: Callable[[int | None], CallTool | DescribeModule],
        timeout: float,
        stop: Callable[[], bool] = lambda: False,
        on_command: OnCommand | None = None,
    ) -> ToolOutcome:
        """Start a warden, send it ``message`` (made once the shared layer is known) and wait
        for its answer, taking the call down after ``timeout``; meanwhile, answer each Command
        session it asks for with ``on_command``, off the clock."""
        main_end, tool_end = socket.socketpair()
        try:
            started = self._launch(workspace, reach, tmp, tool_end, servers)
            process = started.process
            tool_end.close()
            out, err = _Capture(process.stdout), _Capture(process.stderr)
            main_end.sendall(encode(message(started.net_layer)))
            try:
                answer = _answer(main_end, time.monotonic() + timeout, stop, on_command)
            except TimeoutError as error:
                self._cancel(main_end, process)
                raise ToolTimedOut(seconds=timeout) from error
            except ToolCancelled:
                self._cancel(main_end, process)
                raise
            self._end(process)
            return _outcome(answer, out.text(), err.text())
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
            prompt_id=call.prompt_id,
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


def _refuse_compiled(package: Path) -> None:
    """The warden imports only the package's sources, but a process it spawns (``multiprocessing``,
    another ``python``) imports as Python does, ``.pyc`` first: compiled code never gets there."""
    if files := compiled_files(package):
        raise PackageNotSource(package=str(package), files=list[JsonValue](files))


def _answer(
    channel: socket.socket,
    deadline: float,
    stop: Callable[[], bool],
    on_command: OnCommand | None,
) -> ToMain | None:
    """The warden's last message, once it has asked every Command session it wanted; ``None``
    if it closed without one. Waiting for Main moves the deadline: it does not count."""
    lines = _Lines(channel)
    while True:
        line = lines.next(deadline, stop)
        if not line:
            return None
        message = parse_to_main(line)
        if not isinstance(message, RunCommand):
            return message
        asked = time.monotonic()
        result = on_command(message.text) if on_command is not None else _NO_COMMANDS
        deadline += time.monotonic() - asked
        channel.sendall(encode(CommandResult(result=result)))


class _Lines:
    """The lines the warden sends on a call's channel, one at a time."""

    def __init__(self, channel: socket.socket) -> None:
        self._channel = channel
        self._data = b""
        self._closed = False

    def next(self, deadline: float, stop: Callable[[], bool]) -> bytes:
        """The next line, or what came before the warden closed (empty once nothing did);
        TimeoutError at the deadline, ``ToolCancelled`` once ``stop`` says so."""
        while b"\n" not in self._data and not self._closed:
            if stop():
                raise ToolCancelled
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            self._channel.settimeout(min(remaining, _STOP_POLL))
            try:
                chunk = self._channel.recv(1 << 16)
            except TimeoutError:
                continue
            if not chunk or len(self._data) > _ANSWER_MAX:
                self._closed = True
                break
            self._data += chunk
        line, _, self._data = self._data.partition(b"\n")
        return line


def _outcome(message: ToMain | None, stdout: str, stderr: str) -> ToolOutcome:
    if message is None or isinstance(
        message, RunCommand | Attached | Stale | Progress | Announce | Initiative
    ):
        raise ToolDied(exit_code=None, stderr=stderr[-_STDERR_TOLD:])
    match message:
        case Returned(value=value):
            return ToolOutcome(value=value, error=None, stdout=stdout, stderr=stderr)
        case Raised(error=error):
            return ToolOutcome(value=None, error=error, stdout=stdout, stderr=stderr)
        case Died(exit_code=code):
            raise ToolDied(exit_code=code, stderr=stderr[-_STDERR_TOLD:])
        case Unconfined(call=call, errno=errno):
            raise ConfinementFailed(call=call, errno=errno)


# Hears that a Proceso adherido ended, from the thread that reaped it.
type OnEnd = Callable[["AttachedProcess"], None]

# What a call hears of the method while it waits for it: a line of progress.
type OnProgress = Callable[[str], None]
# Something a method announced, for the Sessions subscribed to its process (#116).
type OnAnnounce = Callable[[str], None]


class _Turns:
    """The calls a Proceso adherido runs at once: ``at_once`` (``None``: any), the rest held
    back in the order they came (ADR-0012)."""

    def __init__(self, at_once: int | None) -> None:
        self._at_once = at_once
        self._changed = threading.Condition()
        self._waiting: deque[object] = deque()
        self._running = 0
        self._closed = False

    def take(self, stop: Callable[[], bool]) -> bool:
        """Wait for this call's turn; ``False`` if the process ended first. Raises
        ``ToolCancelled`` once ``stop`` says so."""
        ticket = object()
        with self._changed:
            self._waiting.append(ticket)
            while not self._is_turn(ticket):
                if self._closed or stop():
                    self._waiting.remove(ticket)
                    self._changed.notify_all()
                    if self._closed:
                        return False
                    raise ToolCancelled
                self._changed.wait(_STOP_POLL)
            self._waiting.popleft()
            self._running += 1
            self._changed.notify_all()
            return True

    def give_back(self) -> None:
        with self._changed:
            self._running -= 1
            self._changed.notify_all()

    def close(self) -> None:
        with self._changed:
            self._closed = True
            self._changed.notify_all()

    def _is_turn(self, ticket: object) -> bool:
        return self._waiting[0] is ticket and (
            self._at_once is None or self._running < self._at_once
        )


@dataclass(frozen=True)
class _Hooks:
    """Where what a call asks or tells goes while Main waits for its answer."""

    on_command: OnCommand | None
    on_progress: OnProgress | None
    on_announce: OnAnnounce | None


@dataclass
class _Pending:
    """A call sent to the process whose answer has not come."""

    id: int
    method: str
    inbox: queue.Queue[ToMain | None] = field(default_factory=queue.Queue[ToMain | None])
    waiting: bool = True  # its caller still waits for it
    kill_at: float = math.inf  # once nobody waits: when Main kills a process that still runs it


class AttachedProcess:
    """A Proceso adherido as Main holds it: its process, and the channel its methods are called
    on, each call with its id (ADR-0012). It is safe to use from any thread."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        channel: socket.socket,
        tmp: Path,
        grace: float,
        on_end: OnEnd,
        at_once: int | None = 1,
        release: float = 5.0,
        on_initiative: OnCommand | None = None,
    ) -> None:
        """``at_once``: the calls it takes at a time (``None``: any); ``release``: the seconds
        a call past its timeout gets to end before Main kills the whole process;
        ``on_initiative``: what runs the Command sessions it asks between calls."""
        self._process = process
        self._on_initiative = on_initiative
        self._channel = channel
        self._tmp = tmp
        self._grace = grace
        self._on_end = on_end
        self._release = release
        self._lines = _Lines(channel)
        self._sending = threading.Lock()  # whole lines: a detach never cuts a call in two
        self._turns = _Turns(at_once)
        self._pending: dict[int, _Pending] = {}
        self._state = threading.Lock()  # over ``_pending`` and ``_closed``
        self._closed = False
        self._ids = itertools.count(1)
        self._stderr = _Capture(process.stderr, tail=True)
        self._stdout = _Capture(process.stdout)
        self._ended = threading.Event()
        self._exit_code: int | None = None  # its warden's
        self._fproc_died: int | None = None  # its fproc's process's, as its warden told it
        self._hung: str | None = None
        self._reaping = threading.Lock()  # a kill never comes after its reaping
        self._outbox: queue.Queue[CommandResult | InitiativeResult | None] = (
            queue.Queue()
        )  # the reader's sends
        self._reader: threading.Thread | None = None  # once its class was made
        self._reaper = threading.Thread(target=self._reap, name="attached-reaper", daemon=True)
        self._reaper.start()

    @property
    def pid(self) -> int:
        """Its warden's: the leader of its process group."""
        return self._process.pid

    @property
    def stderr(self) -> str:
        """The end of what it wrote to its standard error."""
        return self._stderr.text()[-_STDERR_TOLD:]

    @property
    def hung(self) -> str | None:
        """Why Main killed it, if it did: a call that did not end after its timeout, or a line
        Main does not read."""
        return self._hung

    def wait(self) -> int | None:
        """Block until it ended, by itself or detached; its exit code (negative for a signal):
        its fproc's process's if that one ended first, as when it crashes."""
        self._ended.wait()
        return self._fproc_died if self._fproc_died is not None else self._exit_code

    def send(
        self,
        message: AttachFproc | CallMethod | CancelCall | CommandResult | Detach | InitiativeResult,
    ) -> None:
        """Write one message whole on its channel. Raises ``OSError`` once it is closed."""
        with self._sending:
            self._channel.sendall(encode(message))

    def call(
        self,
        method: str,
        arguments: dict[str, JsonValue],
        timeout: float,
        on_command: OnCommand | None = None,
        stop: Callable[[], bool] = lambda: False,
        on_progress: OnProgress | None = None,
        seen: Revision | None = None,
        *,
        on_announce: OnAnnounce | None = None,
        prompt_id: PromptId = NO_PROMPT_ID,
    ) -> ToolOutcome:
        """Call a public method of its fproc and wait for its answer, once it is this call's
        turn. Each Command session the method asks for goes to ``on_command``, each line of
        progress to ``on_progress`` and each thing it announces to ``on_announce``; the timeout
        counts from the last progress, and not while Main runs a Command session. ``seen``: the
        revision its Session saw last, which an ``@writes`` method checks; ``None`` for a
        human's call, never checked. ``prompt_id``: the Prompt whose Turn calls it.

        Raises ``AttachedEnded`` if the process ended, before or during the call;
        ``AttachedChanged`` for an ``@writes`` its Session must read again before;
        ``ToolTimedOut`` past the timeout, and ``ToolCancelled`` once ``stop`` says so, both
        telling the method to leave it: if it does not end within its timeout and ``release``
        seconds more, Main kills the whole process."""
        if not self._turns.take(stop):
            raise AttachedEnded(exit_code=self.wait())
        pending = _Pending(next(self._ids), method)
        with self._state:
            closed = self._closed
            if not closed:
                self._pending[pending.id] = pending
        if closed:
            raise AttachedEnded(exit_code=self.wait())
        try:
            self.send(
                CallMethod(
                    id=pending.id,
                    method=method,
                    arguments=arguments,
                    seen=seen,
                    prompt_id=prompt_id,
                )
            )
        except OSError:
            pending.inbox.put(None)  # its reader ends it with the rest
        hooks = _Hooks(on_command, on_progress, on_announce)
        return self._wait_answer(pending, timeout, stop, hooks)

    def _wait_answer(
        self,
        pending: _Pending,
        timeout: float,
        stop: Callable[[], bool],
        hooks: _Hooks,
    ) -> ToolOutcome:
        on_command, on_progress = hooks.on_command, hooks.on_progress
        deadline = time.monotonic() + timeout
        while True:
            if stop():
                self._give_up(pending, deadline)
                raise ToolCancelled
            now = time.monotonic()
            if now >= deadline:
                self._give_up(pending, now)
                raise ToolTimedOut(seconds=timeout)
            try:
                message = pending.inbox.get(timeout=min(deadline - now, _STOP_POLL))
            except queue.Empty:
                continue
            match message:
                case Progress(text=text):
                    deadline = time.monotonic() + timeout
                    if on_progress is not None:
                        on_progress(text)
                case Announce(text=text):
                    if hooks.on_announce is not None:
                        hooks.on_announce(text)
                case RunCommand(text=text):
                    asked = time.monotonic()
                    try:
                        result = on_command(text) if on_command is not None else _NO_COMMANDS
                    except ToolCancelled:  # the method still waits for it
                        self._answer_command(pending.id, _CANCELLED)
                        self._give_up(pending, deadline + time.monotonic() - asked)
                        raise
                    deadline += time.monotonic() - asked
                    self._answer_command(pending.id, result)
                case Returned(value=value, revision=revision):
                    self._done(pending)
                    return ToolOutcome(value, None, "", "", _revision(revision))
                case Raised(error=error, revision=revision):
                    self._done(pending)
                    return ToolOutcome(None, error, "", "", _revision(revision))
                case Stale(revision=revision):
                    self._done(pending)
                    raise AttachedChanged(revision=revision, advice=_READ_AGAIN)
                case _:  # it ended
                    raise AttachedEnded(exit_code=self.wait())

    def _answer_command(self, call_id: int, result: JsonValue) -> None:
        with contextlib.suppress(OSError):  # it ended: the call hears it from its reader
            self.send(CommandResult(id=call_id, result=result))

    def _give_up(self, pending: _Pending, deadline: float) -> None:
        """Nobody waits for ``pending`` any more: tell the method to leave it, and kill the
        process if it still runs it ``release`` seconds after ``deadline``. Nothing is sent
        under ``_state``, which the reader needs to go on reading."""
        with contextlib.suppress(OSError):
            self.send(CancelCall(id=pending.id))
        refused = 0
        with self._state:
            pending.waiting = False
            pending.kill_at = deadline + self._release
            while not pending.inbox.empty():
                refused += self._unwaited(pending, pending.inbox.get_nowait())
        for _ in range(refused):
            self._answer_command(pending.id, _CANCELLED)

    def _done(self, pending: _Pending) -> None:
        with self._state:
            if self._pending.pop(pending.id, None) is None:
                return  # the process ended meanwhile: its turns are closed
        self._turns.give_back()

    def _unwaited(self, pending: _Pending, message: ToMain | None) -> int:
        """Under ``_state``: what comes for a call nobody waits for. Its answer gives its turn
        back; how many Command sessions it asked, which do not run (1 or 0), is for the caller
        to answer once out of ``_state``."""
        match message:
            case RunCommand():
                return 1
            case Returned() | Raised() | Stale():
                if self._pending.pop(pending.id, None) is not None:
                    self._turns.give_back()
            case _:
                pass
        return 0

    def wait_attached(self, timeout: float) -> None:
        """Wait for its class to be made, then start reading its answers. Raises
        ``AttachFailed`` if that raised, ``ConfinementFailed``, ``ToolDied`` if it ended first
        and ``ToolTimedOut``."""
        try:
            line = self._lines.next(time.monotonic() + timeout, lambda: False)
        except TimeoutError as error:
            raise ToolTimedOut(seconds=timeout) from error
        message = parse_to_main(line) if line else None
        match message:
            case Attached():
                threading.Thread(target=self._write, name="attached-writer", daemon=True).start()
                self._reader = threading.Thread(
                    target=self._read, name="attached-reader", daemon=True
                )
                self._reader.start()
            case Raised(error=error):
                raise AttachFailed(error=error)
            case Unconfined(call=call, errno=errno):
                raise ConfinementFailed(call=call, errno=errno)
            case Died(exit_code=code):
                self._fproc_died = code
                raise ToolDied(exit_code=code, stderr=self.stderr)
            case _:
                raise ToolDied(exit_code=self.wait(), stderr=self.stderr)

    def _read(self) -> None:
        """Its one reader: pass each answer to the call it belongs to, until the channel ends;
        meanwhile, kill it if a call nobody waits for runs past its time."""
        while True:
            try:
                line = self._lines.next(time.monotonic() + _STOP_POLL, lambda: False)
            except TimeoutError:
                self._watch()
                continue
            except OSError:
                break
            if not line:
                break
            try:
                message = parse_to_main(line)
            except MalformedMessage:
                self._kill("it sent Main a line it does not read")
                continue
            self._route(message)
            self._watch()  # however busy the channel
        with self._state:
            self._closed = True
            for pending in self._pending.values():
                pending.inbox.put(None)
            self._pending.clear()
        self._turns.close()
        self._outbox.put(None)

    def _write(self) -> None:
        """Its reader's sends, so that the reader never waits on one: until it ends."""
        while (message := self._outbox.get()) is not None:
            with contextlib.suppress(OSError):  # it ended
                self.send(message)

    def _route(self, message: ToMain) -> None:
        match message:
            case Died(exit_code=code):
                self._fproc_died = code
                return
            case Initiative(id=initiative_id, text=text):
                threading.Thread(
                    target=self._initiative,
                    args=(initiative_id, text),
                    name=f"initiative-{initiative_id}",
                    daemon=True,
                ).start()
                return
            case Returned(id=int() as call_id) | Raised(id=int() as call_id):
                pass
            case Stale(id=call_id) | Progress(id=call_id) | Announce(id=call_id):
                pass
            case RunCommand(id=int() as call_id):
                pass
            case _:
                self._kill("it sent Main a line it does not read")
                return
        refused = 0
        with self._state:
            pending = self._pending.get(call_id)
            if pending is None:  # nobody asked it
                refused = 1 if isinstance(message, RunCommand) else 0
            elif pending.waiting:
                pending.inbox.put(message)
            else:
                refused = self._unwaited(pending, message)
        if refused:  # the reader never waits on a send: the writer does it
            self._outbox.put(CommandResult(id=call_id, result=_CANCELLED))

    def _initiative(self, initiative_id: int, text: str) -> None:
        """A Command session it asked on its own, run where it waits for nobody else's."""
        on_initiative = self._on_initiative
        result = on_initiative(text) if on_initiative is not None else _NO_INITIATIVE
        self._outbox.put(InitiativeResult(id=initiative_id, result=result))

    def _watch(self) -> None:
        now = time.monotonic()
        with self._state:
            hung = [p for p in self._pending.values() if not p.waiting and p.kill_at <= now]
        if hung:
            self._kill(f"its call to {hung[0].method} did not end after its timeout")

    def _kill(self, why: str) -> None:
        """Take it down as a crash: its reaper tells who listens. Never once it was reaped,
        when its group's id may be another's."""
        with self._reaping:
            if self._exit_code is not None:
                return
            if self._hung is None:
                self._hung = why
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._process.pid, signal.SIGKILL)

    def detach(self) -> None:
        """Take it down: its warden ends it with every process it started, and what is left
        of its process group after ``grace`` is killed. Waits until it is gone."""
        with contextlib.suppress(OSError):  # it is already gone
            self.send(Detach())
        self._ended.wait(self._grace)
        if not self._ended.is_set():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._process.pid, signal.SIGKILL)
        self._ended.wait()

    def _reap(self) -> None:
        """Its one reaper: once the warden ended, kill what is left of its group (before the
        warden is reaped, so the group's id cannot be reused), let its reader end, close its
        channel, clear its TMPDIR and tell who listens."""
        pidfd = os.pidfd_open(self._process.pid)
        try:
            select.select([pidfd], [], [])  # readable once the warden has ended
        finally:
            os.close(pidfd)
        with self._reaping:
            with contextlib.suppress(ProcessLookupError):  # nothing else was left
                os.killpg(self._process.pid, signal.SIGKILL)
            self._exit_code = self._process.wait()
        if self._reader is not None:
            self._reader.join()  # it reads to the end, how its fproc ended too
        else:
            self._read_death()
            self._turns.close()
        with self._sending:
            self._channel.close()
        shutil.rmtree(self._tmp, ignore_errors=True)
        self._ended.set()
        self._on_end(self)

    def _read_death(self) -> None:
        """What its warden said as it ended before its class was made: how its fproc's process
        ended."""
        try:
            line = self._lines.next(time.monotonic() + _STOP_POLL, lambda: False)
            if line and isinstance(died := parse_to_main(line), Died):
                self._fproc_died = died.exit_code
        except (TimeoutError, OSError, MalformedMessage):
            pass  # it said nothing, or nothing Main reads


def _revision(seen: int | None) -> Revision | None:
    return None if seen is None else Revision(seen)


def close_left_warden(pid: int, main_pid: int) -> bool:
    """Close the warden of a Proceso adherido that the dead Main ``main_pid`` left alive, with
    every process of its group; whether there was one. Since that Main died, ``pid`` may belong
    to an unrelated process now: only one whose command line is a warden of that Main is
    touched."""
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:  # gone
        return False
    if not any(_WARDEN in arg for arg in args) or str(main_pid).encode() not in args:
        return False
    try:
        os.kill(pid, signal.SIGTERM)  # its warden takes down what it started
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + _ORPHAN_GRACE
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):  # what is left of its group
        os.killpg(pid, signal.SIGKILL)
    return True


def _alive(pid: int) -> bool:
    """Whether ``pid`` is a live process; a zombie has already exited."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rpartition(")")[2].split()[0] != "Z"


class _Capture:
    """Read a pipe to its end in its own thread, keeping its first bytes, or its last ones."""

    def __init__(self, pipe: IO[bytes] | None, *, tail: bool = False) -> None:
        self._kept = bytearray()
        self._tail = tail
        self._pipe = pipe
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        if self._pipe is None:
            return
        with self._pipe:
            while chunk := os.read(self._pipe.fileno(), 1 << 16):
                if self._tail:
                    self._kept = (self._kept + chunk)[-_OUTPUT_KEPT:]
                    continue
                room = _OUTPUT_KEPT - len(self._kept)
                if room > 0:
                    self._kept += chunk[:room]

    def text(self) -> str:
        """What it kept; for a tail, what it kept so far, without waiting for the end."""
        if not self._tail:
            self._thread.join(1.0)
        return bytes(self._kept).decode(errors="replace")
