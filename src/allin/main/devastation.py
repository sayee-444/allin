"""``--devastation``: with it, Main answers by itself the Confirmations of its Chat and of its
Sessions with the Exemption (ADR-0013, as amended on 2026-11-01), and those of a Subperfil with
the Exención that touch nothing outside it (ADR-0026).

Only a human lights it, when Main starts or with ``/devastation on``, and it lasts that start
of Main: it is never saved and never travels. Main's Chat and each covered Window mark it on
their border, and lighting it tells, in a Notice, which Sessions stop asking.
"""

from allin.core.errors import AllInError
from allin.core.ids import AttachedName, MainId, SessionId
from allin.core.origin import Link, Origin, weakest_link
from allin.core.settings import SessionSettings
from allin.ipc.messages import Coverage
from allin.main.boxes import MainBoxes
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore
from allin.main.subprofiles import Subprofiles
from allin.ui.marks import BorderMarks

_NOTICE_TITLE = "--devastation on"


class Devastation:
    def __init__(
        self,
        on: bool,
        marks: BorderMarks,
        windows: OpenWindows,
        sessions: SessionStore,
        boxes: MainBoxes,
        reporter: Reporter,
        main_id: MainId,
        subprofiles: Subprofiles,
    ) -> None:
        self._on = on
        self._marks = marks
        self._windows = windows
        self._sessions = sessions
        self._boxes = boxes
        self._reporter = reporter
        self._main_id = main_id
        self._subprofiles = subprofiles

    @property
    def on(self) -> bool:
        return self._on

    def covers(self, settings: SessionSettings) -> bool:
        """Whether Main answers by itself the Confirmations of a Session with ``settings``."""
        return self._on and settings.exempt

    def covers_session(self, session_id: str) -> bool:
        """Whether it covers the Session ``session_id`` names; not one it cannot read."""
        try:
            return self.covers(self._sessions.load(session_id).settings)
        except AllInError:
            return False

    def answers(self, origin: Origin) -> bool:
        """Whether Main answers by itself a Confirmation asked with ``origin``: only if it covers
        every link of the chain (ADR-0013). A human's or its own are covered; a Session only
        if it is Main's and has the Exemption; a Proceso adherido never; a Suscripción, as the
        Session it wakes, which comes after it."""
        if not self._on:
            return False
        if weakest_link(origin) != "session":
            return True
        return all(self._covers_link(link) for link in origin.chain)

    def answers_inside(self, origin: Origin) -> bool:
        """Whether Main answers by itself, of a Confirmation asked with ``origin``, only what
        stays inside the Subperfil it acts in: each link is covered or in a Subperfil with the
        Exención, a Proceso adherido by its own (ADR-0026)."""
        if not self._on:
            return False
        return all(self._covers_link(link) or self._exempt_inside(link) for link in origin.chain)

    def covers_inside(self, session_id: SessionId) -> bool:
        """Whether it answers what stays inside the Session's Subperfil, which has the
        Exención; what its Window marks."""
        name = self._subprofiles.holding(session_id)
        return self._on and name is not None and self._subprofiles.record(name).exempt

    def _exempt_inside(self, link: Link) -> bool:
        match link.kind:
            case "session" if link.id:
                return self.covers_inside(SessionId(link.id))
            case "process":
                name = AttachedName(link.id)
                return name in self._subprofiles.names() and self._subprofiles.record(name).exempt
            case _:
                return False

    def _covers_link(self, link: Link) -> bool:
        match link.kind:
            case "human" | "main":
                return True
            # A Suscripción only wakes its own Session, which follows it in the chain and is
            # checked there (#116).
            case "subscription":
                return True
            case "session" if link.id:
                try:
                    session = self._sessions.load(SessionId(link.id))
                except AllInError:
                    return False  # one that cannot be read asks the human
                return session.owner == self._main_id and session.settings.exempt
            case _:
                return False

    def start(self) -> None:
        """Mark Main's Chat if it was lit on the command line."""
        if self._on:
            self.switch(on=True)

    def switch(self, on: bool) -> None:
        """Light it or put it out; running Turns keep what they had when they started."""
        self._on = on
        self._reporter.report(None, "devastation", f"--devastation {'on' if on else 'off'}", on=on)
        self._marks.devastation(on)
        for session_id in self._windows.pids():
            self.mark(session_id)
        if on:
            covered = ", ".join(self._exempt_sessions()) or "none"
            text = (
                f"Main now answers the Confirmations of its Chat and of these Sessions: {covered}"
            )
            exempt = [n for n in self._subprofiles.names() if self._subprofiles.record(n).exempt]
            if exempt:
                text += f"; and what stays inside these Subperfiles: {', '.join(exempt)}"
            self._boxes.notify(None, _NOTICE_TITLE, text)

    def mark(self, session_id: SessionId) -> None:
        """Tell a Session's Window whether Main answers its Confirmations now."""
        try:
            settings = self._sessions.load(session_id).settings
        except AllInError as error:
            self._reporter.error(error, session_id)
            return
        covered = self.covers(settings)
        inside = not covered and self.covers_inside(session_id)
        self._windows.send(session_id, Coverage(covered=covered, inside=inside))

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
