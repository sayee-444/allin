"""Putting the user back where they were after Main stops, however it stopped.

It keeps MainState saved after every change (``save``), puts it back on start (``restore``) and
says in each Session's Log what a Main killed outright could not (``close_left_open``).
"""

from collections.abc import Callable

from pydantic import JsonValue

from allin.core.drafts import put_back
from allin.core.errors import AllInError, TurnCrashed
from allin.core.ids import MainId, SessionId
from allin.main.boxes import MainBoxes
from allin.main.notifications import ERRAND, Notifications
from allin.main.open_windows import OpenWindows
from allin.main.prompts import Prompt
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore
from allin.main.state import MainState, MainStateStore
from allin.main.subscriptions import Subscriptions
from allin.main.turn_queue import TurnQueue
from allin.main.window_room import NO_LIMIT

# Closes the Window of a Session (by its xterm's pid) that a killed Main left open; returns
# whether there was one.
type CloseOrphanWindow = Callable[[SessionId, int], bool]

_MAIN_DIED = "main stopped without closing it"
_TURN_ENDINGS = ("turn_finished", "turn_failed", "turn_cancelled")


class Recovery:
    def __init__(
        self,
        reporter: Reporter,
        windows: OpenWindows,
        turns: TurnQueue,
        sessions: SessionStore,
        main_id: MainId,
        state: MainStateStore,
        close_orphan_window: CloseOrphanWindow,
        subscriptions: Subscriptions,
        boxes: MainBoxes,
        notifications: Notifications,
    ) -> None:
        self._reporter = reporter
        self._boxes = boxes
        self._notifications = notifications
        self._unclean = False  # the last Main died without closing what it had open
        self._subscriptions = subscriptions
        self._windows = windows
        self._turns = turns
        self._sessions = sessions
        self._main_id = main_id
        self._state = state
        self._close_orphan_window = close_orphan_window
        self._saved = MainState()  # what ``state`` holds now

    def close_left_open(self) -> None:
        """Mark closed the Sessions a Main killed outright left with an open Window or a Turn.

        No Window outlives the Main that opened it and no Turn either, so on start every Session
        is closed; their Logs only need saying so where the dead Main could not. A Turn left
        unfinished also gets ``turn_failed`` (beyond what F1-11 asked), so its rebuilt Chat says
        why its Prompt has no answer instead of showing it as if it were still waiting.
        """
        for session_id in self._sessions.ids():
            try:
                session = self._sessions.load_owned(session_id, self._main_id)
                events = [entry.event for entry in self._sessions.read_log(session)]
            except AllInError:
                continue  # another Main's, or unreadable: /open reports it if it is ever asked for
            if _last_index(events, "turn_started") > _last_index(events, *_TURN_ENDINGS):
                # Whatever it streamed died with Main; its Prompt stays in the Chat.
                error = str(TurnCrashed(reason=_MAIN_DIED))
                fields = {"error": error, "reasoning": "", "answer": ""}
                self._reporter.record(session.id, "turn_failed", f"turn failed: {error}", **fields)
                self._unclean = True
            if _last_index(events, "window_opened") > _last_index(events, "window_closed"):
                self._unclean = True
                shown = f"window closed: {_MAIN_DIED}"
                self._reporter.record(session.id, "window_closed", shown, returncode=None)

    def restore(self) -> None:
        """Put the user back where they were when Main last stopped, however it stopped.

        A Turn that was running is reverted (only whole Turns reach a Conversation): its Prompt
        goes back to its Window's input, shown ahead of the Draft, and is not sent again by
        itself.
        The queue waits for the server's ``/health``, which may still be loading the model.
        If the last Main died, the Informe de recuperación tells what it left half done.
        """
        if self._put_back() or self._unclean:
            self._report()

    def _report(self) -> None:
        """The Informe de recuperación: for now, the Encargos the dead Main had open, which
        go on, its workers that died with it counted as no answer (ADR-0011)."""
        errands = self._subscriptions.errands()
        if not errands:
            return
        lines = [f"errand {s.id} of Session {s.session_id} ({s.event}): goes on" for s in errands]
        for subscription in errands:
            told = f"Main stopped while your errand {subscription.id} was open; it goes on"
            self._notifications.leave(subscription.session_id, ERRAND, told)
        self._reporter.log_main("recovery_report", errands=list[JsonValue](lines))
        text = "Left half done when Main last stopped:\n" + "\n".join(lines)
        self._boxes.notify(None, "Recovery report", text, kind="recovery_report")

    def enter(self, state: MainStateStore) -> None:
        """Put back the MainState of the Profile Main just entered (``/profile switch``): the
        Windows Main kept open, those of Sessions both Profiles share, stay as they are."""
        self._state = state
        self._saved = MainState()
        self._put_back()

    def _put_back(self) -> bool:
        """Whether Turns were running when the state was saved last."""
        try:
            self._saved = self._state.load()
        except AllInError as error:
            self._windows.show_error(error)
        saved = self._saved
        drafts = dict(saved.drafts)
        for prompt in saved.running:
            draft = drafts.get(prompt.session_id, [])
            drafts[prompt.session_id] = put_back(draft, prompt.text)
        self._windows.replace_drafts(drafts)
        self._subscriptions.restore(saved.subscriptions)
        for prompt in saved.running:  # in no Conversation, it answers no one
            self._subscriptions.lost(prompt, "Main stopped during its Turn")
        self._turns.requeue(saved.queue)
        for session_id in saved.background:
            if not self._windows.is_open(session_id):
                self._windows.keep_in_background(session_id)
        reopened = [sid for sid in saved.windows if not self._windows.is_open(sid)]
        # In view again up to the Máximo de Ventanas, the last ones opened; the rest in the
        # background (ADR-0020).
        maximum = self._windows.maximum
        room = len(reopened) if maximum == NO_LIMIT else maximum - len(self._windows.open_ids())
        hidden = reopened[: max(0, len(reopened) - room)]
        for session_id, pid in saved.windows.items():
            if self._windows.is_open(session_id):
                continue
            # Only one Main of this id runs (ADR-0007), so a Window of its still up is an orphan.
            if self._close_orphan_window(session_id, pid):
                shown = "window left open by the last main closed"
                self._reporter.record(session_id, "orphan_window_closed", shown, pid=pid)
            if session_id in hidden:
                self._windows.keep_in_background(session_id)
                continue
            try:
                session = self._sessions.load_owned(session_id, self._main_id)
                self._windows.open(session, self._turns.streamed(session.id))
            except AllInError as error:
                self._windows.show_error(error, session_id)
        self._turns.tell_waiting()  # once the Windows are open
        self.save()
        return bool(saved.running)

    def leave(self, queue: list[Prompt]) -> None:
        """Keep the MainState of the Profile Main leaves (``/profile switch``): its Windows and
        Drafts as they are, and ``queue``, the Prompts it takes with it; no Turn runs."""
        state = MainState(
            windows=self._windows.pids(),
            drafts=self._windows.drafts(),
            queue=queue,
            background=self._windows.background_ids(),
            subscriptions=self._subscriptions.state(),
        )
        try:
            self._state.save(state)
        except AllInError as error:
            self._windows.show_error(error)

    def save(self) -> None:
        """Keep MainState as it is now, if it changed."""
        state = MainState(
            windows=self._windows.pids(),
            drafts=self._windows.drafts(),
            queue=self._turns.queued(),
            running=self._turns.running_prompts(),
            background=self._windows.background_ids(),
            subscriptions=self._subscriptions.state(),
        )
        if state == self._saved:
            return
        try:
            self._state.save(state)
        except AllInError as error:
            self._windows.show_error(error)
        else:
            self._saved = state


def _last_index(events: list[str], *names: str) -> int:
    """Where the last of ``names`` is in ``events``; -1 if none is."""
    return max((index for index, event in enumerate(events) if event in names), default=-1)
