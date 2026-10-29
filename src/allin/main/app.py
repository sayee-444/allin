"""Main's process: curses UI thread, dispatcher thread, executor threads, and one Window per
open Session."""

import curses
import queue
import threading
from pathlib import Path

from allin.core.ids import ROOT_MAIN_ID
from allin.core.settings import MainSettings
from allin.ipc.channel import remove_stale_fifos
from allin.main.dispatcher import Dispatcher
from allin.main.events import MainCommandEntered, MainEvent, Shutdown
from allin.main.executors import TurnExecutors
from allin.main.health import HealthWatch
from allin.main.log import Log
from allin.main.sessions import SessionStore
from allin.main.signals import ShutdownSignals
from allin.main.slots import fetch_total_slots, resolve_slots
from allin.main.turn import OpenAiLlm
from allin.main.windows import WindowOpener
from allin.ui.screen import ChatScreen

_SHUTDOWN_GRACE = 2.0


def run(data_dir: Path, sysprompts: Path) -> None:
    curses.wrapper(_run, data_dir, sysprompts)


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
