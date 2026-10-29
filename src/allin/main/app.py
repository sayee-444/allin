"""Main's process: curses UI thread, dispatcher thread, executor threads, and one Window per
open Session."""

import curses
import queue
import sys
import threading
from pathlib import Path

from allin.core.errors import MainAlreadyRunning
from allin.core.ids import ROOT_MAIN_ID
from allin.core.settings import MainSettings
from allin.ipc.channel import remove_stale_fifos
from allin.main.dispatcher import Dispatcher
from allin.main.events import MainCommandEntered, MainEvent, Shutdown
from allin.main.executors import TurnExecutors
from allin.main.health import HealthWatch
from allin.main.lock import MainLock
from allin.main.log import Log
from allin.main.sessions import SessionStore
from allin.main.signals import ShutdownSignals
from allin.main.slots import fetch_total_slots, resolve_slots
from allin.main.state import MainStateStore
from allin.main.turn import OpenAiLlm
from allin.main.windows import WindowOpener, close_orphan_window
from allin.ui.screen import ChatScreen

_SHUTDOWN_GRACE = 2.0


def run(data_dir: Path, sysprompts: Path) -> None:
    lock = MainLock(_state_dir(data_dir) / f"{ROOT_MAIN_ID}.lock")  # held until the process ends
    try:  # before anything touches the state another Main may be using (ADR-0007)
        lock.acquire()
    except MainAlreadyRunning as error:
        sys.exit(f"{error}: another Main with this id already runs on {data_dir}")
    curses.wrapper(_run, data_dir, sysprompts)


def _state_dir(data_dir: Path) -> Path:
    """Where each Main keeps its MainState and its lock (ADR-0007)."""
    return data_dir / "state"


def _run(stdscr: curses.window, data_dir: Path, sysprompts: Path) -> None:
    settings = MainSettings()
    title = "AllIn · Main  (/new, /open <sid>, /send <sid> <text>, /raw on|off [sid], /exit)"
    screen = ChatScreen(stdscr, title)
    log_dir = data_dir / "logs" / "main"
    log_dir.mkdir(parents=True, exist_ok=True)
    inbox: queue.Queue[MainEvent] = queue.Queue()
    remove_stale_fifos(data_dir / "fifo")  # no Window outlives the Main that opened it
    slots = resolve_slots(fetch_total_slots(settings.base_url), settings.parallel)
    executors = TurnExecutors(OpenAiLlm(settings.base_url), slots.count, inbox)
    health = HealthWatch(settings.base_url, inbox)
    dispatcher = Dispatcher(
        inbox,
        screen.inbox.put,
        Log(log_dir / "log.jsonl"),
        SessionStore(data_dir / "sessions"),
        WindowOpener(data_dir, inbox),
        executors.submit,
        ROOT_MAIN_ID,
        settings,
        slots,
        sysprompts,
        health.start,
        MainStateStore(_state_dir(data_dir) / f"{ROOT_MAIN_ID}.json"),
        close_orphan_window,
    )

    def dispatch() -> None:
        dispatcher.run()  # closes every Window before returning
        executors.shutdown(_SHUTDOWN_GRACE)  # running Turns are cancelled, never saved
        health.stop()
        screen.inbox.put(None)

    shutdown_signals = ShutdownSignals(lambda: inbox.put(Shutdown()))
    shutdown_signals.install()
    executors.start()
    dispatcher_thread = threading.Thread(target=dispatch, name="dispatcher")
    dispatcher_thread.start()
    try:
        screen.run(lambda text: inbox.put(MainCommandEntered(text)))
    except KeyboardInterrupt:
        pass
    finally:
        inbox.put(Shutdown())
        dispatcher_thread.join()
        shutdown_signals.uninstall()
