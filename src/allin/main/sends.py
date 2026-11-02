"""A Prompt one Session sends another (#110, ADR-0013, ADR-0010).

It goes with its chain of Origin, under a header that says who sends it, and Main never reads
``@…`` or ``/…`` in its text: its attachments and Turn parameters come in the structured form,
ahead of the text. Main is not enclosed, so it never reaches a file for a Session beyond what
that Session's tools reach: an attachment comes from the sender's Carpeta de trabajo, and one
from outside, as resolved through any link, waits for a human's Approval, which neither ``-y``
nor ``--devastation`` gives. It is copied, as it is then, to ``.allin/adjuntos/<prompt>/`` in
the receiver's Carpeta de trabajo, unless it is in there already. A Session only reaches the
Sessions of the active Profile, which is private (ADR-0010).
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import JsonValue

from allin.core.errors import AttachmentNotApproved, FileReferenceInvalid, OutsideProfile
from allin.core.ids import SessionId, new_prompt_id
from allin.core.origin import Origin
from allin.main.attachments import attach, source_of
from allin.main.boxes import MainBoxes
from allin.main.events import CommandOutcome
from allin.main.prompt_commands import SendOptions
from allin.main.prompts import Prompt, origin_header
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.turn_queue import TurnQueue


class SessionSends:
    def __init__(
        self,
        sessions: SessionStore,
        turns: TurnQueue,
        boxes: MainBoxes,
        reporter: Reporter,
        in_profile: Callable[[SessionId], bool],
    ) -> None:
        """``in_profile``: whether a Session is one of the active Profile's."""
        self._sessions = sessions
        self._turns = turns
        self._boxes = boxes
        self._reporter = reporter
        self._in_profile = in_profile

    def send(
        self,
        target: Session,
        options: SendOptions,
        origin: Origin,
        answer: Callable[[CommandOutcome], None],
    ) -> None:
        """Queue the Prompt for ``target``; ``answer`` hears how it went, maybe only once a
        human answered the Approval of a file from outside."""
        if not self._in_profile(target.id):
            raise OutsideProfile(session_id=target.id)
        base = self._workspace_of(origin)
        sources = _sources(options.attach, base)
        outside = [s for s in sources if base is None or not s.is_relative_to(base.resolve())]
        if not outside:
            self._push(target, options, origin, base, sources)
            answer("ok")
            return

        def approved(yes: bool) -> None:
            if not yes:
                refused = AttachmentNotApproved(paths=list[JsonValue](map(str, outside)))
                self._reporter.record(target.id, "attachment_refused", f"not sent: {refused}")
                answer(refused)
                return
            try:
                self._push(target, options, origin, base, sources)
            except FileReferenceInvalid as error:
                answer(error)
            else:
                answer("ok")

        sender = origin.sender or str(origin)
        files = "\n".join(map(str, outside))
        text = (
            f"{sender} attaches, to its Prompt for {target.id}, from outside its folder:\n{files}"
        )
        self._boxes.confirm(None, "Approve files", f"{text}\nCopy them?", approved)

    def _push(
        self,
        target: Session,
        options: SendOptions,
        origin: Origin,
        base: Path | None,
        approved: Sequence[Path],
    ) -> None:
        """Copy the attachments, as they are now, and queue the Prompt. A file that changed
        where it leads since it was approved is not copied."""
        if _sources(options.attach, base) != list(approved):
            raise FileReferenceInvalid(
                path=", ".join(options.attach), reason="it leads elsewhere since it was approved"
            )
        prompt_id = new_prompt_id() if approved else ""
        workspace = self._sessions.workspace(target.id)
        cwd = base or Path("/")
        _, attachments = attach([str(source) for source in approved], workspace, prompt_id, cwd)
        prompt = Prompt(
            target.id,
            f"{origin_header(origin)}\n{options.text}",  # data: never read for @… or /…
            origin=origin,
            prompt_id=prompt_id,
            attachments=tuple(attachments),
            overrides=options.overrides,
        )
        self._turns.push(target, prompt)

    def _workspace_of(self, origin: Origin) -> Path | None:
        """The Carpeta de trabajo of the Session that sends, if a Session of this store does."""
        sender = origin.sender
        if sender is None or SessionId(sender) not in self._sessions.ids():
            return None
        return self._sessions.workspace(SessionId(sender))


def _sources(references: Sequence[str], base: Path | None) -> list[Path]:
    """The files the attachments name, resolved; a relative one from the sender's Carpeta de
    trabajo, which a sender that is no Session lacks."""
    sources: list[Path] = []
    for reference in references:
        if base is None and not Path(reference).expanduser().is_absolute():
            reason = "a relative path needs the sender's Carpeta de trabajo"
            raise FileReferenceInvalid(path=reference, reason=reason)
        sources.append(source_of(reference, base or Path("/")))
    return sources
