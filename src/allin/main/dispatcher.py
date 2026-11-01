"""Main's single dispatcher: the only writer of Main's state (ADR-0001).

It takes Main's events in arrival order, on one thread, and hands each to the collaborator it
owns and calls from that same thread: ``Reporter`` (Main's Chat and the Logs), ``OpenWindows``
(the Windows and their Drafts), ``TurnQueue`` (the queue and the running Turns), ``Recovery``
(MainState and what a dead Main left) and ``MainCommands``.
"""

import queue
from collections.abc import Callable
from pathlib import Path

from allin.core.ids import MainId
from allin.core.settings import MainSettings
from allin.main.commands import MainCommands
from allin.main.events import (
    DraftReceived,
    ErrorOccurred,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    ServerDown,
    ServerLoading,
    ServerReady,
    Shutdown,
    SlotFreed,
    StepEnded,
    TurnEnded,
    TurnOutput,
    TurnRaw,
    WindowClosed,
    WindowConnected,
)
from allin.main.log import Log
from allin.main.open_windows import OpenWindow, OpenWindows
from allin.main.recovery import CloseOrphanWindow, Recovery
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore
from allin.main.slot_locks import SlotLocks
from allin.main.state import MainStateStore
from allin.main.turn_queue import RecheckServer, SubmitTurn, TurnQueue, WaitForSlot


class Dispatcher:
    def __init__(
        self,
        inbox: queue.Queue[MainEvent],
        main_chat: Callable[[str], None],
        main_log: Log,
        sessions: SessionStore,
        open_window: OpenWindow,
        submit_turn: SubmitTurn,
        main_id: MainId,
        settings: MainSettings,
        sysprompts: Path,
        state: MainStateStore,
        close_orphan_window: CloseOrphanWindow,
        slot_locks: SlotLocks,
        wait_for_slot: WaitForSlot,
        recheck_server: RecheckServer,
    ) -> None:
        self._inbox = inbox
        self._main_id = main_id
        self._reporter = Reporter(main_chat, main_log, sessions)
        self._windows = OpenWindows(open_window, self._reporter, sessions)
        self._turns = TurnQueue(
            self._reporter,
            self._windows,
            sessions,
            sysprompts,
            settings.parallel,
            submit_turn,
            slot_locks,
            wait_for_slot,
            recheck_server,
        )
        self._recovery = Recovery(
            self._reporter,
            self._windows,
            self._turns,
            sessions,
            main_id,
            state,
            close_orphan_window,
        )
        self._commands = MainCommands(
            self._reporter, self._windows, self._turns, sessions, main_id, settings, sysprompts
        )

    def run(self) -> None:
        """Handle events until a Shutdown event or the /exit command."""
        self._reporter.report(None, "main_started", "main started", main_id=self._main_id)
        self._recovery.close_left_open()
        self._recovery.restore()
        while self._handle(self._inbox.get()):
            self._recovery.save()
        self._turns.stop()
        self._take_turn_events()
        # Windows, queue and running Turns as they are: a restart restores them.
        self._recovery.save()
        self._turns.cancel_running()
        self._windows.close_all()
        self._reporter.log_main("main_stopped", main_id=self._main_id)

    def _take_turn_events(self) -> None:
        """Handle what Turns and drafts reported before Main stopped, so a Turn that already
        ended is saved instead of being cancelled and the last draft is kept; no command or
        other Window event is taken any more."""
        while True:
            try:
                event = self._inbox.get(block=False)
            except queue.Empty:
                return
            if isinstance(event, TurnOutput | StepEnded | TurnEnded | DraftReceived):
                self._handle(event)

    def _handle(self, event: MainEvent) -> bool:
        match event:
            case WindowConnected(session_id):
                self._reporter.record(session_id, "window_connected", "window connected")
                self._turns.send_turn_state(session_id)
            case InputReceived(session_id, text):
                self._windows.clear_draft(session_id)  # the Window's input is empty now
                self._commands.run(f"/send {session_id} {text}")
                self._turns.send_turn_state(session_id)  # unblocks Enter if /send failed
            case DraftReceived(session_id, blocks):
                self._windows.keep_draft(session_id, blocks)
            case TurnOutput(session_id, kind, text):
                self._turns.output(session_id, kind, text)
            case TurnRaw(session_id, raw):
                self._reporter.raw(session_id, raw)
            case StepEnded(session_id, step):
                self._turns.step_ended(session_id, step)
            case TurnEnded(session_id, steps, error):
                self._turns.turn_ended(session_id, steps, error)
            case WindowClosed(session_id, returncode):
                self._windows.closed(session_id, returncode)
                self._turns.cancel(session_id, "window closed")
            case ErrorOccurred(error, session_id):
                self._windows.show_error(error, session_id)
            case ServerReady(props):
                self._turns.server_ready(props)
            case ServerLoading():
                self._turns.pause("loading")
            case ServerDown():
                self._turns.pause("down")
            case SlotFreed():
                self._turns.slot_freed()
            case MainCommandEntered(text):
                return self._commands.run(text)
            case Shutdown():
                return False
        return True
