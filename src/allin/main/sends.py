"""A Prompt one Session sends another (#110, ADR-0013, ADR-0010).

It goes with its chain of Origin, under a header that says who sends it, and Main never reads
``@…`` or ``/…`` in its text: its attachments and Turn parameters come in the structured form,
ahead of the text. Main is not enclosed, so it never reaches a file for a Session beyond what
that Session's tools reach: an attachment comes from the sender's Carpeta de trabajo, and one
from outside, as resolved through any link, waits for a human's Approval, which neither ``-y``
nor ``--devastation`` gives; what is copied is the very file the human saw. It is copied to ``.allin/adjuntos/<prompt>/`` in
the receiver's Carpeta de trabajo, unless it is in there already. Both ends must be in one space:
the active Profile, which is private, or one Subperfil (ADR-0010, ADR-0026). A sender that is no Session (Main, a Proceso
adherido) has no Carpeta de trabajo: every file it attaches is from outside.
"""

import re
from collections.abc import Sequence
from pathlib import Path

from pydantic import JsonValue

from allin.core.errors import (
    AllInError,
    AttachmentNotApproved,
    FileReferenceInvalid,
    InvalidArguments,
    OutsiderNotFromProcess,
    SubscriptionInvalid,
    SubscriptionNotOwn,
)
from allin.core.ids import SessionId, SubscriptionId, new_prompt_id
from allin.core.origin import Origin
from allin.main.attachments import copy_in, source_of
from allin.main.boxes import MainBoxes
from allin.main.events import OnOutcome
from allin.main.prompt_commands import SendOptions
from allin.main.prompts import Prompt, origin_header
from allin.main.sessions import Session, SessionStore
from allin.main.subprofiles import Subprofiles, acting_process
from allin.main.subscriptions import Subscriptions
from allin.main.turn_queue import TurnQueue

ME = "me"  # ``--reply-to=me``: the Session that sends, as its LLM writes it (#116)
_OUTSIDER = re.compile(r"[\w.:@+-]{1,64}")  # an Externo's identity, as ``discord:juan``
OUTSIDER_USAGE = "--outsider=<who>: up to 64 letters, digits and . : @ + - _"


class SessionSends:
    def __init__(
        self,
        sessions: SessionStore,
        turns: TurnQueue,
        boxes: MainBoxes,
        subprofiles: Subprofiles,
        subscriptions: Subscriptions,
    ) -> None:
        """``subprofiles``: the spaces of the active Profile, which only a human crosses."""
        self._sessions = sessions
        self._subscriptions = subscriptions
        self._turns = turns
        self._boxes = boxes
        self._subprofiles = subprofiles

    def send(
        self, target: Session, options: SendOptions, origin: Origin, answer: OnOutcome
    ) -> None:
        """Queue the Prompt for ``target``; ``answer`` hears how it went, maybe only once a
        human answered the Approval of a file from outside."""
        self._check_profile(target, origin)
        if options.outsider is not None:
            _check_outsider(options.outsider, origin)
        subscriber = (
            None if options.reply_to is None else self._subscriber(options.reply_to, origin)
        )
        base = self._workspace_of(origin)
        files = [(path, _source(path, base)) for path in options.attach]
        outside = [s for _, s in files if base is None or not s.is_relative_to(base.resolve())]
        if not outside:
            self._push(target, options, origin, files, subscriber)
            answer("ok")
            return

        def on_approval(yes: bool) -> None:
            try:
                if not yes:
                    raise AttachmentNotApproved(paths=list[JsonValue](map(str, outside)))
                self._check_profile(target, origin)  # the human may have switched meanwhile
                self._push(target, options, origin, files, subscriber)
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
        """Both ends are in one space, the active Profile or one Subperfil: ``/send`` never
        crosses either boundary, which only a human does (ADR-0010, ADR-0026)."""
        self._subprofiles.check_reach(origin, target.id)

    def _push(
        self,
        target: Session,
        options: SendOptions,
        origin: Origin,
        files: Sequence[tuple[str, Path]],
        subscriber: SessionId | None,
    ) -> None:
        """Copy the attachments, the very files resolved and shown, and queue the Prompt;
        its answer wakes ``subscriber``, if any (#116)."""
        prompt_id = new_prompt_id()
        attachments = copy_in(files, self._sessions.workspace(target.id), prompt_id)
        owed: tuple[SubscriptionId, ...] = ()
        if subscriber is not None:
            owed = (self._subscriptions.reply_to(subscriber, target.id, prompt_id, origin),)
        prompt = Prompt(
            target.id,
            f"{origin_header(origin, options.outsider)}\n{options.text}",  # never read for @…
            origin=origin,
            outsider=options.outsider,
            prompt_id=prompt_id,
            attachments=tuple(attachments),
            overrides=options.overrides,
            owed=owed,
        )
        self._turns.push(target, prompt)

    def _subscriber(self, reply_to: str, origin: Origin) -> SessionId:
        """The Session ``--reply-to`` wakes, by its id now (an old one is an alias): ``me`` is
        the Session that sends, which may only wake itself; of the active Profile."""
        sender = origin.sender
        if reply_to == ME:
            if sender is None:
                raise SubscriptionInvalid(event=f"--reply-to={ME}", reason="no Session sends it")
            reply_to = sender
        session = self._sessions.load(SessionId(reply_to))  # raises if there is none
        if sender is not None and sender != session.id:
            raise SubscriptionNotOwn(session_id=session.id, sender=sender)
        self._subprofiles.check_reach(origin, session.id)
        return session.id

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


def _check_outsider(outsider: str, origin: Origin) -> None:
    """Only a Proceso adherido brings an Externo's words, and names them in a way that cannot
    pass for anything else in its Prompt's header (ADR-0026)."""
    if acting_process(origin) is None:
        raise OutsiderNotFromProcess(outsider=outsider, origin=list[JsonValue](origin.labels))
    if _OUTSIDER.fullmatch(outsider) is None:
        raise InvalidArguments(usage=OUTSIDER_USAGE)


def _source(path: str, base: Path | None) -> Path:
    """The file an attachment names, resolved; a relative one from the sender's Carpeta de
    trabajo, which a sender that is no Session lacks."""
    if base is None and not Path(path).expanduser().is_absolute():
        reason = "a relative path needs the sender's Carpeta de trabajo"
        raise FileReferenceInvalid(path=path, reason=reason)
    return source_of(path, base or Path("/"))
