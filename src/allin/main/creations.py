"""Crear a piece (#112, ADR-0013): ``/create <sysprompt:name|mold:name> <content>``, or
``@create`` from a Session.

A human's creates the piece approved: they wrote it. One a Session asks for is always delicate,
since the piece widens what Sessions do: in the normal mode a single box shows it whole and is
both the Confirmation and the Approval, so a yes leaves it approved. When ``--devastation``
answers the Confirmation, nobody saw it: the piece is written but waits in quarantine for a
human, who approves it with ``/piece``. Either way the Profile remembers which Session wrote
its current version, and that version never goes in a bulk approval.
"""

from collections.abc import Callable

from pydantic import JsonValue

from allin.core.errors import AllInError, InvalidArguments, ProfileStorageFailed
from allin.core.files import write_atomically
from allin.core.hashing import content_hash
from allin.core.ids import SessionId
from allin.core.origin import HUMAN, Origin
from allin.core.settings import SessionSettings
from allin.main.boxes import MainBoxes
from allin.main.devastation import Devastation
from allin.main.events import CommandOutcome, OnOutcome
from allin.main.molds import LoadedMold, load_mold, load_package_mold, parse_mold
from allin.main.provenance import SessionOrigin
from allin.main.quarantine import PieceRef, Quarantine, parse_piece_ref
from allin.main.reporting import Reporter
from allin.main.sysprompt import fill_sysprompt

USAGE = "/create <sysprompt:<file>|mold:<name>> <content>"
MOLD_USAGE = "--mold=<name>"


def _ignored(_: CommandOutcome) -> None:
    pass


class PieceCreator:
    def __init__(
        self,
        quarantine: Callable[[], Quarantine],
        boxes: MainBoxes,
        devastation: Devastation,
        reporter: Reporter,
    ) -> None:
        """``quarantine``: the active Profile's."""
        self._quarantine = quarantine
        self._boxes = boxes
        self._devastation = devastation
        self._reporter = reporter

    def mold(self, name: str) -> LoadedMold:
        """The active Profile's approved Molde ``name``."""
        ref = parse_piece_ref(f"mold:{name}")
        if ref is None:
            raise InvalidArguments(usage=MOLD_USAGE)
        return load_mold(self._quarantine(), ref)

    def package_mold(self, package: PieceRef, name: str) -> LoadedMold:
        """The Molde ``name`` of an approved fproc's ``package`` (ADR-0026)."""
        return load_package_mold(self._quarantine(), package, name)

    def create(
        self, piece: str, content: str, origin: Origin = HUMAN, answer: OnOutcome = _ignored
    ) -> bool:
        """Write the piece ``piece`` names with ``content``; ``answer`` hears how it went,
        maybe once a human answered its box."""
        ref = parse_piece_ref(piece)
        if ref is None or ref.is_code or not content.strip():
            raise InvalidArguments(usage=USAGE)
        _check_content(ref, content)
        if origin.is_human:
            self._write(ref, content, origin, approve=True)
            answer("ok")
            return True
        if self._devastation.answers(origin):
            self._write(ref, content, origin, approve=False)
            shown = f"{ref.id}: created by --devastation's answer; it waits for your approval"
            self._reporter.report(None, "piece_waiting", shown, piece=ref.id)
            answer("ok")
            return True

        def on_answer(yes: bool) -> None:
            if not yes:
                answer("rejected")
                return
            try:
                self._write(ref, content, origin, approve=True)
            except AllInError as error:
                answer(error)
            else:
                answer("ok")

        exists = self._quarantine().path(ref).exists()
        verb = "replaces" if exists else "creates"
        text = f"{origin} {verb} {ref.id}:\n\n{content}\n\nCreate it, approved?"
        self._boxes.confirm(None, f"Create {ref.id}", text, on_answer)
        return True

    def _write(self, ref: PieceRef, content: str, origin: Origin, *, approve: bool) -> None:
        """Write it and record who did; approved, it is the very bytes shown, never what is
        on disk by then."""
        quarantine = self._quarantine()
        path = quarantine.path(ref)
        digest = content_hash(content.encode())
        sender = origin.sender
        created = None if sender is None else SessionOrigin(session=SessionId(sender), hash=digest)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(path, content)
            quarantine.record_creator(ref, created)
            if approve:
                quarantine.approve(ref, digest)
        except OSError as error:
            raise ProfileStorageFailed(root=str(path.parent), reason=str(error)) from error
        self._reporter.log_main(
            "piece_created",
            piece=ref.id,
            approved=approve,
            origin=list[JsonValue](origin.labels),
        )


def _check_content(ref: PieceRef, content: str) -> None:
    """Raise before anyone is asked if ``content`` is not a piece of its kind."""
    if ref.kind == "mold":
        parse_mold(ref.name, content)
    else:
        fill_sysprompt(ref.name, content, SessionSettings())
