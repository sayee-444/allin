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
from allin.core.ids import ServerKey, SessionId
from allin.core.origin import Origin
from allin.main.events import ServerFailed, ServerReady, ServerState
from allin.main.prompt_commands import session_command_name
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.session_commands import SessionCommandCall, SessionCommandType

# How a Command session went, as the tool that asked for it reads it.
type Answer = Callable[[JsonValue], None]
# Runs a Command main with an Origin; calls back with how it went (``ok``, ``error …`` or
# ``rejected``) once it did, maybe after a Confirmation. Returns whether Main keeps running.
type RunMain = Callable[[str, Origin, Callable[[str], None]], bool]


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


SEND = SessionCommandType("send", "@send <sid> <text>", "send a Prompt to a Session", _send)
SERVER = SessionCommandType(
    "server", "@server [restart]", "show or restart its Session's Server", _server
)


@dataclass(frozen=True)
class LoadedSessionCommands:
    types: dict[str, SessionCommandType]
    root: tuple[str, ...]  # what a new Session gets unless ``/new`` names others
    problems: list[AllInError] = field(default_factory=list[AllInError])


def load_session_command_types(folder: Path) -> LoadedSessionCommands:
    """Main's own Command sessions and those the modules in ``folder`` declare, and why any
    module failed to load. One declared again keeps its first module."""
    types = {SEND.name: SEND, SERVER.name: SERVER}
    root = [SEND.name, SERVER.name]
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
            if declared.name in types:
                continue
            types[declared.name] = declared
            if path.parent == folder:
                root.append(declared.name)
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
            kind = self._kind(text)
            self._check_listed(kind.name, origin)
            args = text.strip().removeprefix(f"@{kind.name}").strip()
            command = _translate(
                kind, SessionCommandCall(session.id, args, session.settings.server)
            )
        except AllInError as error:
            shown = f"[{session_id}] {text.strip()[:60]}: error {error}"
            self._reporter.report(None, "session_command", shown, command=text, result=str(error))
            reply({"error": str(error)})
            return True
        self._reporter.log_session(
            session.id,
            "session_command",
            command=text,
            runs=command,
            origin=list[JsonValue](origin.labels),
        )
        restarts = command.startswith("/server restart ") and session.settings.server is not None

        def done(result: str) -> None:
            if result != "ok":
                reply({"error": result})
            elif restarts:  # it answers once its Server is up again, or could not be
                assert session.settings.server is not None
                self._restarting.setdefault(ServerKey(session.settings.server), []).append(reply)
            else:
                reply({"result": "ok"})

        return self._run_main(command, origin, done)

    def server_changed(self, server: ServerKey, state: ServerState) -> None:
        """A Server a tool restarted is ready again, or failed: the tool hears which."""
        if not isinstance(state, ServerReady | ServerFailed):
            return
        result: JsonValue = (
            {"result": "ok", "server": "ready"}
            if isinstance(state, ServerReady)
            else {"error": "failed", "server": "failed", "reason": state.reason}
        )
        for reply in self._restarting.pop(server, []):
            reply(result)

    def _kind(self, text: str) -> SessionCommandType:
        name = session_command_name(text)
        kind = self._types.get(name) if name is not None else None
        if kind is None:
            raise UnknownSessionCommand(command=name or text.strip()[:40])
        return kind

    def _check_listed(self, name: str, origin: Origin) -> None:
        """A human may run any; anyone else only one every Session of its chain has."""
        if origin.is_human:
            return
        for link in origin.chain:
            if link.kind != "session" or not link.id:
                continue
            if SessionId(link.id) not in self._sessions.ids():
                raise SessionCommandNotListed(command=name, session=link.id)
            if name not in self._sessions.load(link.id).commands:
                raise SessionCommandNotListed(command=name, session=link.id)


def _translate(kind: SessionCommandType, call: SessionCommandCall) -> str:
    try:
        command = kind.translate(call)
    except ValueError as error:
        raise InvalidSessionCommand(
            command=kind.name, reason=str(error), usage=kind.usage
        ) from error
    return command


def _ignored(_: JsonValue) -> None:
    pass
