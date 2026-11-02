"""The Command sessions Main loaded, and running one (ADR-0025).

A Command session, ``@name …``, becomes exactly one Command main, which runs with the Origin it
came with: a human's from a Window, or the chain of the Turn whose tool asked for it. A human
may use any; a Session's LLM only one that every Session of its chain has in its list, the
weakest link (ADR-0013). Each one leaves its Command main in its Session's Log.

``@send`` and ``@server`` are Main's own; the modules in ``<data-dir>/comsession/`` add theirs,
those at the folder's root being what a new Session gets by default.
"""

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import JsonValue

from allin import session_commands
from allin.core.errors import (
    AllInError,
    InvalidSessionCommand,
    SessionCommandFailed,
    SessionCommandNotListed,
    UnknownSessionCommand,
)
from allin.core.ids import CommandName, ServerKey, SessionId
from allin.core.origin import Origin
from allin.main.events import (
    CommandOutcome,
    ServerFailed,
    ServerReady,
    ServerState,
    SessionCommandResult,
)
from allin.main.prompt_commands import session_command_name
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.session_commands import SessionCommandCall, SessionCommandType

FOLDER = "comsession"  # in the data dir: the human's Command session modules
type Answer = Callable[[SessionCommandResult], None]
# Runs a Command main with an Origin; calls back with how it went once it did, maybe after a
# Confirmation. Returns whether Main keeps running.
type RunMain = Callable[[str, Origin, Callable[[CommandOutcome], None]], bool]


def _send(call: SessionCommandCall) -> str:
    sid, _, text = call.args.partition(" ")
    if not sid or not text.strip():
        msg = "a Session and a text: @send <sid> <text>"
        raise ValueError(msg)
    return f"/send {sid} {text}"


def _server(call: SessionCommandCall) -> str:
    if call.server is None:
        msg = "its Session's Enlace is a Server Main only observes, with no Supervisor"
        raise ValueError(msg)
    match call.args.split():
        case []:
            return f"/server {call.server}"
        case ["restart"]:
            return f"/server restart {call.server}"
        case _:
            msg = "@server, or @server restart"
            raise ValueError(msg)


def _conversation_logs(call: SessionCommandCall) -> str:
    words = call.args.split()
    if not 1 <= len(words) <= 2 or words[0] not in ("on", "off", "list"):
        msg = "@conversation_logs on|off|list [source]"
        raise ValueError(msg)
    return " ".join(["/conversation_logs", call.session_id, *words])


SEND = SessionCommandType("send", "@send <sid> <text>", "send a Prompt to a Session", _send)
CONVERSATION_LOGS = SessionCommandType(
    "conversation_logs",
    "@conversation_logs on|off|list [source]",
    "whether this Session's Notificaciones enter its Conversation",
    _conversation_logs,
)
SERVER = SessionCommandType(
    "server", "@server [restart]", "show or restart its Session's Server", _server
)


@dataclass(frozen=True)
class LoadedSessionCommands:
    types: dict[CommandName, SessionCommandType]
    root: tuple[CommandName, ...]  # what a new Session gets unless ``/new`` names others
    problems: list[AllInError] = field(default_factory=list[AllInError])


# Main's own, when no folder is loaded (a Main in tests).
BUILT_IN = LoadedSessionCommands(
    {CommandName(kind.name): kind for kind in (SEND, SERVER, CONVERSATION_LOGS)},
    tuple(CommandName(kind.name) for kind in (SEND, SERVER, CONVERSATION_LOGS)),
)


def chain_allows(sessions: SessionStore, origin: Origin) -> frozenset[CommandName] | None:
    """The Command sessions every Session of ``origin``'s chain has: what it may use, the
    weakest link (ADR-0013). ``None`` for a human's, which may use any."""
    if origin.is_human:
        return None
    lists = [_listed(sessions, sid) for sid in _chain_sessions(origin)]
    return lists[0].intersection(*lists[1:]) if lists else None


def lacking(sessions: SessionStore, origin: Origin, name: CommandName) -> list[str]:
    """The Sessions of ``origin``'s chain that do not have ``name`` in their list."""
    if origin.is_human:
        return []
    return [sid for sid in _chain_sessions(origin) if name not in _listed(sessions, sid)]


def _chain_sessions(origin: Origin) -> list[str]:
    return [link.id for link in origin.chain if link.kind == "session" and link.id]


def _listed(sessions: SessionStore, session_id: str) -> frozenset[CommandName]:
    """A Session's list; one that no longer exists has none."""
    if SessionId(session_id) not in sessions.ids():
        return frozenset()
    return frozenset(sessions.load(session_id).commands)


def load_session_command_types(folder: Path) -> LoadedSessionCommands:
    """Main's own Command sessions and those the modules in ``folder`` declare, and why any
    module failed to load. One declared again keeps its first module."""
    types = dict(BUILT_IN.types)
    root = list(BUILT_IN.root)
    problems: list[AllInError] = []
    paths = sorted(folder.rglob("*.py")) if folder.is_dir() else []
    for path in paths:
        session_commands.declared.clear()
        try:
            _import(path, folder)
        except Exception as error:  # noqa: BLE001 -- a broken module must not stop Main
            problems.append(
                SessionCommandFailed(module=str(path.relative_to(folder)), reason=repr(error))
            )
            continue
        for declared in session_commands.declared:
            name = CommandName(declared.name)
            if name in types:
                continue
            types[name] = declared
            if path.parent == folder:
                root.append(name)
    session_commands.declared.clear()
    return LoadedSessionCommands(types, tuple(root), problems)


def _import(path: Path, folder: Path) -> None:
    name = "_".join(path.relative_to(folder).with_suffix("").parts)
    spec = importlib.util.spec_from_file_location(f"allin_comsession_{name}", path)
    if spec is None or spec.loader is None:  # a ``.py`` file always has a loader
        raise SessionCommandFailed(module=path.name, reason="not a module")
    spec.loader.exec_module(importlib.util.module_from_spec(spec))


class SessionCommands:
    """Runs the Command sessions of this Main's Sessions."""

    def __init__(
        self,
        loaded: LoadedSessionCommands,
        sessions: SessionStore,
        load: Callable[[str], Session],
        run_main: RunMain,
        reporter: Reporter,
    ) -> None:
        """``load`` gives a Session of this Main by id; ``run_main`` runs a Command main."""
        self._types = loaded.types
        self._sessions = sessions
        self._load = load
        self._run_main = run_main
        self._reporter = reporter
        # The tools waiting for a Server their ``@server restart`` restarts to be up again.
        self._restarting: dict[ServerKey, list[Answer]] = {}

    def run(self, session_id: str, text: str, origin: Origin, answer: Answer | None = None) -> bool:
        """Run ``text``, ``@name …``, asked in ``session_id``'s Window or by a tool of its Turn
        with ``origin``; ``answer`` gets how it went. Returns whether Main keeps running."""
        reply = answer or _ignored
        try:
            session = self._load(session_id)
            name, kind = self._kind(text)
            self._check_listed(name, origin)
            args = text.strip().removeprefix(f"@{name}").strip()
            server = session.settings.server
            command = _translate(kind, SessionCommandCall(session.id, args, server))
        except AllInError as error:
            shown = f"[{session_id}] {text.strip()[:60]}: error {error}"
            self._reporter.report(None, "session_command", shown, command=text, result=str(error))
            reply(SessionCommandResult(error=str(error)))
            return True
        self._reporter.log_session(
            session.id,
            "session_command",
            command=text,
            runs=command,
            origin=list[JsonValue](origin.labels),
        )
        restarts = server if kind is SERVER and args == "restart" else None

        def done(outcome: CommandOutcome) -> None:
            match outcome:
                case "ok" if restarts is not None:  # it answers once its Server is back, or not
                    self._restarting.setdefault(ServerKey(restarts), []).append(reply)
                case "ok":
                    reply(SessionCommandResult())
                case "rejected":
                    reply(SessionCommandResult(error="rejected"))
                case AllInError() as error:
                    reply(SessionCommandResult(error=str(error)))

        return self._run_main(command, origin, done)

    def server_changed(self, server: ServerKey, state: ServerState) -> None:
        """A Server a tool restarted is ready again, or failed: the tool hears which."""
        if not isinstance(state, ServerReady | ServerFailed):
            return
        result = (
            SessionCommandResult(server="ready")
            if isinstance(state, ServerReady)
            else SessionCommandResult(error=f"failed: {state.reason}", server="failed")
        )
        for reply in self._restarting.pop(server, []):
            reply(result)

    def _kind(self, text: str) -> tuple[CommandName, SessionCommandType]:
        written = session_command_name(text)
        name = CommandName(written or text.strip()[:40])
        kind = self._types.get(name) if written is not None else None
        if kind is None:
            raise UnknownSessionCommand(command=name)
        return name, kind

    def _check_listed(self, name: CommandName, origin: Origin) -> None:
        """A human may run any; anyone else only one every Session of its chain has."""
        missing = lacking(self._sessions, origin, name)
        if missing:
            raise SessionCommandNotListed(command=name, not_in=list[JsonValue](missing))


def _translate(kind: SessionCommandType, call: SessionCommandCall) -> str:
    try:
        command = kind.translate(call)
    except ValueError as error:
        raise InvalidSessionCommand(
            command=kind.name, reason=str(error), usage=kind.usage
        ) from error
    return command


def _ignored(_: SessionCommandResult) -> None:
    pass
