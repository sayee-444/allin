"""``--devastation``: with it, Main answers by itself the Confirmations of its Chat and of its
Sessions with the Exemption (ADR-0013, as amended on 2026-11-01).

Only a human lights it, when Main starts or with ``/devastation on``, and it lasts that start
of Main: it is never saved and never travels. Main's Chat and each covered Window mark it on
their border, and lighting it tells, in a Notice, which Sessions stop asking.
"""

from collections.abc import Callable

from allin.core.errors import AllInError
from allin.core.ids import MainId, SessionId
from allin.core.settings import SessionSettings
from allin.ipc.messages import Coverage
from allin.main.boxes import MainBoxes
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore
from allin.ui.marks import DEVASTATION_MARK
from allin.ui.screen import TitleMark

_NOTICE_TITLE = "--devastation on"


class Devastation:
    def __init__(
        self,
        on: bool,
        main_border: Callable[[TitleMark], None],
        windows: OpenWindows,
        sessions: SessionStore,
        boxes: MainBoxes,
        reporter: Reporter,
        main_id: MainId,
    ) -> None:
        self._on = on
        self._main_border = main_border
        self._windows = windows
        self._sessions = sessions
        self._boxes = boxes
        self._reporter = reporter
        self._main_id = main_id

    @property
    def on(self) -> bool:
        return self._on

    def covers(self, settings: SessionSettings) -> bool:
        """Whether Main answers by itself the Confirmations of a Session with ``settings``."""
        return self._on and settings.exempt

    def start(self) -> None:
        """Mark Main's Chat if it was lit on the command line."""
        if self._on:
            self.switch(on=True)

    def switch(self, on: bool) -> None:
        """Light it or put it out; running Turns keep what they had when they started."""
        self._on = on
        self._reporter.report(None, "devastation", f"--devastation {'on' if on else 'off'}", on=on)
        self._main_border(TitleMark(DEVASTATION_MARK if on else ""))
        for session_id in self._windows.pids():
            self.mark(session_id)
        if on:
            covered = ", ".join(self._exempt_sessions()) or "none"
            text = (
                f"Main now answers the Confirmations of its Chat and of these Sessions: {covered}"
            )
            self._boxes.notify(None, _NOTICE_TITLE, text)

    def mark(self, session_id: SessionId) -> None:
        """Tell a Session's Window whether Main answers its Confirmations now."""
        try:
            settings = self._sessions.load(session_id).settings
        except AllInError as error:
            self._reporter.error(error, session_id)
            return
        self._windows.send(session_id, Coverage(covered=self.covers(settings)))

    def _exempt_sessions(self) -> list[SessionId]:
        exempt: list[SessionId] = []
        for session_id in sorted(self._sessions.ids()):
            try:
                session = self._sessions.load(session_id)
            except AllInError:
                continue  # an unreadable Session asks nothing
            if session.owner == self._main_id and session.settings.exempt:
                exempt.append(session.id)
        return exempt
