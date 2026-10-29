"""Main's process: curses UI thread, dispatcher thread, and one Window per Session."""

import curses
import queue
import sys
import threading
from pathlib import Path

from allin.core.errors import AllInError
from allin.core.ids import new_session_id
from allin.ipc.channel import Channel, create_fifo_pair
from allin.ipc.messages import UserInput, WindowReady, decode_window_message
from allin.main.dispatcher import Dispatcher
from allin.main.events import (
    ErrorOccurred,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    Shutdown,
    WindowClosed,
    WindowConnected,
)
from allin.main.launcher import Launcher, xterm_command
from allin.ui.screen import LineScreen

_CLOSE_GRACE = 2.0


class _WindowListener:
    """Turns what a Window sends into events for the dispatcher."""

    def __init__(self, session_id: str, inbox: queue.Queue[MainEvent]) -> None:
        self._session_id = session_id
        self._inbox = inbox

    def on_message(self, message: WindowReady | UserInput) -> None:
        match message:
            case WindowReady():
                self._inbox.put(WindowConnected(self._session_id))
            case UserInput(text=text):
                self._inbox.put(InputReceived(self._session_id, text))

    def on_error(self, error: AllInError) -> None:
        self._inbox.put(ErrorOccurred(error, self._session_id))

    def on_eof(self) -> None:
        pass  # the Launcher's watcher reports the closed Window


def run(data_dir: Path) -> None:
    curses.wrapper(_run, data_dir)


def _run(stdscr: curses.window, data_dir: Path) -> None:
    screen = LineScreen(stdscr, "AllIn · Main  (/exit to quit)")
    inbox: queue.Queue[MainEvent] = queue.Queue()
    dispatcher = Dispatcher(inbox, screen.inbox.put)

    session_id = new_session_id()
    fifos = create_fifo_pair(data_dir / "fifo", session_id)
    channel = Channel(
        fifos.from_window,
        fifos.to_window,
        decode_window_message,
        _WindowListener(session_id, inbox),
    )
    window_command = [
        sys.executable,
        "-m",
        "allin.window",
        session_id,
        str(fifos.to_window),
        str(fifos.from_window),
    ]
    launcher = Launcher(
        xterm_command(window_command, title=f"AllIn · {session_id}"),
        data_dir / "logs" / "main" / f"{session_id}-xterm.log",
        lambda returncode: inbox.put(WindowClosed(session_id, returncode)),
    )
    dispatcher.register(session_id, channel)

    def dispatch() -> None:
        dispatcher.run()
        screen.inbox.put(None)

    dispatcher_thread = threading.Thread(target=dispatch, name="dispatcher")
    dispatcher_thread.start()
    channel.start()
    launcher.start()
    screen.append(f"[{session_id}] opening window")

    try:
        screen.run(lambda text: inbox.put(MainCommandEntered(text)))
    except KeyboardInterrupt:
        pass
    finally:
        inbox.put(Shutdown())
        dispatcher_thread.join()
        channel.close()
        if not launcher.wait(_CLOSE_GRACE):
            launcher.terminate(_CLOSE_GRACE)
        fifos.remove()
