"""Notificaciones: the lines Main leaves pending for a Session's next Prompt (ADR-0013).

A command, an fsource (through the Command main it runs) or an event of Main leaves one. It
goes in its Session's Log and Chat at once, and, unless the Session's Conversation logs turn
its source off, waits in the Session until its next Prompt: then it opens that Prompt, under
``[LOGS @COMMANDS]``, in the same ``user`` message, never as ``developer``, since what it tells
of other Sessions is data.

Two that undo each other before any Turn told them cancel out: giving a function and taking it
away again leaves nothing. A Turn tells the ones pending when it starts; they leave the Session
once it is saved, so a Turn that fails or is suspended tells them again.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue, TypeAdapter, ValidationError

from allin.core.cut import CutReason
from allin.core.errors import InvalidArguments
from allin.core.ids import NotificationSource, SessionId
from allin.core.settings import ConversationLogs
from allin.main.chat_rebuild import logs_entry
from allin.main.conversation import Conversation
from allin.main.cut_turns import cut_line, last_cut
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.sessions import Change, Notification, Session, SessionStore

# The line a Turn interrupted with Shift+Enter leaves for the next Prompt.
INTERRUPTED = NotificationSource("interrupted")
FUNC = NotificationSource("/func")  # functions and Command sessions given or taken away
CALL = NotificationSource("call")  # a human's @call of a Proceso adherido's method (#114)
SUBSCRIPTION = NotificationSource("subscription")  # a Suscripción's chain cut (#116)
ERRAND = NotificationSource("errand")  # how an Encargo goes: "2 of 3 answered" (#117)
# What a Session still has open after a Compactación, which its summary may have lost (#119).
COMPACT = NotificationSource("compact")
# That its last answer reached its Output cap or filled the slot (#120).
CUT = NotificationSource("cut")
# How much Context is left, once its Notice shows (#120).
CONTEXT_LEFT = NotificationSource("context")
# The sources Main itself leaves Notificaciones from, listed even before any did.
SOURCES = (FUNC, CALL, INTERRUPTED, CUT, CONTEXT_LEFT, SUBSCRIPTION, ERRAND, COMPACT)
USAGE = "/conversation_logs <sid> on|off|list [source]"
_ACTION = TypeAdapter[Literal["on", "off", "list"]](Literal["on", "off", "list"])


def told_line(change: Change) -> str:
    """How a Notificación tells the LLM what it was given or lost."""
    command = change.subject.startswith("@")
    if change.given:
        what = "your tools may run it" if command else "its tools are yours"
        return f"{change.subject} was given to you: {what}"
    what = "your tools may no longer run it" if command else "its tools are no longer yours"
    return f"{change.subject} was taken away: {what}"


@dataclass(frozen=True)
class Told:
    """The pending Notificaciones a Turn opens its Prompt with."""

    ids: tuple[int, ...]
    lines: tuple[str, ...]


NOTHING_TOLD = Told((), ())


class Notifications:
    def __init__(self, sessions: SessionStore, reporter: Reporter, windows: OpenWindows) -> None:
        self._sessions = sessions
        self._reporter = reporter
        self._windows = windows
        # What each Session's running or suspended Turn told: those never cancel out any more,
        # and a suspended one opens its Prompt with them again when it goes on.
        self._told: dict[SessionId, Told] = {}

    def leave(
        self,
        session_id: SessionId,
        source: NotificationSource,
        text: str,
        change: Change | None = None,
    ) -> None:
        """Leave a Notificación for the Session's next Prompt; it shows in its Log and Chat
        whether or not its Conversation logs let it in."""
        session = self._sessions.load(session_id)
        enters = session.settings.conversation_logs.enters(source)
        self._reporter.log_session(
            session.id, "notification", source=source, text=text, conversation=enters
        )
        self._windows.send(session.id, logs_entry(text))
        if not enters:
            return
        pending = list(session.notifications)
        undone = self._undone(session.id, pending, change)
        if undone is not None:
            pending.remove(undone)
            texts: list[JsonValue] = [undone.text, text]
            self._reporter.log_session(session.id, "notifications_cancelled", texts=texts)
        else:
            number = max((n.id for n in pending), default=0) + 1
            pending.append(Notification(id=number, source=source, text=text, change=change))
        self._sessions.save_notifications(session, tuple(pending))

    def _undone(
        self, session_id: SessionId, pending: Sequence[Notification], change: Change | None
    ) -> Notification | None:
        """The pending one ``change`` undoes, if no running Turn told it yet."""
        if change is None:
            return None
        told = self._told.get(session_id, NOTHING_TOLD).ids
        opposite = change.model_copy(update={"given": not change.given})
        return next((n for n in pending if n.change == opposite and n.id not in told), None)

    def opening(self, session: Session, conversation: Conversation, told: Told) -> list[str]:
        """What a Prompt of the Session opens with: that its last Turn was cut, then ``told``,
        as far as its Conversation logs let each in."""
        note = last_cut(conversation)
        enters = note is not None and session.settings.conversation_logs.enters(
            _cut_source(note.reason)
        )
        return [cut_line(note), *told.lines] if note is not None and enters else list(told.lines)

    def pending(self, session_id: SessionId) -> Told:
        """What its next Prompt would open with now; nothing is marked told."""
        if session_id in self._told:  # a suspended Turn's, which goes on with them
            return self._told[session_id]
        pending = self._sessions.load(session_id).notifications
        return Told(tuple(n.id for n in pending), tuple(n.text for n in pending))

    def tell(self, session_id: SessionId) -> Told:
        """What its Turn starting now opens its Prompt with: those no longer cancel out. A
        suspended Turn going on gets what it told before."""
        told = self.pending(session_id)
        self._told[session_id] = told
        return told

    def saved(self, session_id: SessionId, told: Told) -> None:
        """The Turn that told them is in the Conversation: they leave the Session."""
        self._told.pop(session_id, None)
        if not told.ids:
            return
        session = self._sessions.load(session_id)
        left = tuple(n for n in session.notifications if n.id not in told.ids)
        self._sessions.save_notifications(session, left)

    def withdraw(self, session_id: SessionId, source: NotificationSource) -> None:
        """Newer news replaces the pending ones of ``source`` no running Turn told yet: how
        much Context is left, once the Context changed (#120)."""
        session = self._sessions.load(session_id)
        told = self._told.get(session_id, NOTHING_TOLD).ids
        left = tuple(n for n in session.notifications if n.source != source or n.id in told)
        if len(left) != len(session.notifications):
            self._sessions.save_notifications(session, left)

    def not_saved(self, session_id: SessionId) -> None:
        """The Turn that told them failed, was cancelled or suspended: they wait for the next."""
        self._told.pop(session_id, None)

    def run(self, session_id: str, action: str, source: str | None = None) -> bool:
        """``/conversation_logs <sid> on|off|list [source]``: whether a source's Notificaciones,
        or every source's, enter the Conversation; never the Log or the Chat."""
        try:
            chosen = _ACTION.validate_python(action)
        except ValidationError as error:
            raise InvalidArguments(usage=USAGE) from error
        session = self._sessions.load(session_id)
        logs = session.settings.conversation_logs
        if chosen == "list":
            for name in sorted({*SOURCES, *logs.sources}):
                self._reporter.show(session.id, f"{name}: {'on' if logs.enters(name) else 'off'}")
            return True
        on = chosen == "on"
        changed = (
            ConversationLogs(every=on)  # every source, those set on their own too
            if source is None
            else logs.model_copy(update={"sources": {**logs.sources, source: on}})
        )
        settings = session.settings.model_copy(update={"conversation_logs": changed})
        session = self._sessions.save_settings(session, settings)
        # What waits from a source now off stays in the Log and the Chat only.
        told = self._told.get(session.id, NOTHING_TOLD).ids
        kept = tuple(n for n in session.notifications if changed.enters(n.source) or n.id in told)
        self._sessions.save_notifications(session, kept)
        shown = f"conversation logs {chosen}" + (f" for {source}" if source else "")
        self._reporter.record(session.id, "conversation_logs", shown, action=chosen, source=source)
        return True


def _cut_source(reason: CutReason) -> NotificationSource:
    """Which Conversation logs source lets a cut Turn's line in."""
    match reason:
        case "interrupted":
            return INTERRUPTED
        case "output_cap" | "slot_limit":
            return CUT
