"""The Command sessions Main loaded, and running one (ADR-0025).

A Command session, ``@name …``, becomes exactly one Command main, which runs with the Origin it
came with: a human's from a Window, or the chain of the Turn whose tool asked for it. A human
may use any; a Session's LLM only one that every Session of its chain has in its list, the
weakest link (ADR-0013). Each one leaves its Command main in its Session's Log. A Proceso
adherido runs, on its own, those that act on its Sessions the same way, and leaves them in
Main's Log (ADR-0026).

``@send`` and ``@server`` are Main's own; the modules in ``<data-dir>/comsession/`` add theirs,
those at the folder's root being what a new Session gets by default.
"""

import importlib.util
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import JsonValue

from allin import session_commands
from allin.core.errors import (
    AllInError,
    InitiativeRefused,
    InvalidSessionCommand,
    SecretInMainChatOnly,
    SessionCommandFailed,
    SessionCommandNotListed,
    UnknownSessionCommand,
)
from allin.core.ids import AttachedName, CommandName, NotificationSource, ServerKey, SessionId
from allin.core.origin import Origin
from allin.decorators import GRANT as GRANT_NAME
from allin.main.attached import TO
from allin.main.events import (
    CommandOutcome,
    OnOutcome,
    Queried,
    ServerFailed,
    ServerReady,
    ServerState,
    SessionCommandResult,
)
from allin.main.notifications import Notifications
from allin.main.prompt_commands import read_secret, session_command_name
from allin.main.reporting import Reporter
from allin.main.secret_commands import USAGE as SECRET_USAGE
from allin.main.sessions import Session, SessionStore
from allin.main.status import PARTS_SHOWN as STATUS_PARTS
from allin.session_commands import SessionCommandCall, SessionCommandType

FOLDER = "comsession"  # in the data dir: the human's Command session modules
# Gives how it went to who asked; ``False`` if nobody waits for it any more: a tool whose Turn
# was cancelled meanwhile, say while a human took long to answer its Confirmation.
type Answer = Callable[[SessionCommandResult], bool]
# Runs a Command main with an Origin; calls back with how it went once it did, maybe after a
# Confirmation. Returns whether Main keeps running.
type RunMain = Callable[[str, Origin, OnOutcome], bool]


def _send(args: str) -> str:
    """The same from a Session as from an fproc's Iniciativa."""
    sid, _, text = args.partition(" ")
    if not sid or not text.strip():
        msg = f"a Session and a text: {_SEND_USAGE}"
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


_CONVERSATION_LOGS_USAGE = "@conversation_logs on|off|list [source]"


def _conversation_logs(call: SessionCommandCall) -> str:
    """Its action is checked by the Command main it becomes."""
    words = call.args.split()
    if not 1 <= len(words) <= 2:
        raise ValueError(_CONVERSATION_LOGS_USAGE)
    return " ".join(["/conversation_logs", call.session_id, *words])


# Its Command prompts go in the structured form, ahead of the text, which Main never reads; a
# relative path is from the sender's Carpeta de trabajo (#110); ``--reply-to=me`` wakes the
# sender with the answer (#116).
_SEND_USAGE = "@send <sid> [--reply-to=me] [--attach=<path>]… [--<param>=<value>]… [--] <text>"
SEND = SessionCommandType(
    "send",
    _SEND_USAGE,
    "send a Prompt to a Session of this Profile, maybe with files and Turn parameters;"
    " with --reply-to=me its answer comes back to you as a Prompt",
    lambda call: _send(call.args),
    initiative=_send,
)

_SUBSCRIBE_USAGE = "@subscribe [process:<name>|timer:<seconds>|rewind:<sid>|compact:<sid>]"


def _subscribe(call: SessionCommandCall) -> str:
    """Always its own Session (ADR-0011); what it waits for is checked by the Command main."""
    words = call.args.split()
    if len(words) > 1:
        raise ValueError(_SUBSCRIBE_USAGE)
    return " ".join(["/subscribe", call.session_id, *words])


def _unsubscribe(call: SessionCommandCall) -> str:
    words = call.args.split()
    if len(words) != 1:
        msg = "@unsubscribe <subscription>"
        raise ValueError(msg)
    return f"/unsubscribe {call.session_id} {words[0]}"


SUBSCRIBE = SessionCommandType(
    "subscribe",
    _SUBSCRIBE_USAGE,
    "list your Suscripciones, or open one: the event wakes you with a Prompt once your Turn ended",
    _subscribe,
)
_COMPACT_USAGE = "@compact [instructions]"


def _compact(call: SessionCommandCall) -> str:
    """Always its own Conversation (ADR-0017); the instructions guide its summary."""
    return f"/session compact {call.session_id} {call.args}".rstrip()


COMPACT = SessionCommandType(
    "compact",
    _COMPACT_USAGE,
    "compact your Conversation now, at the end of a phase of work: its old Turns become a"
    " summary, guided by your instructions",
    _compact,
)
_STATUS_USAGE = f"@status [{STATUS_PARTS}]"


def _status(call: SessionCommandCall) -> str:
    """Always for its own Session, read only (#118)."""
    words = call.args.split()
    if len(words) > 1:
        raise ValueError(_STATUS_USAGE)
    return " ".join(["/status", call.session_id, *words])


STATUS = SessionCommandType(
    "status",
    _STATUS_USAGE,
    "see what you have within reach: the queue, Suscripciones and Encargos, Procesos adheridos,"
    " Servers and Supervisors",
    _status,
)
UNSUBSCRIBE = SessionCommandType(
    "unsubscribe", "@unsubscribe <subscription>", "close one of your Suscripciones", _unsubscribe
)


def _errand(call: SessionCommandCall) -> str:
    """Always for its own Session (ADR-0011); the rest is checked by the Command main."""
    if not call.args.strip():
        raise ValueError(_ERRAND_USAGE)
    return f"/errand {call.session_id} {call.args.strip()}"


def _batch(call: SessionCommandCall) -> str:
    """Always for its own Session, as ``@errand``."""
    if not call.args.strip():
        raise ValueError(_BATCH_USAGE)
    return f"/batch {call.session_id} {call.args.strip()}"


_ERRAND_USAGE = (
    "@errand [--k=<n>] [--deadline=<seconds>] [--retries=<n>]"
    ' --to=<sid>,<sid>… <task> | {"<sid>": "<task>", …}'
)
_BATCH_USAGE = (
    '@batch [--k=<n>] [--deadline=<seconds>] [--retries=<n>] --to=<sid>,<sid>… ["<task>", …]'
)
ERRAND = SessionCommandType(
    "errand",
    _ERRAND_USAGE,
    "hand tasks out to other Sessions; once all answered you wake with every result",
    _errand,
)
BATCH = SessionCommandType(
    "batch",
    _BATCH_USAGE,
    "a list of tasks free workers take in turns; once done you wake with every result",
    _batch,
)
CONVERSATION_LOGS = SessionCommandType(
    "conversation_logs",
    _CONVERSATION_LOGS_USAGE,
    "whether this Session's Notificaciones enter its Conversation",
    _conversation_logs,
)
SERVER = SessionCommandType(
    "server", "@server [restart]", "show or restart its Session's Server", _server
)


_GRANT_USAGE = "@grant <sid> <piece|@command>…"


def _grant(call: SessionCommandCall) -> str:
    """What it gives is checked by the Command main it becomes."""
    sid, _, named = call.args.partition(" ")
    if not sid or not named.strip():
        raise ValueError(_GRANT_USAGE)
    return f"/grant {sid} {named}"


_CREATE_USAGE = "@create <sysprompt:<file>|mold:<name>> <content>"


def _create(call: SessionCommandCall) -> str:
    """What it creates is checked by the Command main it becomes; the content goes as is,
    newlines included."""
    parts = call.args.strip().split(maxsplit=1)
    if len(parts) < 2:
        raise ValueError(_CREATE_USAGE)
    piece, content = parts[0], call.args.strip()[len(parts[0]) :].lstrip()
    return f"/create {piece} {content}"


CREATE = SessionCommandType(
    "create",
    _CREATE_USAGE,
    "create a Sysprompt or a Molde of this Profile; a human confirms it, and it waits in"
    " quarantine if nobody did",
    _create,
)


NEW_USAGE = "@new <mold> [--id=<id>]"


def _new(args: str) -> str:
    """``@new``'s Command main: a Session from a Molde, born in the background; for a Session's
    LLM a Molde of the Profile, for an fproc one of its package, in its Subperfil (ADR-0026)."""
    words = args.split()
    if not 1 <= len(words) <= 2 or words[0].startswith("-"):
        raise ValueError(NEW_USAGE)
    if any(not word.startswith("--id=") for word in words[1:]):
        raise ValueError(NEW_USAGE)
    return " ".join(["/new", f"--mold={words[0]}", "--bg", *words[1:]])


NEW = SessionCommandType(
    "new",
    NEW_USAGE,
    "create a Session from a Molde, in the background, and hear its id; an fproc's come from"
    " its package, in its Subperfil",
    lambda call: _new(call.args),
    initiative=_new,
)


_ATTACH_USAGE = "@attach <fproc> [name] [--file=<path>]"


def _attach(call: SessionCommandCall) -> str:
    """Its methods go to its own Session (ADR-0021); a relative ``--file`` is from its Carpeta
    de trabajo, and one from outside waits for a human's Approval (#113)."""
    words = call.args.split()
    if not words or any(word.partition("=")[0] == TO.rstrip("=") for word in words):
        raise ValueError(_ATTACH_USAGE)
    return " ".join(["/attach", *words, f"{TO}{call.session_id}"])


IN = "--in="  # @call's /call: the Session whose Window it was typed in
_CALL_USAGE = "@call <name> [<method> [key=value…]]"


def _call(call: SessionCommandCall) -> str:
    """What it gives back shows in this Window and stays in this Session's Log, with a
    Notificación (ADR-0021)."""
    words = call.args.split()
    if not words or any(word.startswith(IN) for word in words):
        raise ValueError(_CALL_USAGE)
    return f"/call {call.args.strip()} {IN}{call.session_id}"


CALL = SessionCommandType(
    "call",
    _CALL_USAGE,
    "call a method of a Proceso adherido, or list them; this Window shows what it gives back",
    _call,
)


ATTACH = SessionCommandType(
    "attach",
    _ATTACH_USAGE,
    "attach a Proceso adherido from an approved fproc, whose methods this Session gets",
    _attach,
)


_BG_USAGE = "@bg [sid]"
_CLOSE_USAGE = "@close"


def _bg(call: SessionCommandCall) -> str:
    """Its own Session, or the one it names; which ones a Session's LLM may send away is
    checked by the Command main (ADR-0020)."""
    words = call.args.split()
    if len(words) > 1:
        raise ValueError(_BG_USAGE)
    return f"/bg {words[0] if words else call.session_id}"


def _close(call: SessionCommandCall) -> str:
    """Always its own Window, as its X; ``/close <sid>`` in Main's Chat closes any."""
    if call.args.strip():
        raise ValueError(_CLOSE_USAGE)
    return f"/close {call.session_id}"


def _close_named(args: str) -> str:
    """An fproc has no Window of its own: it names the Session of its Subperfil it closes."""
    return f"/close {_one_session(args, '@close <sid>')}"


_OPEN_USAGE = "@open <sid>"


def _open(args: str) -> str:
    """Never ``--bg``: sending a Session to the background is ``@bg``'s, with its check. The
    same from a Session as from an fproc's Iniciativa."""
    return f"/open {_one_session(args, _OPEN_USAGE)}"


def _one_session(args: str, usage: str) -> str:
    words = args.split()
    if len(words) != 1 or words[0].startswith("-"):
        raise ValueError(usage)
    return words[0]


_WINDOWS_USAGE = "@windows"


def _windows(call: SessionCommandCall) -> str:
    if call.args.strip():
        raise ValueError(_WINDOWS_USAGE)
    return "/windows list"


BG = SessionCommandType(
    "bg",
    _BG_USAGE,
    "send your own Session, or one you created, to the background: its Window closes, its"
    " Turns go on",
    _bg,
)
# Like the Window's X: only a human closes (ADR-0020), so it is never in the default list.
CLOSE = SessionCommandType(
    "close",
    _CLOSE_USAGE,
    "close this Session's Window, as its X does; only a human",
    _close,
    initiative=_close_named,
)
OPEN = SessionCommandType(
    "open",
    _OPEN_USAGE,
    "open a Session's Window, or bring it from the background, displacing nobody; without room"
    " it asks the human for attention",
    lambda call: _open(call.args),
    initiative=_open,
)
WINDOWS = SessionCommandType(
    "windows",
    _WINDOWS_USAGE,
    "list the Sessions of this Profile: in view, in the background or closed",
    _windows,
)


# Otorgar (ADR-0025): never in the default list, since only a human gives it.
GRANT = SessionCommandType(
    GRANT_NAME,
    _GRANT_USAGE,
    "give a Session of this Profile approved functions or Command sessions; asks first",
    _grant,
)


@dataclass(frozen=True)
class LoadedSessionCommands:
    types: dict[CommandName, SessionCommandType]
    root: tuple[CommandName, ...]  # what a new Session gets unless ``/new`` names others
    problems: list[AllInError] = field(default_factory=list[AllInError])


# Main's own, when no folder is loaded (a Main in tests).
BUILT_IN = LoadedSessionCommands(
    {
        CommandName(kind.name): kind
        for kind in (
            SEND,
            SERVER,
            CONVERSATION_LOGS,
            SUBSCRIBE,
            UNSUBSCRIBE,
            ERRAND,
            BATCH,
            STATUS,
            COMPACT,
            CREATE,
            NEW,
            GRANT,
            ATTACH,
            CALL,
            WINDOWS,
            OPEN,
            BG,
            CLOSE,
        )
    },
    # @create, @new, @attach and @call widen what Sessions do: given by name, never by default; a
    # Suscripción only wakes its own Session, with its chain capped (#116), and an Encargo
    # sends no more than @send would (#117), @status only reads (#118) and @compact only touches
    # its own Conversation, which a Rewind gives back (#119). The fsource ``windows`` needs
    # @windows, @open and @bg (#122): they only read, displace nobody, and send to the
    # background only what the Session made; @close is a human's alone.
    tuple(
        CommandName(kind.name)
        for kind in (
            SEND,
            SERVER,
            CONVERSATION_LOGS,
            SUBSCRIBE,
            UNSUBSCRIBE,
            ERRAND,
            BATCH,
            STATUS,
            COMPACT,
            WINDOWS,
            OPEN,
            BG,
        )
    ),
)


def chain_allows(sessions: SessionStore, origin: Origin) -> frozenset[CommandName] | None:
    """The Command sessions every Session of ``origin``'s chain has: what it may use, the
    weakest link (ADR-0013). ``None`` for a human's, which may use any."""
    if origin.is_human:
        return None
    lists = [_listed(sessions, sid) for sid in chain_sessions(origin)]
    return lists[0].intersection(*lists[1:]) if lists else None


def lacking(sessions: SessionStore, origin: Origin, name: CommandName) -> list[str]:
    """The Sessions of ``origin``'s chain that do not have ``name`` in their list."""
    if origin.is_human:
        return []
    return [sid for sid in chain_sessions(origin) if name not in _listed(sessions, sid)]


def chain_sessions(origin: Origin) -> list[SessionId]:
    """The Sessions of ``origin``'s chain, first to last."""
    return [SessionId(link.id) for link in origin.chain if link.kind == "session" and link.id]


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
        notifications: Notifications,
    ) -> None:
        """``load`` gives a Session of this Main by id; ``run_main`` runs a Command main."""
        self._types = loaded.types
        self._sessions = sessions
        self._load = load
        self._run_main = run_main
        self._reporter = reporter
        self._notifications = notifications
        # The tools waiting for a Server their ``@server restart`` restarts to be up again.
        self._restarting: dict[ServerKey, list[Answer]] = {}

    def run(self, session_id: str, text: str, origin: Origin, answer: Answer | None = None) -> bool:
        """Run ``text``, ``@name …``, asked in ``session_id``'s Window or by a tool of its Turn
        with ``origin``; ``answer`` gets how it went. Returns whether Main keeps running."""
        answer = answer or _ignored
        try:
            _refuse_secret(text)
            session = self._load(session_id)
            name, kind = self._kind(text)
            self._check_listed(name, origin)
            args = _args(name, text)
            server = session.settings.server
            call = SessionCommandCall(session.id, args, server)
            command = _translate(name, lambda: kind.translate(call), kind.usage)
        except AllInError as error:
            return self._refused(session_id, "session_command", text, error, answer)
        self._reporter.log_session(
            session.id,
            "session_command",
            command=text,
            runs=command,
            origin=list[JsonValue](origin.labels),
        )
        restarts = server if kind is SERVER and args == "restart" else None

        def reply(result: SessionCommandResult) -> bool:
            """One nobody waits for any more reaches its Session's LLM as a Notificación, by
            its Conversation logs (ADR-0013)."""
            if not answer(result):
                told = f"{text.strip()}: {json.dumps(result.as_json(), ensure_ascii=False)}"
                self._notifications.leave(session.id, NotificationSource(f"@{name}"), told)
            return True

        def done(outcome: CommandOutcome) -> None:
            if outcome == "ok" and restarts is not None:  # it answers once its Server is back
                self._restarting.setdefault(ServerKey(restarts), []).append(reply)
            else:
                reply(_result(outcome))

        return self._run_main(command, origin, done)

    def run_initiative(
        self,
        name: AttachedName,
        text: str,
        checked: Callable[[], Origin],
        answer: Answer,
        run_main: RunMain,
    ) -> bool:
        """Run ``text``, ``@name …``, that the Proceso adherido ``name`` asks for on its own,
        as a Session's runs (ADR-0026): ``checked`` gives the Origin it runs with, or raises
        why the process may not ask, and ``run_main`` runs its Command main by its Iniciativa.
        Only those that act on its own Sessions run. Returns whether Main keeps running."""
        try:
            _refuse_secret(text)
            origin = checked()
            command, translate = self._on_its_own(text)
            self._check_listed(command, origin)
            args = _args(command, text)
            main_text = _translate(command, lambda: translate(args))
        except AllInError as error:
            return self._refused(name, "initiative", text, error, answer, name=name)
        self._reporter.log_main(
            "initiative",
            name=name,
            command=text,
            runs=main_text,
            origin=list[JsonValue](origin.labels),
        )

        def done(outcome: CommandOutcome) -> None:
            answer(_result(outcome))

        return run_main(main_text, origin, done)

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

    def _on_its_own(self, text: str) -> tuple[CommandName, Callable[[str], str]]:
        """The Command session ``text`` asks for and what it becomes when an fproc asks for it
        on its own; raises ``InitiativeRefused`` for one that never may."""
        name = CommandName(session_command_name(text) or "")
        kind = self._types.get(name)
        if kind is None or kind.initiative is None:
            allowed = [str(n) for n, k in self._types.items() if k.initiative is not None]
            raise InitiativeRefused(command=name, allowed=list[JsonValue](allowed))
        return name, kind.initiative

    def _refused(
        self,
        asker: str,
        event: str,
        text: str,
        error: AllInError,
        answer: Answer,
        **fields: JsonValue,
    ) -> bool:
        """Whoever asked hears why its Command session did not run, and Main's Chat shows it;
        of an ``@secret …`` line, never its value (#155)."""
        kept = read_secret(text).kept
        shown = f"[{asker}] {kept.strip()[:60]}: error {error}"
        self._reporter.report(None, event, shown, **fields, command=kept, result=str(error))
        answer(SessionCommandResult(error=str(error)))
        return True

    def _check_listed(self, name: CommandName, origin: Origin) -> None:
        """A human may run any; anyone else only one every Session of its chain has."""
        missing = lacking(self._sessions, origin, name)
        if missing:
            raise SessionCommandNotListed(command=name, not_in=list[JsonValue](missing))


def _refuse_secret(text: str) -> None:
    """``@secret`` is no Command session, whatever ``comsession/`` declares, nor what an
    ``@send`` sends, whoever asks: a Secreto is given in Main's Chat alone (ADR-0026, #195,
    #209)."""
    if read_secret(text).sends:
        raise SecretInMainChatOnly(usage=SECRET_USAGE)


def _args(name: CommandName, text: str) -> str:
    """What follows a Command session's name, as written."""
    return text.strip().removeprefix(f"@{name}").strip()


def _translate(name: CommandName, translate: Callable[[], str], usage: str | None = None) -> str:
    """The Command main a Command session becomes; raises ``InvalidSessionCommand`` saying why
    its arguments make none."""
    try:
        return translate()
    except ValueError as error:
        shown: dict[str, JsonValue] = {} if usage is None else {"usage": usage}
        raise InvalidSessionCommand(command=name, reason=str(error), **shown) from error


def _result(outcome: CommandOutcome) -> SessionCommandResult:
    """How a Command main went, as whoever asked for its Command session hears it."""
    match outcome:
        case "ok":
            return SessionCommandResult()
        case Queried(state=state):
            return SessionCommandResult(state=state)
        case "rejected":
            return SessionCommandResult(error="rejected")
        case AllInError() as error:
            return SessionCommandResult(error=str(error))


def _ignored(_: SessionCommandResult) -> bool:
    return True  # nobody asked to hear it
