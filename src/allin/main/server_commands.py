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
from allin.main.status import SupervisorView
from allin.main.turn_queue import Servers, TurnQueue
from allin.supervisor.lock import supervisor_active
from allin.supervisor.ports import listening_pid, port_free
from allin.supervisor.settings import (
    CONTEXT_SHIFT_ENV,
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
_PROC = Path("/proc")
_FALSE = frozenset({b"", b"0", b"false", b"off", b"no", b"disabled"})


def _first_word(text: str) -> tuple[str, str]:
    """A line's first word and the rest after it; both empty for a blank line."""
    words = text.split(maxsplit=1)
    return (words[0] if words else "", words[1] if len(words) > 1 else "")


def _action(args: list[str]) -> str:
    """The word after ``/server``; empty for the list of them all."""
    return _first_word(args[0])[0] if args else ""


def delicate(args: list[str]) -> bool:
    return _action(args) in _DELICATE


def human_only(args: list[str]) -> bool:
    return _action(args) in _HUMAN_ONLY


@dataclass(frozen=True)
class _Line:
    """The Supervisor a ``/server`` action names, and what follows its name."""

    name: SupervisorName
    rest: str


class ServerCommands:
    def __init__(
        self,
        servers: Servers,
        reporter: Reporter,
        turns: TurnQueue,
        links: LinkResolver,
        sessions: SessionStore,
        windows: OpenWindows,
        default_server: Callable[[], SupervisorName | None],
        post: Callable[[MainEvent], None],
    ) -> None:
        """``default_server``: the default Enlace now; ``post`` puts an event in Main's
        inbox from another thread."""
        self._dir = links.servers
        self._servers = servers
        self._reporter = reporter
        self._turns = turns
        self._links = links
        self._sessions = sessions
        self._windows = windows
        self._default_server = default_server
        self._post = post

    def run(self, text: str = "", origin: Origin = HUMAN) -> bool:
        action, rest = _first_word(text)
        match action:
            case "":
                self._list()
            case "new":
                self._new(self._named(rest, exists=False, takes_rest=True), origin)
            case "set":
                self._set(self._maybe_named(rest), origin)
            case "restart":
                self._restart(self._maybe_named(rest))
            case "adopt":
                self._adopt(self._named(rest, exists=False))
            case "sleep":
                self._sleep(self._named(rest))
            case "wake":
                self._wake(self._named(rest).name)
            case _ if not rest:
                self._show(self._existing(action))
            case _:
                raise InvalidArguments(usage=USAGE)
        return True

    def adopted(self, name: SupervisorName) -> None:
        """The adopted llama ended and freed its port: its Supervisor launches it again."""
        self._wake(name)

    # --- reading the line

    def _named(self, rest: str, exists: bool = True, takes_rest: bool = False) -> _Line:
        first, after = _first_word(rest)
        name = parse_supervisor_name(first)
        if name is None or (after and not takes_rest):
            raise InvalidArguments(usage=USAGE)
        if exists:
            self._existing(name)
        elif self._toml(name).exists():
            raise SupervisorExists(supervisor=name)
        return _Line(name, after)

    def _maybe_named(self, rest: str) -> _Line:
        """``[name] …``: the default Enlace's Supervisor when the first word is no name."""
        first, after = _first_word(rest)
        if first and "=" not in first and first != "--now":
            return _Line(self._existing(first), after)
        default = self._default_server()
        if default is None:
            raise InvalidArguments(usage=USAGE)  # no default Enlace to fall back on
        return _Line(self._existing(default), rest)

    def _existing(self, text: str) -> SupervisorName:
        name = parse_supervisor_name(text)
        if name is None:
            raise InvalidArguments(usage=USAGE)
        if not self._toml(name).exists():
            raise SupervisorNotFound(supervisor=name)
        return name

    def _toml(self, name: SupervisorName) -> Path:
        return self._dir / f"{name}.toml"

    # --- actions

    def _new(self, line: _Line, origin: Origin) -> None:
        assignments = parse_assignments(line.rest)
        check_server_authority(assignments, origin)
        settings = apply_server_assignments(None, assignments)
        save_server_settings(self._toml(line.name), settings)
        shown = f"Supervisor {line.name} created, asleep: work for it wakes it"
        self._reporter.report(None, "supervisor_created", shown, server=line.name)

    def _set(self, line: _Line, origin: Origin) -> None:
        assignments = parse_assignments(line.rest)
        if not assignments:
            raise InvalidArguments(usage=USAGE)
        check_server_authority(assignments, origin)
        name = line.name
        settings = apply_server_assignments(load_server_settings(self._toml(name)), assignments)
        save_server_settings(self._toml(name), settings)  # only once it is all valid
        shown = f"Supervisor {name}: pending Settings changed; /server restart {name} applies them"
        self._reporter.report(None, "server_settings_changed", shown, server=name)

    def _restart(self, line: _Line) -> None:
        if line.rest not in ("", "--now"):
            raise InvalidArguments(usage=USAGE)
        self._servers.restart(line.name, now=line.rest == "--now")

    def _sleep(self, line: _Line) -> None:
        self._servers.sleep(line.name)
        shown = f"Supervisor {line.name}: going to sleep; only /server wake wakes it"
        self._reporter.report(None, "supervisor_sleep", shown, server=line.name)

    def _wake(self, name: SupervisorName) -> None:
        self._turns.watch(self._links.resolve_name(name))  # so whether it fits in VRAM is told
        if self._servers.wake(name, human=True):
            shown = f"waking Supervisor {name}"
            self._reporter.report(None, "supervisor_woken", shown, server=name)
        else:
            self._reporter.show(None, f"Supervisor {name} is awake already")

    def _adopt(self, line: _Line) -> None:
        """Save the Settings of the ``llama serve`` on Main's ``base_url`` port as a new
        Supervisor's, end it and have the Supervisor launch it again once it is gone."""
        port = urlsplit(self._links.observed().base_url).port or 0
        pid, settings = self._adoptable(port)
        save_server_settings(self._toml(line.name), settings)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:  # it ended meanwhile: nothing to take over
            self._toml(line.name).unlink()
            raise ServerNotAdoptable(reason="it ended before it was adopted", pid=pid) from None
        shown = f"Supervisor {line.name} adopts pid {pid}: launched again once it ends"
        arguments = shlex.join(settings.arguments())
        fields = {"server": line.name, "pid": pid, "arguments": arguments}
        self._reporter.report(None, "supervisor_adopted", shown, **fields)
        relaunch = threading.Thread(
            target=self._relaunch_when_gone, args=(line.name, pid, port), daemon=True
        )
        relaunch.start()

    def _adoptable(self, port: int) -> tuple[int, ServerSettings]:
        """The process that listens on ``port`` and the Settings that launch it again;
        ``ServerNotAdoptable`` if there is none, or a Supervisor already runs it."""
        pid = listening_pid(port)
        if pid is None:
            raise ServerNotAdoptable(reason="no process of this user listens there", port=port)
        for name in self._names():
            state = read_state(self._dir, name)
            if state is not None and state.server_pid == pid and supervisor_active(self._dir, name):
                reason = "a Supervisor already runs it"
                raise ServerNotAdoptable(reason=reason, supervisor=name, pid=pid)
        settings = _read_process(pid)
        if settings.port != port:
            raise ServerNotAdoptable(reason="its line names another port", port=settings.port)
        return pid, settings

    def _relaunch_when_gone(self, name: SupervisorName, pid: int, port: int) -> None:
        deadline = time.monotonic() + _GONE_TIMEOUT
        while (_PROC / str(pid)).exists() or not port_free(port):
            if time.monotonic() > deadline:
                break  # waking it then says who holds the port
            time.sleep(_GONE_POLL)
        self._post(SupervisorAdopted(name))

    # --- showing

    def _names(self) -> list[SupervisorName]:
        names = (parse_supervisor_name(path.stem) for path in sorted(self._dir.glob("*.toml")))
        return [name for name in names if name is not None]

    def supervisors(self) -> list[SupervisorView]:
        """Consultar's (#118): each Supervisor, how it is, and which is the default Enlace's."""
        default = self._default_server()
        return [
            SupervisorView(name=name, status=self._status(name), default=name == default)
            for name in self._names()
        ]

    def _list(self) -> None:
        names = self._names()
        default = self._default_server()
        users = self._open_sessions_by_server()
        if not names:
            self._reporter.show(None, "no Supervisors: /server new <name> key=value… makes one")
        for name in names:
            mark = " (default Enlace)" if name == default else ""
            count = users.get(name, 0)
            # Other Mains' Sessions are not seen until Mains talk to each other (F3).
            line = f"{name}{mark}: {self._status(name)}, used by {count} open Session(s) here"
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
            shown = f"runs its last Settings that started: {state.fallback_reason}"
            self._reporter.show(None, shown)
        differs = running is not None and running.effective() != pending.effective()
        for key in ServerSettings.model_fields:
            line = f"{key}={_shown(getattr(pending, key))}"
            if differs and running is not None:
                ran = getattr(running.effective(), key)
                if ran != getattr(pending.effective(), key):
                    line += f"   * running: {_shown(ran)}"
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

    def _open_sessions_by_server(self) -> dict[SupervisorName, int]:
        counts: dict[SupervisorName, int] = {}
        for session_id in self._windows.pids():
            try:
                server = self._sessions.load(session_id).settings.server
            except AllInError:
                continue
            name = parse_supervisor_name(server) if server is not None else None
            if name is not None:
                counts[name] = counts.get(name, 0) + 1
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
    proc = _PROC / str(pid)
    try:
        argv = [word.decode() for word in (proc / "cmdline").read_bytes().split(b"\0") if word]
        cwd = str((proc / "cwd").readlink())
        environ = (proc / "environ").read_bytes().split(b"\0")
    except OSError as error:
        raise ServerNotAdoptable(reason=error.strerror, pid=pid) from error
    for entry in environ:
        key, _, value = entry.partition(b"=")
        if key == CONTEXT_SHIFT_ENV.encode() and value.lower() not in _FALSE:
            reason = "its environment turns on context shift, which truncates in silence"
            raise ServerNotAdoptable(reason=reason)
    return parse_cmdline(argv, cwd=cwd)
