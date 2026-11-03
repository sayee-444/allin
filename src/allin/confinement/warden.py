"""The Vigilante (warden): the first code a Function tool's process runs (ADR-0005, ADR-0016).

Main starts it as ``python -I -c <start> <allin's folder> <main pid> <channel fd>``, in a new
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
import dataclasses
import importlib
import importlib.machinery
import json
import multiprocessing
import os
import selectors
import signal
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, FunctionType, ModuleType
from typing import IO, cast

from allin.confinement import landlock, seccomp
from allin.core.errors import ConfinementFailed

_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36
_ANSWER_MAX = 16 * 1024 * 1024  # as Main's side: past it, the answer is not one
_COMMAND_MAX = 64 * 1024  # as ``messages.COMMAND_MAX``: a longer Command session is refused
_libc = ctypes.CDLL(None, use_errno=True)

type _Json = bool | int | float | str | list["_Json"] | dict[str, "_Json"] | None


@dataclass(frozen=True)
class _Fproc:
    """What ``messages.AttachFproc`` adds to a call: the Proceso adherido's name and file."""

    name: str
    file: str | None


@dataclass(frozen=True)
class _Call:
    """``messages.CallTool``, ``messages.DescribeModule`` or ``messages.AttachFproc``, as Main
    wrote it: a description has no ``function``, runs in its package and gets nothing but
    reading; an attachment has no ``function`` either, but its ``fproc``."""

    package: str
    module: str
    function: str | None  # None: describe the module, or attach its fproc
    arguments: dict[str, _Json]
    workspace: str
    read: tuple[Path, ...]
    write: tuple[Path, ...]
    connect: tuple[int, ...] | None
    net_layer: int | None
    ip: bool
    unix: bool
    fproc: _Fproc | None = None  # a Proceso adherido's: it serves its methods until detached


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
    if call.fproc is not None:
        _attach(call, channel, warden, scoped)
    reply_out, reply_in = os.pipe()
    asks_out, asks_in = os.pipe()  # the runner's Command sessions, to relay to Main
    results_out, results_in = os.pipe()  # Main's results, for the runner
    pid = os.fork()
    if pid == 0:
        for end in (reply_out, asks_out, results_in):
            os.close(end)
        channel.close()
        if call.function is not None:  # a description runs none
            from allin import channel as commands  # noqa: PLC0415 - the runner's alone

            commands.connect(asks_in, results_out)
        _run_function(call, reply_in, warden)
    for end in (reply_in, asks_in, results_out):
        os.close(end)
    runner = _Runner(pid=pid, scoped=scoped)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: _take_down_and_exit(runner))
    reply = _wait(channel, reply_out, runner, _Relay(asks_out, results_in))
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
    match fields.get("type"):
        case "call":
            return _parse_tool_call(fields)
        case "describe":
            return _parse_description(fields)
        case "attach":
            return _parse_attachment(fields)
        case _:
            return None


def _parse_description(fields: dict[object, object]) -> _Call | None:
    package, module, read = fields.get("package"), fields.get("module"), _paths(fields.get("read"))
    if not (isinstance(package, str) and isinstance(module, str) and read is not None):
        return None
    return _Call(
        package=package,
        module=module,
        function=None,
        arguments={},
        workspace=package,
        read=read,
        write=(),
        connect=(),
        net_layer=None,
        ip=False,
        unix=False,
    )


def _parse_tool_call(fields: dict[object, object]) -> _Call | None:
    package, module, function, workspace = (
        fields.get(key) for key in ("package", "module", "function", "workspace")
    )
    arguments = fields.get("arguments")
    read, write = _paths(fields.get("read")), _paths(fields.get("write"))
    connect, net_layer = fields.get("connect"), fields.get("net_layer")
    ports = None if connect is None else _ports(connect)
    ip, unix = fields.get("ip"), fields.get("unix")
    if not (
        isinstance(package, str)
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


def _parse_attachment(fields: dict[object, object]) -> _Call | None:
    """An attachment is a tool call without a function or arguments, plus its fproc."""
    name, file = fields.get("name"), fields.get("file")
    if not (isinstance(name, str) and (file is None or isinstance(file, str))):
        return None
    call = _parse_tool_call({**fields, "function": "", "arguments": {}})
    if call is None:
        return None
    return dataclasses.replace(call, function=None, fproc=_Fproc(name, file))


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
    # With display, X11's abstract socket: libxcb gives up on EPERM instead of trying the
    # socket's path, and display already reaches every unix socket of the session (#101).
    restricted = landlock.restrict_self(call.read, call.write, call.connect, call.unix)
    if call.net_layer is not None:
        landlock.apply_layer(call.net_layer)
        os.close(call.net_layer)
    seccomp.apply_filter(ip=call.ip, unix=call.unix)
    return restricted.signals_scoped


@dataclass
class _Relay:
    """The runner's Command sessions on their way to Main, and Main's results on their way
    back; each side's partial line until it ends."""

    asks_out: int
    results_in: int
    asked: bytes = b""
    from_main: bytes = b""

    def ask(self, channel: socket.socket) -> bool:
        """Pass on to Main the runner's whole Command sessions; ``False`` once it closed."""
        chunk = os.read(self.asks_out, 1 << 16)
        if not chunk:
            return False
        self.asked += chunk
        while b"\n" in self.asked:
            line, _, self.asked = self.asked.partition(b"\n")
            text = _command_text(line)
            if text is None:  # never relayed: Main gets only what this checked
                _write_all(self.results_in, _line({"result": {"error": "malformed command"}}))
            else:
                channel.sendall(_line({"type": "command", "text": text}))
        return len(self.asked) <= _COMMAND_MAX * 4  # past it, no line end in sight: not one

    def answer(self, chunk: bytes) -> bool:
        """Pass on to the runner Main's whole results; ``False`` at anything else from Main,
        which is a cancel."""
        self.from_main += chunk
        while b"\n" in self.from_main:
            line, _, self.from_main = self.from_main.partition(b"\n")
            result = _command_result(line)
            if result is None:
                return False
            _write_all(self.results_in, result)
        return True


def _typed(line: bytes, kind: str) -> dict[str, _Json] | None:
    """The fields of a JSON line whose ``type`` is ``kind``; ``None`` for anything else."""
    try:
        message: object = json.loads(line)
    except ValueError:
        return None
    if not isinstance(message, dict):
        return None
    fields = cast(dict[str, _Json], message)
    return fields if fields.get("type") == kind else None


def _command_text(line: bytes) -> str | None:
    """The text of a Command session the runner wrote, if that is what it wrote."""
    fields = _typed(line, "command")
    text = fields.get("text") if fields is not None else None
    if not isinstance(text, str) or len(text) > _COMMAND_MAX:
        return None
    return text


def _command_result(line: bytes) -> bytes | None:
    """A result from Main, as the runner reads it; ``None`` for anything else."""
    fields = _typed(line, "command_result")
    return _line({"result": fields.get("result")}) if fields is not None else None


def _write_all(fd: int, data: bytes) -> None:
    sent = 0
    while sent < len(data):
        sent += os.write(fd, data[sent:])


def _wait(channel: socket.socket, reply_out: int, runner: _Runner, relay: _Relay) -> bytes | None:
    """Wait for the runner's answer or Main's cancel, relaying its Command sessions and their
    results meanwhile; then take the call down. The line to send Main, as the runner wrote it
    (Main parses it); ``None`` when Main cancelled."""
    selector = selectors.DefaultSelector()
    selector.register(channel, selectors.EVENT_READ)
    selector.register(reply_out, selectors.EVENT_READ)
    selector.register(relay.asks_out, selectors.EVENT_READ)
    answer = b""
    while True:
        for key, _ in selector.select():
            if key.fileobj is relay.asks_out:
                if not relay.ask(channel):
                    selector.unregister(relay.asks_out)
                continue
            if key.fileobj is channel:  # a result, a cancel, or Main closed its end
                chunk = channel.recv(1 << 16)
                if chunk and relay.answer(chunk):
                    continue
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


def _prepare_runner(call: _Call, warden: int) -> None:
    """What a runner does before it imports its module: die with the warden, stand in the
    Carpeta de trabajo, and import only the package's sources after everything else."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    _die_with(warden, signal.SIGKILL)
    os.chdir(call.workspace)
    # ``-I`` left the standard library and the venv; the package goes last, so it can never
    # shadow them. The Carpeta de trabajo is never on the path.
    sys.path_hooks.insert(0, _sources_only_in(Path(call.package).resolve()))
    sys.path.append(call.package)
    # forkserver, the default since 3.14, needs a unix socket the filter refuses (#99).
    multiprocessing.set_start_method("spawn", force=True)


def _run_function(call: _Call, reply_in: int, warden: int) -> None:
    """The runner: import the function from its package, run it in the Carpeta de trabajo and
    write its answer. It never returns."""
    _prepare_runner(call, warden)
    try:
        module = importlib.import_module(call.module)
        if call.function is None:  # imported only here: pydantic costs nothing to a call
            from allin.confinement.describer import describe  # noqa: PLC0415

            value = describe(module)
        else:
            value = getattr(module, call.function)(**call.arguments)
        answer = _line({"type": "returned", "value": value})
    except BaseException as error:  # noqa: BLE001 - the function's error is its output, as data
        answer = _line({"type": "raised", "error": f"{type(error).__name__}: {error}"})
    sys.stdout.flush()
    sys.stderr.flush()
    with os.fdopen(reply_in, "wb") as out:  # writes it whole, however long
        out.write(answer)
    os._exit(0)


def _attach(call: _Call, channel: socket.socket, warden: int, scoped: bool) -> None:
    """A Proceso adherido: fork the runner that makes the fproc's class and serves its methods,
    and pass Main's calls to it and its answers back until Main detaches it, Main dies or the
    runner ends. It never returns."""
    replies_out, replies_in = os.pipe()
    calls_out, calls_in = os.pipe()
    pid = os.fork()
    if pid == 0:
        for end in (replies_out, calls_in):
            os.close(end)
        channel.close()
        _serve_fproc(call, calls_out, replies_in, warden)
    for end in (replies_in, calls_out):
        os.close(end)
    runner = _Runner(pid=pid, scoped=scoped)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: _take_down_and_exit(runner))
    if _relay_methods(channel, replies_out, calls_in):  # it ended by itself: Main hears how
        _, status = os.waitpid(runner.pid, 0)
        with contextlib.suppress(OSError):  # Main is gone too
            channel.sendall(_line({"type": "died", "exit_code": os.waitstatus_to_exitcode(status)}))
    _take_down(runner)
    os._exit(0)


def _relay_methods(channel: socket.socket, replies_out: int, calls_in: int) -> bool:
    """Pass each method call from Main to the runner and each answer back, whole lines only.
    Whether the runner ended by itself; not when Main detached it, closed its end or sent
    something that is no call."""
    selector = selectors.DefaultSelector()
    selector.register(channel, selectors.EVENT_READ)
    selector.register(replies_out, selectors.EVENT_READ)
    from_main, from_runner = b"", b""
    while True:
        for key, _ in selector.select():
            if key.fileobj is channel:
                chunk = channel.recv(1 << 16)
                if not chunk:
                    return False
                from_main += chunk
                while b"\n" in from_main:
                    line, _, from_main = from_main.partition(b"\n")
                    for_runner = _for_runner(line)
                    if for_runner is None:
                        return False  # a detach, or anything else: Main is done with it
                    _write_all(calls_in, for_runner)
                continue
            chunk = os.read(replies_out, 1 << 16)
            if not chunk or len(from_runner) > _ANSWER_MAX:
                return True
            from_runner += chunk
            while b"\n" in from_runner:
                line, _, from_runner = from_runner.partition(b"\n")
                channel.sendall(line + b"\n")  # Main parses it


_FOR_RUNNER = {  # what Main sends a Proceso adherido's runner, and the fields it keeps
    "method": ("method", "arguments", "seen"),
    "command_result": ("result",),
    "cancel_call": (),
}


def _for_runner(line: bytes) -> bytes | None:
    """A call from Main, a Command session's result or a cancel, as the runner reads it, with
    its call's id; ``None`` for anything else."""
    for kind, kept in _FOR_RUNNER.items():
        fields = _typed(line, kind)
        if fields is not None and isinstance(fields.get("id"), int):
            return _line({"type": kind, "id": fields["id"], **{k: fields.get(k) for k in kept}})
    return None


def _serve_fproc(call: _Call, calls_out: int, replies_in: int, warden: int) -> None:
    """The runner of a Proceso adherido: import the fproc from its package, make its class in
    its Carpeta de trabajo and answer each call to one of its public methods until its calls
    end (``allin.fproc_runtime``). It never returns."""
    assert call.fproc is not None
    _prepare_runner(call, warden)
    replies = os.fdopen(replies_in, "wb")
    try:
        from allin import fproc, fproc_runtime  # noqa: PLC0415 - the runner's alone

        fproc.attached_as(
            call.fproc.name, None if call.fproc.file is None else Path(call.fproc.file)
        )
        fproc_class = _fproc_class(importlib.import_module(call.module))
        instance = fproc_class()
        answer = _line({"type": "attached"})
    except BaseException as error:  # noqa: BLE001 - why it could not start is its output
        _answer(replies, _line({"type": "raised", "error": f"{type(error).__name__}: {error}"}))
        os._exit(0)
    _answer(replies, answer)
    fproc_runtime.serve(fproc_class, instance, calls_out, replies)
    os._exit(0)


def _answer(replies: IO[bytes], line: bytes) -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    replies.write(line)
    replies.flush()


def _fproc_class(module: ModuleType) -> type:
    """The module's one public class, as its description found it (``describer``)."""
    own = [
        value
        for name, value in vars(module).items()
        if not name.startswith("_")
        and isinstance(value, type | FunctionType)
        and getattr(value, "__module__", None) == module.__name__
    ]
    if len(own) != 1 or not isinstance(own[0], type):
        msg = f"{module.__name__} defines no single public class"
        raise TypeError(msg)
    return own[0]


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Compiles a module of the package from its source at every import: what was approved is
    the source, so a ``.pyc`` in ``__pycache__`` is never read (ADR-0016)."""

    def get_code(self, fullname: str) -> CodeType:
        path = self.get_filename(fullname)
        return self.source_to_code(self.get_data(path), path)


def _sources_only_in(package: Path) -> Callable[[str], importlib.machinery.FileFinder]:
    """A path hook for the package's folders alone: they import ``.py`` sources and nothing
    else, neither a ``.pyc`` without its source nor compiled code (ADR-0016, #103). Main
    refuses a package that holds any before it starts a call, since a process the function
    spawns imports without this hook."""

    def finder(path: str) -> importlib.machinery.FileFinder:
        if not Path(path).resolve().is_relative_to(package):
            raise ImportError  # not the package's: the next hook takes it
        sources = (_SourceOnlyLoader, importlib.machinery.SOURCE_SUFFIXES)
        return importlib.machinery.FileFinder(path, sources)

    return finder


if __name__ == "__main__":
    main()
