"""Main's process: curses UI thread, dispatcher thread, and one Window per open Session."""

import curses
import queue
import threading
from pathlib import Path

from allin.core.ids import ROOT_MAIN_ID
from allin.main.dispatcher import Dispatcher
from allin.main.events import MainCommandEntered, MainEvent, Shutdown
from allin.main.sessions import SessionStore
from allin.main.windows import WindowOpener
from allin.ui.screen import ChatScreen


def run(data_dir: Path) -> None:
    curses.wrapper(_run, data_dir)


def _run(stdscr: curses.window, data_dir: Path) -> None:
    screen = ChatScreen(stdscr, "AllIn · Main  (/new, /open <sid>, /exit)")
    inbox: queue.Queue[MainEvent] = queue.Queue()
    dispatcher = Dispatcher(
        inbox,
        screen.inbox.put,
        SessionStore(data_dir / "sessions"),
        WindowOpener(data_dir, inbox),
        ROOT_MAIN_ID,
    )

    def dispatch() -> None:
        dispatcher.run()  # closes every Window before returning
        screen.inbox.put(None)

    dispatcher_thread = threading.Thread(target=dispatch, name="dispatcher")
    dispatcher_thread.start()
    try:
        screen.run(lambda text: inbox.put(MainCommandEntered(text)))
    except KeyboardInterrupt:
        pass
    finally:
        inbox.put(Shutdown())
        dispatcher_thread.join()
