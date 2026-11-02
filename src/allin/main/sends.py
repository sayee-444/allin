"""A Prompt one Session sends another (#110, ADR-0013, ADR-0010).

It goes with its chain of Origin, under a header that says who sends it, and Main never reads
``@…`` or ``/…`` in its text: its attachments and Turn parameters come in the structured form,
ahead of the text. Main is not enclosed, so it never reaches a file for a Session beyond what
that Session's tools reach: an attachment comes from the sender's Carpeta de trabajo, and one
from outside, as resolved through any link, waits for a human's Approval, which neither ``-y``
nor ``--devastation`` gives; what is copied is the very file the human saw. It is copied to ``.allin/adjuntos/<prompt>/`` in
the receiver's Carpeta de trabajo, unless it is in there already. Both ends must be Sessions of
the active Profile, which is private (ADR-0010). A sender that is no Session (Main, a Proceso
adherido) has no Carpeta de trabajo: every file it attaches is from outside.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import JsonValue

from allin.core.errors import (
    AllInError,
    AttachmentNotApproved,
    FileReferenceInvalid,
    OutsideProfile,
)
from allin.core.ids import SessionId, new_prompt_id
from allin.core.origin import Origin
from allin.main.attachments import copy_in, source_of
from allin.main.boxes import MainBoxes
from allin.main.events import OnOutcome
from allin.main.prompt_commands import SendOptions
from allin.main.prompts import Prompt, origin_header
from allin.main.sessions import Session, SessionStore
from allin.main.turn_queue import TurnQueue


class SessionSends:
    def __init__(
        self,
        sessions: SessionStore,
        turns: TurnQueue,
        boxes: MainBoxes,
        in_profile: Callable[[SessionId], bool],
    ) -> None:
        """``in_profile``: whether a Session is one of the active Profile's."""
        self._sessions = sessions
        self._turns = turns
        self._boxes = boxes
        self._in_profile = in_profile

    def send(
        self, target: Session, options: SendOptions, origin: Origin, answer: OnOutcome
    ) -> None:
        """Queue the Prompt for ``target``; ``answer`` hears how it went, maybe only once a
        human answered the Approval of a file from outside."""
        self._check_profile(target, origin)
        base = self._workspace_of(origin)
        files = [(path, _source(path, base)) for path in options.attach]
        outside = [s for _, s in files if base is None or not s.is_relative_to(base.resolve())]
        if not outside:
            self._push(target, options, origin, files)
            answer("ok")
            return

        def on_approval(yes: bool) -> None:
            try:
                if not yes:
                    raise AttachmentNotApproved(paths=list[JsonValue](map(str, outside)))
                self._check_profile(target, origin)  # the human may have switched meanwhile
                self._push(target, options, origin, files)
            except AllInError as error:
                answer(error)
            else:
                answer("ok")

        sender = origin.sender or str(origin)
        shown = "\n".join(map(str, outside))
        text = (
            f"{sender} attaches, to its Prompt for {target.id}, from outside its folder:\n{shown}"
        )
        self._boxes.confirm(None, "Approve files", f"{text}\nCopy them?", on_approval)

    def _check_profile(self, target: Session, origin: Origin) -> None:
        """Neither end may be out of the active Profile: ``/send`` never crosses it."""
        sender = self._sending_session(origin)
        for session_id in (target.id, *([sender] if sender is not None else [])):
            if not self._in_profile(session_id):
                raise OutsideProfile(session_id=session_id)

    def _push(
        self,
        target: Session,
        options: SendOptions,
        origin: Origin,
        files: Sequence[tuple[str, Path]],
    ) -> None:
        """Copy the attachments, the very files resolved and shown, and queue the Prompt."""
        prompt_id = new_prompt_id() if files else ""
        attachments = copy_in(files, self._sessions.workspace(target.id), prompt_id)
        prompt = Prompt(
            target.id,
            f"{origin_header(origin)}\n{options.text}",  # data: never read for @… or /…
            origin=origin,
            prompt_id=prompt_id,
            attachments=tuple(attachments),
            overrides=options.overrides,
        )
        self._turns.push(target, prompt)

    def _sending_session(self, origin: Origin) -> SessionId | None:
        """The Session that sends, if one of this store does."""
        sender = origin.sender
        if sender is None or SessionId(sender) not in self._sessions.ids():
            return None
        return SessionId(sender)

    def _workspace_of(self, origin: Origin) -> Path | None:
        """The Carpeta de trabajo of the Session that sends; none if no Session does."""
        sender = self._sending_session(origin)
        return None if sender is None else self._sessions.workspace(sender)


def _source(path: str, base: Path | None) -> Path:
    """The file an attachment names, resolved; a relative one from the sender's Carpeta de
    trabajo, which a sender that is no Session lacks."""
    if base is None and not Path(path).expanduser().is_absolute():
        reason = "a relative path needs the sender's Carpeta de trabajo"
        raise FileReferenceInvalid(path=path, reason=reason)
    return source_of(path, base or Path("/"))
