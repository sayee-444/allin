"""``/func``: which Function tools a Session has (ADR-0005, #106).

A Session gets the Profile's approved fsources when it is born, unless ``/new -sf=off``, and the
fagents it is given there (``-fa=``) or later. Only a human widens what a Session has: adding is
a human's alone, and removing is anyone's, since it only takes away (ADR-0013). What a Session
has reaches its LLM only while it is approved: a piece back in quarantine is kept in its list,
but not offered.
"""

from collections.abc import Callable

from pydantic import JsonValue

from allin.core.errors import (
    AllInError,
    InvalidArguments,
    PieceNotFound,
    UnknownSessionCommand,
)
from allin.core.ids import PieceId, SessionId
from allin.core.origin import HUMAN, Origin
from allin.main.quarantine import Piece, PieceRef, PieceState, Quarantine, parse_piece_ref
from allin.main.reporting import Reporter
from allin.main.session_commands import LoadedSessionCommands
from allin.main.sessions import Session, SessionStore

USAGE = (
    "/func list [sid] | /func add <sid> <piece|@command>… | /func remove <sid> <piece|@command>…"
)
_STATES: dict[PieceState, str] = {
    "approved": "approved",
    "quarantined": "in quarantine",
    "rejected": "rejected",
}


def _words(args: list[str]) -> list[str]:
    return " ".join(args).split()  # ``args`` is the rest of the line, as one word


def only_human(args: list[str]) -> bool:
    """``/func add`` widens what a Session's LLM may do."""
    return _words(args)[:1] == ["add"]


def session_of(args: list[str]) -> str | None:
    """The Session a ``/func`` line names, after its action."""
    words = _words(args)
    return words[1] if len(words) > 1 else None


class FuncCommands:
    def __init__(
        self,
        reporter: Reporter,
        sessions: SessionStore,
        load: Callable[[str], Session],
        quarantine: Callable[[], Quarantine],
        commands: LoadedSessionCommands,
        notify: Callable[[str, str], object],
    ) -> None:
        """``load`` gives a Session of this Main by id; ``quarantine``, the active Profile's;
        ``commands``, the Command sessions Main loaded; ``notify`` shows an Aviso with a title
        and a text in Main's Chat."""
        self._reporter = reporter
        self._sessions = sessions
        self._load = load
        self._quarantine = quarantine
        self._commands = commands
        self._notify = notify

    def run(self, text: str = "", origin: Origin = HUMAN) -> bool:
        del origin  # who may add is checked before: ``only_human``
        match text.split():
            case [] | ["list"]:
                self._list_profile()
            case ["list", sid]:
                self._list_session(self._load(sid))
            case ["add", sid, *named] if named:
                pieces, commands = _split(named)
                refs = [_ref(p) for p in pieces]
                self._add_commands(self._load(sid), commands)
                if refs:
                    self._add(self._load(sid), refs)
            case ["remove", sid, *named] if named:
                pieces, commands = _split(named)
                refs = [_ref(p) for p in pieces]
                self._remove_commands(self._load(sid), commands)
                if refs:
                    self._remove(self._load(sid), refs)
            case _:
                raise InvalidArguments(usage=USAGE)
        return True

    def born_with(self, sources: bool, agents: list[str]) -> tuple[PieceId, ...]:
        """What a new Session has: the approved fsources, unless ``sources`` is off, but the one
        that declares Otorgar, which only a human gives (ADR-0025); and the fagents given by
        package name. Raises ``PieceNotFound`` for an fagent the Profile does not have."""
        quarantine = self._quarantine()
        given = [quarantine.piece(PieceRef("agent", name)).ref.id for name in agents]
        if not sources:
            return _unique(given)
        inherited = [
            piece.ref.id
            for piece in quarantine.pieces()
            if piece.ref.kind == "function"
            and piece.state == "approved"
            and not _grants(quarantine, piece)
        ]
        return _unique([*inherited, *given])

    def commands_born_with(self, named: list[str] | None, origin: Origin) -> tuple[str, ...]:
        """What Command sessions a new Session has: those at the folder's root, or those
        ``named``; one Main does not have is left out with an Aviso. Created by a Session, only
        what that one has too, since it passes on no more than it has (ADR-0025)."""
        wanted = list(self._commands.root) if named is None else named
        missing = [name for name in wanted if name not in self._commands.types]
        if missing:  # it is still created, without them
            shown = ", ".join(f"@{name}" for name in missing)
            self._notify(
                "Command sessions missing",
                f"The new Session does not get {shown}: no module on this machine declares them.",
            )
        listed = [name for name in wanted if name in self._commands.types]
        sender = origin.sender
        if sender is not None and SessionId(sender) in self._sessions.ids():
            has = set(self._sessions.load(sender).commands)
            listed = [name for name in listed if name in has]
        return tuple(dict.fromkeys(listed))

    def _add_commands(self, session: Session, names: list[str]) -> None:
        if not names:
            return
        unknown = [name for name in names if name not in self._commands.types]
        if unknown:
            raise UnknownSessionCommand(command=unknown[0])
        listed = tuple(dict.fromkeys([*session.commands, *names]))
        self._save_commands(session, listed, "commands_added", names)

    def _remove_commands(self, session: Session, names: list[str]) -> None:
        if not names:
            return
        listed = tuple(name for name in session.commands if name not in names)
        self._save_commands(session, listed, "commands_removed", names)

    def _save_commands(
        self, session: Session, listed: tuple[str, ...], event: str, names: list[str]
    ) -> None:
        self._sessions.save_commands(session, listed)
        shown = f"Command sessions: {', '.join(f'@{name}' for name in listed) or 'none'}"
        self._reporter.record(session.id, event, shown, commands=list[JsonValue](names))

    def _list_profile(self) -> None:
        pieces = [p for p in self._quarantine().pieces() if p.ref.is_code]
        if not pieces:
            self._reporter.show(None, "no functions: put packages in functions/ or agents/")
        for piece in pieces:
            self._reporter.show(None, f"{piece.ref.id}  {_STATES[piece.state]}")

    def _list_session(self, session: Session) -> None:
        listed = ", ".join(f"@{name}" for name in session.commands) or "none"
        self._reporter.show(session.id, f"Command sessions: {listed}")
        if not session.functions:
            self._reporter.show(session.id, "no functions")
        quarantine = self._quarantine()
        for piece_id in session.functions:
            ref = parse_piece_ref(piece_id)
            try:
                state = _STATES[quarantine.piece(ref).state] if ref is not None else "unknown"
            except PieceNotFound:
                state = "gone from the Profile"
            self._reporter.show(session.id, f"{piece_id}  {state}")

    def _add(self, session: Session, refs: list[PieceRef]) -> None:
        quarantine = self._quarantine()
        for ref in refs:
            quarantine.piece(ref)  # it must exist; it may wait for its Approval
        functions = _unique([*session.functions, *(ref.id for ref in refs)])
        self._save(session, functions, "functions_added", refs)

    def _remove(self, session: Session, refs: list[PieceRef]) -> None:
        gone = {ref.id for ref in refs}
        functions = tuple(f for f in session.functions if f not in gone)
        self._save(session, functions, "functions_removed", refs)

    def _save(
        self, session: Session, functions: tuple[PieceId, ...], event: str, refs: list[PieceRef]
    ) -> None:
        self._sessions.save_functions(session, functions)
        changed: list[JsonValue] = [ref.id for ref in refs]
        shown = f"functions: {', '.join(functions) or 'none'}"
        self._reporter.record(session.id, event, shown, pieces=changed)


def _split(named: list[str]) -> tuple[list[str], list[str]]:
    """The pieces and the Command sessions (``@name``) a ``/func`` line names."""
    commands = [name.removeprefix("@") for name in named if name.startswith("@")]
    return [name for name in named if not name.startswith("@")], commands


def _unique(ids: list[PieceId]) -> tuple[PieceId, ...]:
    return tuple(dict.fromkeys(ids))  # in order, each once


def _ref(text: str) -> PieceRef:
    ref = parse_piece_ref(text)
    if ref is None or not ref.is_code:
        raise InvalidArguments(usage=USAGE)
    return ref


def _grants(quarantine: Quarantine, piece: Piece) -> bool:
    return any(
        not isinstance(described, AllInError) and described.reach.grants
        for described in quarantine.describe(piece).values()
    )
