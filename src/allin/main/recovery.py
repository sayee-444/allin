"""Putting the user back where they were after Main stops, however it stopped.

It keeps MainState saved after every change (``save``), puts it back on start (``restore``) and
says in each Session's Log what a Main killed outright could not (``close_left_open``).
"""

from collections.abc import Callable

from allin.core.drafts import put_back
from allin.core.errors import AllInError, TurnCrashed
from allin.core.ids import MainId, SessionId
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore
from allin.main.state import MainState, MainStateStore
from allin.main.turn_queue import TurnQueue

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
    ) -> None:
        self._reporter = reporter
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
            if _last_index(events, "window_opened") > _last_index(events, "window_closed"):
                shown = f"window closed: {_MAIN_DIED}"
                self._reporter.record(session.id, "window_closed", shown, returncode=None)

    def restore(self) -> None:
        """Put the user back where they were when Main last stopped, however it stopped.

        A Turn that was running is reverted (only whole Turns reach a Conversation): its Prompt
        goes back to its Window's input, shown ahead of the Draft, and is not sent again by
        itself.
        The queue waits for the server's ``/health``, which may still be loading the model.
        """
        self._put_back()

    def enter(self, state: MainStateStore) -> None:
        """Put back the MainState of the Profile Main just entered (``/profile switch``): the
        Windows Main kept open, those of Sessions both Profiles share, stay as they are."""
        self._state = state
        self._saved = MainState()
        self._put_back()

    def _put_back(self) -> None:
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
        self._turns.requeue(saved.queue)
        for session_id, pid in saved.windows.items():
            if self._windows.is_open(session_id):
                continue
            # Only one Main of this id runs (ADR-0007), so a Window of its still up is an orphan.
            if self._close_orphan_window(session_id, pid):
                shown = "window left open by the last main closed"
                self._reporter.record(session_id, "orphan_window_closed", shown, pid=pid)
            try:
                session = self._sessions.load_owned(session_id, self._main_id)
                self._windows.open(session, self._turns.streamed(session.id))
            except AllInError as error:
                self._windows.show_error(error, session_id)
        self._turns.tell_waiting()  # once the Windows are open
        self.save()

    def save(self) -> None:
        """Keep MainState as it is now, if it changed."""
        state = MainState(
            windows=self._windows.pids(),
            drafts=self._windows.drafts(),
            queue=self._turns.queued(),
            running=self._turns.running_prompts(),
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
