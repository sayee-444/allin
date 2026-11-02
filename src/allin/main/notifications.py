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

from dataclasses import dataclass

from pydantic import JsonValue

from allin.core.errors import InvalidArguments
from allin.core.ids import SessionId
from allin.core.settings import SessionSettings
from allin.main.chat_rebuild import logs_entry
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.sessions import Notification, Session, SessionStore

ALL = "all"  # the Conversation logs key every source without its own follows
INTERRUPTED = "interrupted"  # the source of the line a Turn interrupted with Shift+Enter leaves
FUNC = "/func"  # functions and Command sessions given or taken away
# The sources Main itself leaves Notificaciones from, listed even before any did.
SOURCES = (FUNC, INTERRUPTED)
USAGE = "/conversation_logs <sid> on|off|list [source]"


@dataclass(frozen=True)
class Told:
    """The Notificaciones a Turn opens its Prompt with."""

    ids: tuple[int, ...]
    lines: tuple[str, ...]


NOTHING_TOLD = Told((), ())


def enters(settings: SessionSettings, source: str) -> bool:
    """Whether the Notificaciones of ``source`` enter the Session's Conversation."""
    logs = settings.conversation_logs
    return logs.get(source, logs.get(ALL, True))


class Notifications:
    def __init__(self, sessions: SessionStore, reporter: Reporter, windows: OpenWindows) -> None:
        self._sessions = sessions
        self._reporter = reporter
        self._windows = windows
        # What each Session's running Turn told: those never cancel out any more.
        self._told: dict[SessionId, frozenset[int]] = {}

    def leave(
        self,
        session_id: SessionId,
        source: str,
        text: str,
        subject: str | None = None,
        *,
        added: bool = True,
    ) -> None:
        """Leave a Notificación for the Session's next Prompt; it shows in its Log and Chat
        whether or not its Conversation logs let it in."""
        session = self._sessions.load(session_id)
        enters_it = enters(session.settings, source)
        self._reporter.log_session(
            session.id, "notification", source=source, text=text, conversation=enters_it
        )
        self._windows.send(session.id, logs_entry(text))
        if not enters_it:
            return
        told = self._told.get(session.id, frozenset())
        pending = list(session.notifications)
        undone = next(
            (
                n
                for n in pending
                if subject is not None
                and n.subject == subject
                and n.added != added
                and n.id not in told
            ),
            None,
        )
        if undone is not None:
            pending.remove(undone)
            self._reporter.log_session(
                session.id, "notifications_cancelled", texts=list[JsonValue]([undone.text, text])
            )
        else:
            number = max((n.id for n in pending), default=0) + 1
            pending.append(
                Notification(id=number, source=source, text=text, subject=subject, added=added)
            )
        self._sessions.save_notifications(session, tuple(pending))

    def pending(self, session: Session) -> Told:
        """What its next Prompt would open with now; nothing is marked told."""
        pending = self._sessions.load(session.id).notifications
        return Told(tuple(n.id for n in pending), tuple(n.text for n in pending))

    def tell(self, session: Session) -> Told:
        """What its Turn starting now opens its Prompt with: those no longer cancel out."""
        told = self.pending(session)
        self._told[session.id] = frozenset(told.ids)
        return told

    def saved(self, session_id: SessionId, told: Told) -> None:
        """The Turn that told them is in the Conversation: they leave the Session."""
        self._told.pop(session_id, None)
        if not told.ids:
            return
        session = self._sessions.load(session_id)
        left = tuple(n for n in session.notifications if n.id not in told.ids)
        self._sessions.save_notifications(session, left)

    def not_saved(self, session_id: SessionId) -> None:
        """The Turn that told them failed, was cancelled or suspended: they wait for the next."""
        self._told.pop(session_id, None)

    def run(self, session_id: str, action: str, source: str | None = None) -> bool:
        """``/conversation_logs <sid> on|off|list [source]``: whether a source's Notificaciones,
        or every source's, enter the Conversation; never the Log or the Chat."""
        session = self._sessions.load(session_id)
        logs = dict(session.settings.conversation_logs)
        if action == "list":
            for name in sorted({*SOURCES, *logs} - {ALL}):
                state = "on" if enters(session.settings, name) else "off"
                self._reporter.show(session.id, f"{name}: {state}")
            return True
        if action not in ("on", "off"):
            raise InvalidArguments(usage=USAGE)
        if source is None:  # every source, those set on their own too
            logs = {ALL: action == "on"}
        else:
            logs[source] = action == "on"
        settings = session.settings.model_copy(update={"conversation_logs": logs})
        self._sessions.save_settings(session, settings)
        shown = f"conversation logs {action}" + (f" for {source}" if source else "")
        self._reporter.record(session.id, "conversation_logs", shown, action=action, source=source)
        return True
