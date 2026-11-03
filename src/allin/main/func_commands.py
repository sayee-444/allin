"""``/func``: which Function tools a Session has (ADR-0005, #106), and Otorgar (#111).

A Session gets the Profile's approved fsources when it is born, unless ``/new -sf=off``, and the
fagents it is given there (``-fa=``) or later. Only a human widens what a Session has: adding is
a human's alone, and removing is anyone's, since it only takes away (ADR-0013). What a Session
has reaches its LLM only while it is approved: a piece back in quarantine is kept in its list,
but not offered.

Otorgar (``@grant``, ADR-0025) is the exception: a Session that has it gives any Session of the
Profile what exists and is approved there, though it lacks it, and each use asks first. Only a
human gives it, with ``/func add``: giving the fsource that declares it gives ``@grant`` too, and
``/new`` never brings either. Otorgar never gives Otorgar, so it passes to no other Session; the
Session's Window says so when it gets it and marks it on its border while it has it.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Protocol

from pydantic import JsonValue

from allin.core.errors import (
    AllInError,
    GrantNotGivable,
    InvalidArguments,
    OutsideProfile,
    PieceInQuarantine,
    PieceNotFound,
    PieceRejected,
    UnknownSessionCommand,
)
from allin.core.ids import CommandName, PieceId, SessionId
from allin.core.origin import HUMAN, Origin
from allin.decorators import GRANT as GRANT_NAME
from allin.ipc.messages import GrantHeld
from allin.main.boxes import MainBoxes
from allin.main.notifications import FUNC, Notifications, told_line
from allin.main.open_windows import OpenWindows
from allin.main.quarantine import Piece, PieceRef, PieceState, Quarantine, parse_piece_ref
from allin.main.reporting import Reporter
from allin.main.session_commands import LoadedSessionCommands, chain_allows
from allin.main.sessions import Change, Session, SessionStore

USAGE = (
    "/func list [sid] | /func add <sid> <piece|@command|attached:name>…"
    " | /func remove <sid> <piece|@command|attached:name>…"
)
ATTACHED = "attached:"  # how a line names a Proceso adherido's methods (#114)


class ChangeMethods(Protocol):
    """Gives a Session the methods of the Procesos adheridos a line names
    (``attached:<name>``), or takes them away."""

    def __call__(
        self, session: Session, named: list[str], *, given: bool, origin: Origin
    ) -> None: ...


GRANT_USAGE = "/grant <sid> <piece|@command>…"
GRANT = CommandName(GRANT_NAME)
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
        boxes: MainBoxes,
        notifications: Notifications,
        windows: OpenWindows,
        in_profile: Callable[[SessionId], bool],
        methods: ChangeMethods,
    ) -> None:
        """``load`` gives a Session of this Main by id; ``quarantine``, the active Profile's;
        ``commands``, the Command sessions Main loaded; ``in_profile``, whether a Session is
        the active Profile's."""
        self._reporter = reporter
        self._sessions = sessions
        self._load = load
        self._quarantine = quarantine
        self._commands = commands
        self._boxes = boxes
        self._notifications = notifications
        self._windows = windows
        self._in_profile = in_profile
        self._methods = methods

    def run(self, text: str = "", origin: Origin = HUMAN) -> bool:
        # Who may add is checked before (``only_human``); who did goes in the Log.
        match text.split():
            case [] | ["list"]:
                self._list_profile()
            case ["list", sid]:
                self._list_session(self._load(sid))
            case ["add", sid, *named] if named:
                named = self._methods_named(sid, named, given=True, origin=origin)
                refs, commands = self._named(named, USAGE)
                if self._gives_grant(refs):  # its approved fsource gives Otorgar too
                    commands = [*commands, GRANT]
                if refs:  # the pieces first: one that fails leaves the list as it was
                    self._add(self._load(sid), refs, origin)
                self._add_commands(self._load(sid), commands, origin)
            case ["remove", sid, *named] if named:
                named = self._methods_named(sid, named, given=False, origin=origin)
                refs, commands = self._named(named, USAGE)
                if self._may_grant(refs):  # Otorgar leaves with its fsource
                    commands = [*commands, GRANT]
                if refs:
                    self._remove(self._load(sid), refs, origin)
                self._remove_commands(self._load(sid), commands, origin)
            case _:
                raise InvalidArguments(usage=USAGE)
        return True

    def _methods_named(
        self, session_id: str, named: list[str], *, given: bool, origin: Origin
    ) -> list[str]:
        """Give or take the Procesos adheridos' methods the line names (``attached:<name>``),
        and leave the rest for the pieces and Command sessions."""
        methods = [word for word in named if word.startswith(ATTACHED)]
        if methods:
            self._methods(self._load(session_id), methods, given=given, origin=origin)
        return [word for word in named if not word.startswith(ATTACHED)]

    def grant(self, session_id: str, named: str, origin: Origin = HUMAN) -> bool:
        """``/grant <sid> <piece|@command>…``: Otorgar. It gives a Session of the active
        Profile what exists and is approved there, never Otorgar. Checked before its
        Confirmation is asked (``check_grant``) and again once answered, since the Profile
        may have changed meanwhile."""
        session, refs, commands = self._givable(session_id, named)
        if refs:
            self._add(session, refs, origin)
        self._add_commands(self._load(session.id), commands, origin)
        return True

    def check_grant(self, args: list[str]) -> None:
        """Raise what ``/grant`` would, before a human is asked to confirm it."""
        self._givable(*args)

    def _givable(
        self, session_id: str, named: str
    ) -> tuple[Session, list[PieceRef], list[CommandName]]:
        session = self._load(session_id)
        if not self._in_profile(session.id):
            raise OutsideProfile(session_id=session.id)
        refs, commands = self._named(named.split(), GRANT_USAGE)
        if not refs and not commands:
            raise InvalidArguments(usage=GRANT_USAGE)
        if GRANT in commands:
            raise GrantNotGivable(what=f"@{GRANT}")
        quarantine = self._quarantine()
        for ref in refs:
            _check_givable(quarantine, quarantine.piece(ref))
        return session, refs, commands

    def mark(self, session_id: SessionId) -> None:
        """Tell the Session's Window whether it has Otorgar, for its border."""
        try:
            held = GRANT in self._sessions.load(session_id).commands
        except AllInError as error:
            self._reporter.error(error, session_id)
            return
        self._windows.send(session_id, GrantHeld(held=held))

    def _gives_grant(self, refs: list[PieceRef]) -> bool:
        """Whether one of these is an approved fsource that declares Otorgar."""
        if not refs:
            return False
        quarantine = self._quarantine()
        pieces = [quarantine.piece(ref) for ref in refs]
        return any(p.state == "approved" and _grants(quarantine, p) for p in pieces)

    def _may_grant(self, refs: list[PieceRef]) -> bool:
        """Whether one of these may declare Otorgar: one that cannot be described, or is gone
        from the Profile, may."""
        if not refs:
            return False
        quarantine = self._quarantine()
        for ref in refs:
            try:
                if _may_grant(quarantine, quarantine.piece(ref)):
                    return True
            except PieceNotFound:
                return True
        return False

    def born_with(
        self,
        sources: bool,
        agents: list[str],
        named: Sequence[PieceId] | None = None,
        chain: Sequence[Session | None] | None = None,
    ) -> tuple[PieceId, ...]:
        """What a new Session has: the approved fsources, or those a Molde ``named``, unless
        ``sources`` is off; never one that declares Otorgar, which only a human gives
        (ADR-0025); and the fagents given by package name. Created through a ``chain`` of
        Sessions (``None`` for a human), only what every one of them has; one that is gone has
        nothing. Raises ``PieceNotFound`` for one the Profile does not have, and
        ``PieceInQuarantine`` for one a Molde names that is not approved."""
        quarantine = self._quarantine()
        given = [quarantine.piece(PieceRef("agent", name)).ref.id for name in agents]
        if named is not None:
            pieces = [quarantine.piece(_ref(piece_id, USAGE)) for piece_id in named]
            pieces = [p for p in pieces if sources or p.ref.kind != "function"]
            for piece in pieces:
                if piece.state != "approved":
                    raise PieceInQuarantine(piece=piece.ref.id)
            granting = [p.ref.id for p in pieces if _may_grant(quarantine, p)]
            if granting:  # it is still created, without them
                self._boxes.notify(
                    None,
                    "Otorgar not given",
                    f"The new Session does not get {', '.join(granting)}: only a human gives"
                    " Otorgar, with /func add.",
                )
            chosen = [p.ref.id for p in pieces if p.ref.id not in granting]
        elif sources:
            chosen = [
                piece.ref.id
                for piece in quarantine.pieces()
                if piece.ref.kind == "function"
                and piece.state == "approved"
                and not _grants(quarantine, piece)
            ]
        else:
            chosen = []
        born = _unique([*chosen, *given])
        if chain is None:
            return born
        return tuple(f for f in born if all(s is not None and f in s.functions for s in chain))

    def commands_born_with(
        self, named: list[str] | None, origin: Origin
    ) -> tuple[CommandName, ...]:
        """What Command sessions a new Session has: those at the folder's root, or those
        ``named``; one Main does not have is left out with an Aviso. Created through a chain of
        Sessions, only what every one of them has, as none passes on more (ADR-0025)."""
        wanted = list(self._commands.root) if named is None else [CommandName(n) for n in named]
        if GRANT in wanted:  # only a human gives it, to a Session that exists (ADR-0025)
            wanted.remove(GRANT)
            self._boxes.notify(
                None,
                "Otorgar not given",
                "A new Session never gets @grant: give it with /func add <sid> @grant.",
            )
        missing = [name for name in wanted if name not in self._commands.types]
        if missing:  # it is still created, without them
            shown = _shown(missing)
            self._boxes.notify(
                None,
                "Command sessions missing",
                f"The new Session does not get {shown}: no module on this machine declares them.",
            )
        allowed = chain_allows(self._sessions, origin)
        listed = [
            name
            for name in wanted
            if name in self._commands.types and (allowed is None or name in allowed)
        ]
        return tuple(dict.fromkeys(listed))

    def _named(self, named: list[str], usage: str) -> tuple[list[PieceRef], list[CommandName]]:
        """The pieces and the Command sessions (``@name``) a line names; raises before
        anything changes if one of either does not exist, with ``usage`` for a word that is
        neither."""
        commands = [CommandName(n.removeprefix("@")) for n in named if n.startswith("@")]
        unknown = [name for name in commands if name not in self._commands.types]
        if unknown:
            raise UnknownSessionCommand(command=unknown[0])
        return [_ref(n, usage) for n in named if not n.startswith("@")], commands

    def _add_commands(self, session: Session, names: list[CommandName], origin: Origin) -> None:
        if not names:
            return
        listed = tuple(dict.fromkeys([*session.commands, *names]))
        self._save_commands(session, listed, "commands_added", names, origin)
        self._tell(session, [f"@{n}" for n in names if n not in session.commands], given=True)
        if GRANT in names and GRANT not in session.commands:
            text = (
                "This Session may now give any Session of the Profile its approved functions"
                " and Command sessions with @grant; each use asks you first."
            )
            self._boxes.notify(session.id, "Otorgar given", text)
            self.mark(session.id)

    def _remove_commands(self, session: Session, names: list[CommandName], origin: Origin) -> None:
        if not names:
            return
        listed = tuple(name for name in session.commands if name not in names)
        self._save_commands(session, listed, "commands_removed", names, origin)
        self._tell(session, [f"@{n}" for n in session.commands if n in names], given=False)
        if GRANT in names and GRANT in session.commands:
            self.mark(session.id)

    def _save_commands(
        self,
        session: Session,
        listed: tuple[CommandName, ...],
        event: str,
        names: list[CommandName],
        origin: Origin,
    ) -> None:
        self._sessions.save_commands(session, listed)
        shown = f"Command sessions: {_shown(listed) or 'none'}"
        self._reporter.record(
            session.id,
            event,
            shown,
            commands=list[JsonValue](names),
            origin=list[JsonValue](origin.labels),
        )

    def _list_profile(self) -> None:
        pieces = [p for p in self._quarantine().pieces() if p.ref.is_code]
        if not pieces:
            self._reporter.show(None, "no functions: put packages in functions/ or agents/")
        for piece in pieces:
            self._reporter.show(None, f"{piece.ref.id}  {_STATES[piece.state]}")

    def _list_session(self, session: Session) -> None:
        self._reporter.show(session.id, f"Command sessions: {_shown(session.commands) or 'none'}")
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

    def _add(self, session: Session, refs: list[PieceRef], origin: Origin) -> None:
        quarantine = self._quarantine()
        for ref in refs:
            quarantine.piece(ref)  # it must exist; it may wait for its Approval
        functions = _unique([*session.functions, *(ref.id for ref in refs)])
        self._save(session, functions, "functions_added", refs, origin)
        self._tell(session, [r.id for r in refs if r.id not in session.functions], given=True)

    def _remove(self, session: Session, refs: list[PieceRef], origin: Origin) -> None:
        gone = {ref.id for ref in refs}
        functions = tuple(f for f in session.functions if f not in gone)
        self._save(session, functions, "functions_removed", refs, origin)
        self._tell(session, [f for f in session.functions if f in gone], given=False)

    def _tell(self, session: Session, subjects: Iterable[str], *, given: bool) -> None:
        """Leave the Session a Notificación for each piece or Command session (``@send``) it
        was given or lost (#109)."""
        for subject in dict.fromkeys(subjects):
            change = Change(subject=subject, given=given)
            self._notifications.leave(session.id, FUNC, told_line(change), change)

    def _save(
        self,
        session: Session,
        functions: tuple[PieceId, ...],
        event: str,
        refs: list[PieceRef],
        origin: Origin,
    ) -> None:
        self._sessions.save_functions(session, functions)
        changed: list[JsonValue] = [ref.id for ref in refs]
        shown = f"functions: {', '.join(functions) or 'none'}"
        origin_labels = list[JsonValue](origin.labels)
        self._reporter.record(session.id, event, shown, pieces=changed, origin=origin_labels)


def _shown(names: Iterable[CommandName]) -> str:
    return ", ".join(f"@{name}" for name in names)


def _unique(ids: list[PieceId]) -> tuple[PieceId, ...]:
    return tuple(dict.fromkeys(ids))  # in order, each once


def _ref(text: str, usage: str) -> PieceRef:
    ref = parse_piece_ref(text)
    if ref is None or not ref.is_code:
        raise InvalidArguments(usage=usage)
    return ref


def _check_givable(quarantine: Quarantine, piece: Piece) -> None:
    """Otorgar gives only an approved piece that does not declare it: one it cannot tell
    about, since it cannot be described, is not given (ADR-0025)."""
    if piece.state == "quarantined":
        raise PieceInQuarantine(piece=piece.ref.id)
    if piece.state == "rejected":
        raise PieceRejected(piece=piece.ref.id)
    if _may_grant(quarantine, piece):
        raise GrantNotGivable(what=piece.ref.id)


def _may_grant(quarantine: Quarantine, piece: Piece) -> bool:
    """Whether it declares Otorgar, or cannot be described to tell."""
    return any(
        isinstance(described, AllInError) or described.reach.grants
        for described in quarantine.describe(piece).values()
    )


def _grants(quarantine: Quarantine, piece: Piece) -> bool:
    return any(
        not isinstance(described, AllInError) and described.reach.grants
        for described in quarantine.describe(piece).values()
    )
