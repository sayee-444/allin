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
from string import Template

from pydantic import JsonValue

from allin.core.errors import (
    AllInError,
    InvalidArguments,
    ProfileStorageFailed,
    SyspromptInvalid,
)
from allin.core.files import write_atomically
from allin.core.origin import HUMAN, Origin
from allin.main.boxes import MainBoxes
from allin.main.devastation import Devastation
from allin.main.events import CommandOutcome, OnOutcome
from allin.main.molds import LoadedMold, load_mold, parse_mold
from allin.main.provenance import SessionOrigin
from allin.main.quarantine import PieceRef, Quarantine, parse_piece_ref
from allin.main.reporting import Reporter

USAGE = "/create <sysprompt:<file>|mold:<name>> <content>"


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
        return load_mold(self._quarantine(), name)

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
        quarantine = self._quarantine()
        path = quarantine.path(ref)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(path, content)
        except OSError as error:
            raise ProfileStorageFailed(root=str(path.parent), reason=str(error)) from error
        written = quarantine.piece(ref)
        sender = origin.sender
        created = None if sender is None else SessionOrigin(session=sender, hash=written.hash)
        quarantine.record_creator(ref, created)
        if approve:
            quarantine.approve(ref, written.hash)
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
        return
    try:
        Template(content).substitute(assistant_name="AllIn", username="user")
    except (KeyError, ValueError) as error:
        raise SyspromptInvalid(name=ref.name, reason=f"not a valid template: {error}") from error
