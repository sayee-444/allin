"""The Organizer (#53, ADR-0020): Main's own screen, in its own xterm, listing the Profile's
Sessions to bring one into view, send it to the background or close it.

It is part of Main, not a piece: what the human does in it runs as the Command main of the same
name with a human Origin. There is one per Main; it does not count for the Máximo de Ventanas
and a restart does not bring it back.
"""

from collections.abc import Callable
from dataclasses import dataclass

from allin.core.errors import AllInError, WindowLaunchFailed
from allin.core.ids import SessionId
from allin.core.themes import Theme
from allin.ipc.messages import (
    Close,
    OrganizerAction,
    OrganizerRow,
    OrganizerRows,
    Place,
    ThemeSet,
)
from allin.main.boxes import MainBoxes
from allin.main.open_windows import OpenWindows, WindowLink
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.turn_queue import TurnQueue
from allin.main.usage import context_tokens

type OpenOrganizer = Callable[[Theme], WindowLink]


def no_organizer(_: Theme) -> WindowLink:
    """Where Main has no terminal to open it in."""
    raise WindowLaunchFailed(session_id="organizer", reason="no terminal to open it in")


_PROMPT_START = 40  # characters of its first Prompt in a title left empty


def row_title(session_id: str, title: str, first_prompt: str) -> str:
    """Its ``title`` Setting; if empty, its id and the start of its first Prompt."""
    if title:
        return title
    start = " ".join(first_prompt.split())
    if not start:
        return session_id
    if len(start) > _PROMPT_START:
        start = start[: _PROMPT_START - 1] + "…"
    return f"{session_id} · {start}"


def command_for(action: OrganizerAction, session_id: str, place: Place) -> str | None:
    """The Command main an action of the Organizer runs, as things are now; ``None`` when
    there is nothing to do (bringing a Session already in view, closing a closed one)."""
    match action:
        case "open":
            return None if place == "view" else f"/open {session_id}"
        case "bg":
            if place == "view":
                return f"/bg {session_id}"
            return None if place == "background" else f"/open {session_id} --bg"
        case "close":
            return None if place == "closed" else f"/close {session_id}"


@dataclass(frozen=True)
class _Known:
    """What a row takes from the Session's files, kept while they do not change."""

    stamp: int
    session: Session
    first_prompt: str
    context: int | None


class RowSource:
    """The rows as they are now: the open Sessions in view first, then those in the
    background, then the Profile's closed Sessions, most recent first."""

    def __init__(
        self,
        sessions: SessionStore,
        windows: OpenWindows,
        turns: TurnQueue,
        boxes: MainBoxes,
        profile_sessions: Callable[[], list[SessionId]],
    ) -> None:
        self._sessions = sessions
        self._windows = windows
        self._turns = turns
        self._boxes = boxes
        self._profile_sessions = profile_sessions
        self._known: dict[SessionId, _Known] = {}

    def place(self, session_id: SessionId) -> Place:
        if self._windows.is_open(session_id):
            return "view"
        return "background" if self._windows.is_background(session_id) else "closed"

    def rows(self) -> list[OrganizerRow]:
        visible_ids = self._windows.open_ids()
        hidden = self._windows.background_ids()
        open_ids = [*visible_ids, *hidden]
        try:
            profile = self._profile_sessions()
        except AllInError:
            profile = []
        closed = [sid for sid in profile if sid not in open_ids]
        closed.sort(key=self._sessions.changed_at, reverse=True)
        rows: list[OrganizerRow] = []
        for session_id in [*open_ids, *closed]:
            row = self._row(session_id)
            if row is not None:
                rows.append(row)
        return rows

    def _row(self, session_id: SessionId) -> OrganizerRow | None:
        known = self._facts(session_id)
        if known is None:
            return None
        session = known.session
        place = self.place(session_id)
        streamed = self._turns.streamed(session_id)
        context = known.context
        if streamed is not None and streamed.steps:
            context = context_tokens(streamed.steps)
        return OrganizerRow(
            session_id=session_id,
            title=row_title(session_id, session.settings.title, known.first_prompt),
            place=place,
            running=place != "closed" and self._turns.busy(session_id),
            queued=self._turns.queued_count(session_id),
            asking=self._boxes.asking(session_id),
            notice=self._boxes.noticing(session_id),
            attention=self._boxes.wants_attention(session_id),
            context=context,
            limit=self._windows.context_limit(session),
            supervisor=session.settings.server or "",
        )

    def _facts(self, session_id: SessionId) -> _Known | None:
        stamp = self._sessions.changed_at(session_id)
        known = self._known.get(session_id)
        if known is not None and known.stamp == stamp:
            return known
        try:
            session = self._sessions.load(session_id)
            conversation = self._sessions.load_conversation(session)
        except AllInError:
            return None  # unreadable: the Organizer leaves it out, as /list would fail on it
        turns = conversation.turns if conversation is not None else []
        first = turns[0].as_typed if turns else ""
        context = context_tokens(turns[-1].steps) if turns else None
        known = _Known(stamp, session, first, context)
        self._known[session_id] = known
        return known


class Organizer:
    """Main's side of the Organizer: at most one, sent its rows and Theme whenever they
    change."""

    def __init__(
        self,
        open_organizer: OpenOrganizer,
        rows: Callable[[], list[OrganizerRow]],
        theme: Callable[[], Theme],
        reporter: Reporter,
    ) -> None:
        self._open = open_organizer
        self._rows = rows
        self._theme = theme
        self._reporter = reporter
        self._link: WindowLink | None = None
        self._ready = False
        self._sent_rows: list[OrganizerRow] | None = None
        self._sent_theme: Theme | None = None

    @property
    def is_open(self) -> bool:
        return self._link is not None

    def open(self) -> None:
        """Open it; one already open is closed first, so the new one is in front."""
        if self._link is not None:
            self.close("replaced by a new one")
        self._link = self._open(self._theme())
        self._ready, self._sent_rows, self._sent_theme = False, None, None
        self._reporter.report(None, "organizer_opened", "organizer opening")

    def connected(self) -> None:
        self._ready = True
        self.refresh()

    def refresh(self) -> None:
        """Send what changed since last time."""
        if self._link is None or not self._ready:
            return
        theme = self._theme()
        if theme != self._sent_theme:
            self._link.send(ThemeSet(theme=theme))
            self._sent_theme = theme
        rows = self._rows()
        if rows != self._sent_rows:
            self._link.send(OrganizerRows(rows=rows))
            self._sent_rows = rows

    def closed(self, pid: int) -> None:
        """Its xterm is gone: Esc, its X, or the one replaced. No Session is touched."""
        if self._link is None or self._link.pid != pid:
            return  # one already replaced
        self._link, self._ready = None, False
        self._reporter.report(None, "organizer_closed", "organizer closed")

    def close(self, reason: str) -> None:
        link = self._link
        if link is None:
            return
        self._link, self._ready = None, False
        link.send(Close())
        link.close()
        self._reporter.report(None, "organizer_closed", f"organizer closed: {reason}")
