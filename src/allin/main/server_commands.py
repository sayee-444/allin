"""``/server …``: the Command mains that show, create, change, restart, adopt, put to sleep
and wake Supervisors (ADR-0008, ADR-0022).

Only the Supervisor writes its ``.json``; these write its ``.toml`` (the pending Server
Settings) and ask the running process by signal. The keys that name a file or open the network
are only set by a human (ADR-0013).
"""

import os
import shlex
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from allin.core.errors import (
    AllInError,
    InvalidArguments,
    ServerNotAdoptable,
    SupervisorExists,
    SupervisorNotFound,
)
from allin.core.ids import SupervisorName, parse_supervisor_name
from allin.core.origin import HUMAN, Origin
from allin.core.settings import parse_assignments
from allin.main.events import MainEvent, SupervisorAdopted
from allin.main.links import LinkResolver
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore
from allin.main.turn_queue import Servers, TurnQueue
from allin.supervisor.lock import supervisor_active
from allin.supervisor.ports import listening_pid, port_free
from allin.supervisor.settings import (
    ServerSettings,
    apply_server_assignments,
    check_server_authority,
    load_server_settings,
    parse_cmdline,
    save_server_settings,
)
from allin.supervisor.state import SupervisorState, read_state

USAGE = (
    "/server [name] | /server new <name> key=value… | /server set [name] key=value…"
    " | /server restart [name] [--now] | /server adopt <name> | /server sleep <name>"
    " | /server wake <name>"
)
# These ask a Confirmation first: one cuts a Server off its GPU, the other ends a process.
_DELICATE = frozenset({"sleep", "adopt"})
# Only a human puts one to sleep (ADR-0022), or adopts a process with whatever it reads.
_HUMAN_ONLY = frozenset({"sleep", "adopt"})
_RUNNING = frozenset({"starting", "ready", "crashed", "draining"})
_GONE_TIMEOUT = 30.0  # how long an adopted llama may take to free its port
_GONE_POLL = 0.1
_CONTEXT_SHIFT_ENV = b"LLAMA_ARG_CONTEXT_SHIFT"
_FALSE = frozenset({b"", b"0", b"false", b"off", b"no", b"disabled"})


def _action(args: list[str]) -> str:
    """The word after ``/server``; empty for the list of them all."""
    return args[0].split(maxsplit=1)[0] if args and args[0].split() else ""


def delicate(args: list[str]) -> bool:
    return _action(args) in _DELICATE


def human_only(args: list[str]) -> bool:
    return _action(args) in _HUMAN_ONLY


@dataclass(frozen=True)
class _Line:
    """What ``/server`` was asked: an action, the Supervisor it names and what follows."""

    action: str
    name: SupervisorName | None
    rest: str


class ServerCommands:
    def __init__(
        self,
        servers_dir: Path,
        servers: Servers,
        reporter: Reporter,
        turns: TurnQueue,
        links: LinkResolver,
        sessions: SessionStore,
        windows: OpenWindows,
        default_server: Callable[[], str | None],
        post: Callable[[MainEvent], None],
    ) -> None:
        """``default_server``: the default Enlace now; ``post`` puts an event in Main's
        inbox from another thread."""
        self._dir = servers_dir
        self._servers = servers
        self._reporter = reporter
        self._turns = turns
        self._links = links
        self._sessions = sessions
        self._windows = windows
        self._default_server = default_server
        self._post = post

    def run(self, text: str = "", origin: Origin = HUMAN) -> bool:
        words = text.split(maxsplit=1)
        action = words[0] if words else ""
        rest = words[1] if len(words) > 1 else ""
        match action:
            case "":
                self._list()
            case "new":
                line = self._named(action, rest, exists=False)
                self._new(line, origin)
            case "set":
                self._set(self._maybe_named(rest), origin)
            case "restart":
                line = self._maybe_named(rest)
                if line.rest not in ("", "--now"):
                    raise InvalidArguments(usage=USAGE)
                self._servers.restart(self._name(line), now=line.rest == "--now")
            case "adopt":
                self._adopt(self._named(action, rest, exists=False))
            case "sleep":
                line = self._named(action, rest)
                self._servers.sleep(self._name(line))
                shown = f"Supervisor {line.name}: going to sleep; only /server wake wakes it"
                self._reporter.report(None, "supervisor_sleep", shown, server=line.name)
            case "wake":
                self._wake(self._named(action, rest))
            case _ if not rest:
                self._show(self._existing(action))
            case _:
                raise InvalidArguments(usage=USAGE)
        return True

    def adopted(self, name: SupervisorName) -> None:
        """The adopted llama ended and freed its port: its Supervisor launches it again."""
        self._wake(_Line("wake", name, ""))

    # --- reading the line

    def _named(self, action: str, rest: str, exists: bool = True) -> _Line:
        words = rest.split(maxsplit=1)
        name = parse_supervisor_name(words[0]) if words else None
        if name is None:
            raise InvalidArguments(usage=USAGE)
        line = _Line(action, name, words[1] if len(words) > 1 else "")
        if action not in ("new", "set") and line.rest:
            raise InvalidArguments(usage=USAGE)
        if exists:
            self._existing(name)
        elif self._toml(name).exists():
            raise SupervisorExists(supervisor=name)
        return line

    def _maybe_named(self, rest: str) -> _Line:
        """``[name] …``: the default Enlace's Supervisor when the first word is no name."""
        words = rest.split(maxsplit=1)
        first = words[0] if words else ""
        if first and "=" not in first and first != "--now":
            name = self._existing(first)
            return _Line("", name, words[1] if len(words) > 1 else "")
        default = self._default_server()
        if default is None:
            raise InvalidArguments(usage=USAGE)  # no default Enlace to fall back on
        return _Line("", self._existing(default), rest)

    def _existing(self, text: str) -> SupervisorName:
        name = parse_supervisor_name(text)
        if name is None:
            raise InvalidArguments(usage=USAGE)
        if not self._toml(name).exists():
            raise SupervisorNotFound(supervisor=name)
        return name

    def _name(self, line: _Line) -> SupervisorName:
        assert line.name is not None  # every parse above gives one
        return line.name

    def _toml(self, name: SupervisorName) -> Path:
        return self._dir / f"{name}.toml"

    # --- actions

    def _new(self, line: _Line, origin: Origin) -> None:
        assignments = parse_assignments(line.rest)
        check_server_authority(assignments, origin)
        settings = apply_server_assignments(None, assignments)
        save_server_settings(self._toml(self._name(line)), settings)
        shown = f"Supervisor {line.name} created, asleep: work for it wakes it"
        self._reporter.report(None, "supervisor_created", shown, server=line.name)

    def _set(self, line: _Line, origin: Origin) -> None:
        assignments = parse_assignments(line.rest)
        if not assignments:
            raise InvalidArguments(usage=USAGE)
        check_server_authority(assignments, origin)
        name = self._name(line)
        settings = apply_server_assignments(load_server_settings(self._toml(name)), assignments)
        save_server_settings(self._toml(name), settings)  # only once it is all valid
        shown = f"Supervisor {name}: pending Settings changed; /server restart {name} applies them"
        self._reporter.report(None, "server_settings_changed", shown, server=name)

    def _wake(self, line: _Line) -> None:
        name = self._name(line)
        link = self._links.resolve_name(name)
        self._turns.watch(link)  # so that whether it fits in VRAM is told
        if self._servers.wake(name, human=True):
            self._reporter.report(
                None, "supervisor_woken", f"waking Supervisor {name}", server=name
            )
        else:
            self._reporter.show(None, f"Supervisor {name} is awake already")

    def _adopt(self, line: _Line) -> None:
        """Read the ``llama serve`` on Main's ``base_url`` port, save its Settings as a new
        Supervisor's, end it and have the Supervisor launch it again."""
        name = self._name(line)
        port = urlsplit(self._links.observed().base_url).port
        pid = listening_pid(port) if port is not None else None
        if port is None or pid is None:
            raise ServerNotAdoptable(reason=f"no process of this user listens on port {port}")
        settings = _read_process(pid)
        if settings.port != port:
            reason = f"it says port {settings.port} but listens on {port}"
            raise ServerNotAdoptable(reason=reason)
        save_server_settings(self._toml(name), settings)
        os.kill(pid, signal.SIGTERM)
        shown = f"Supervisor {name} adopts pid {pid}: launched again once it ends"
        self._reporter.report(
            None,
            "supervisor_adopted",
            shown,
            server=name,
            arguments=shlex.join(settings.arguments()),
        )
        threading.Thread(
            target=self._relaunch_when_gone, args=(name, pid, port), daemon=True
        ).start()

    def _relaunch_when_gone(self, name: SupervisorName, pid: int, port: int) -> None:
        deadline = time.monotonic() + _GONE_TIMEOUT
        while (Path(f"/proc/{pid}").exists() or not port_free(port)) and (
            time.monotonic() < deadline
        ):
            time.sleep(_GONE_POLL)
        self._post(SupervisorAdopted(name))  # if it would not end, waking says the port's holder

    # --- showing

    def _list(self) -> None:
        names = [parse_supervisor_name(path.stem) for path in sorted(self._dir.glob("*.toml"))]
        default = self._default_server()
        users = self._open_sessions_by_server()
        shown = [name for name in names if name is not None]
        if not shown:
            self._reporter.show(None, "no Supervisors: /server new <name> key=value… makes one")
        for name in shown:
            mark = " (default Enlace)" if name == default else ""
            count = users.get(name, 0)
            line = f"{name}{mark}: {self._status(name)}, used by {count} open Session(s)"
            self._reporter.show(None, line)

    def _show(self, name: SupervisorName) -> None:
        self._reporter.show(None, f"Supervisor {name}: {self._status(name)}")
        state = read_state(self._dir, name)
        running = _running_settings(state) if supervisor_active(self._dir, name) else None
        try:
            pending = load_server_settings(self._toml(name))
        except AllInError as error:
            self._reporter.show(None, f"pending Settings unreadable: {error}")
            return
        if state is not None and state.fallback_reason:
            self._reporter.show(
                None, f"runs its last Settings that started: {state.fallback_reason}"
            )
        for key in ServerSettings.model_fields:
            value = getattr(pending, key)
            line = f"{key}={_shown(value)}"
            if running is not None and getattr(running, key) != value:
                line += f"   * running: {_shown(getattr(running, key))}"
            self._reporter.show(None, line)

    def _status(self, name: SupervisorName) -> str:
        state = read_state(self._dir, name)
        if supervisor_active(self._dir, name):
            status = state.status if state is not None else "starting"
            pid = f" (Server pid {state.server_pid})" if state and state.server_pid else ""
            return status + pid
        if state is not None and state.status == "failed":
            return f"failed ({state.failure}): {state.reason}"
        if state is not None and state.slept_by_human:
            return "asleep, put to sleep by a human; only /server wake wakes it"
        return "asleep; work for it wakes it"

    def _open_sessions_by_server(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for session_id in self._windows.pids():
            try:
                server = self._sessions.load(session_id).settings.server
            except AllInError:
                continue
            if server is not None:
                counts[server] = counts.get(server, 0) + 1
        return counts


def _running_settings(state: SupervisorState | None) -> ServerSettings | None:
    if state is None or state.status not in _RUNNING or not state.arguments:
        return None
    try:
        return parse_cmdline(state.arguments, cwd=state.cwd)
    except ServerNotAdoptable:
        return None


def _shown(value: str | int | bool | list[str] | None) -> str:
    """A key's value as ``/server set`` takes it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return shlex.join(value)
    return str(value)


def _read_process(pid: int) -> ServerSettings:
    """The Server Settings of the running process ``pid``, from ``/proc``."""
    proc = Path(f"/proc/{pid}")
    try:
        argv = [word.decode() for word in (proc / "cmdline").read_bytes().split(b"\0") if word]
        cwd = str((proc / "cwd").readlink())
        environ = (proc / "environ").read_bytes().split(b"\0")
    except OSError as error:
        raise ServerNotAdoptable(reason=f"cannot read pid {pid}: {error.strerror}") from error
    for entry in environ:
        key, _, value = entry.partition(b"=")
        if key == _CONTEXT_SHIFT_ENV and value.lower() not in _FALSE:
            reason = "its environment turns on context shift, which truncates in silence"
            raise ServerNotAdoptable(reason=reason)
    return parse_cmdline(argv, cwd=cwd)
