"""``/func``: which Function tools a Session has (ADR-0005, #106).

A Session gets the Profile's approved fsources when it is born, unless ``/new -sf=off``, and the
fagents it is given there (``-fa=``) or later. Only a human widens what a Session has: adding is
a human's alone, and removing is anyone's, since it only takes away (ADR-0013). What a Session
has reaches its LLM only while it is approved: a piece back in quarantine is kept in its list,
but not offered.
"""

from collections.abc import Callable

from pydantic import JsonValue

from allin.core.errors import AllInError, InvalidArguments, PieceNotFound
from allin.core.origin import HUMAN, Origin
from allin.main.quarantine import Piece, PieceRef, Quarantine, parse_piece_ref
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore

USAGE = "/func list [sid] | /func add <sid> <piece>… | /func remove <sid> <piece>…"
_STATES = {"approved": "approved", "quarantined": "in quarantine", "rejected": "rejected"}


def only_human(args: list[str]) -> bool:
    """``/func add`` widens what a Session's LLM may do; ``args`` is the rest of the line."""
    return " ".join(args).split()[:1] == ["add"]


def session_of(args: list[str]) -> str | None:
    """The Session a ``/func`` line names, after its action."""
    words = " ".join(args).split()
    return words[1] if len(words) > 1 else None


class FuncCommands:
    def __init__(
        self,
        reporter: Reporter,
        sessions: SessionStore,
        load: Callable[[str], Session],
        quarantine: Callable[[], Quarantine],
    ) -> None:
        """``load`` gives a Session of this Main by id; ``quarantine``, the active Profile's."""
        self._reporter = reporter
        self._sessions = sessions
        self._load = load
        self._quarantine = quarantine

    def run(self, text: str = "", origin: Origin = HUMAN) -> bool:
        del origin  # who may add is checked before: ``only_human``
        match text.split():
            case [] | ["list"]:
                self._list_profile()
            case ["list", sid]:
                self._list_session(self._load(sid))
            case ["add", sid, *pieces] if pieces:
                self._change(self._load(sid), [_ref(p) for p in pieces], add=True)
            case ["remove", sid, *pieces] if pieces:
                self._change(self._load(sid), [_ref(p) for p in pieces], add=False)
            case _:
                raise InvalidArguments(usage=USAGE)
        return True

    def born_with(self, sources: bool, agents: list[str]) -> tuple[str, ...]:
        """What a new Session has: the approved fsources, unless ``sources`` is off, but the one
        that declares Otorgar, which only a human gives (ADR-0025); and the fagents given by
        package name. Raises ``PieceNotFound`` for an fagent the Profile does not have."""
        quarantine = self._quarantine()
        given = [quarantine.piece(PieceRef("agent", name)).ref.id for name in agents]
        inherited = [
            piece.ref.id
            for piece in quarantine.pieces()
            if sources
            and piece.ref.kind == "function"
            and piece.state == "approved"
            and not _grants(quarantine, piece)
        ]
        return tuple(dict.fromkeys([*inherited, *given]))

    def _list_profile(self) -> None:
        pieces = [p for p in self._quarantine().pieces() if p.ref.is_code]
        if not pieces:
            self._reporter.show(None, "no functions: put packages in functions/ or agents/")
        for piece in pieces:
            self._reporter.show(None, f"{piece.ref.id}  {_STATES[piece.state]}")

    def _list_session(self, session: Session) -> None:
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

    def _change(self, session: Session, refs: list[PieceRef], add: bool) -> None:
        if add:
            quarantine = self._quarantine()
            for ref in refs:
                quarantine.piece(ref)  # it must exist; it may wait for its Approval
            functions = tuple(dict.fromkeys([*session.functions, *(r.id for r in refs)]))
        else:
            gone = {ref.id for ref in refs}
            functions = tuple(f for f in session.functions if f not in gone)
        self._sessions.save_functions(session, functions)
        event = "functions_added" if add else "functions_removed"
        changed: list[JsonValue] = [ref.id for ref in refs]
        shown = f"functions: {', '.join(functions) or 'none'}"
        self._reporter.record(session.id, event, shown, pieces=changed)


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
