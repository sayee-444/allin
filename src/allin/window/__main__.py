"""The Window process, started by the Launcher inside xterm.

Usage: ``python -m allin.window <session-id> <to-window-fifo> <from-window-fifo>``
"""

import curses
import sys
from pathlib import Path

from allin.core.errors import AllInError
from allin.ipc.channel import Channel
from allin.ipc.messages import ChatAppend, Close, UserInput, WindowReady, decode_main_message
from allin.ui.screen import LineScreen


class _MainListener:
    """Turns what Main sends into lines for the Window's screen."""

    def __init__(self, screen: LineScreen) -> None:
        self._screen = screen

    def on_message(self, message: ChatAppend | Close) -> None:
        match message:
            case ChatAppend(role=role, text=text):
                self._screen.inbox.put(f"[{role}] {text}")
            case Close():
                self._screen.inbox.put(None)

    def on_error(self, error: AllInError) -> None:
        self._screen.inbox.put(f"[error] {error}")

    def on_eof(self) -> None:
        self._screen.inbox.put("[info] Main disconnected")


def _run(stdscr: curses.window, session_id: str, to_window: Path, from_window: Path) -> None:
    screen = LineScreen(stdscr, f"AllIn · {session_id}")
    channel = Channel(to_window, from_window, decode_main_message, _MainListener(screen))
    channel.start()
    channel.send(WindowReady())

    def submit(text: str) -> None:
        screen.append(f"[user] {text}")
        channel.send(UserInput(text=text))

    try:
        screen.run(submit)
    except KeyboardInterrupt:
        pass
    finally:
        channel.close()


def main() -> None:
    session_id, to_window, from_window = sys.argv[1:4]
    curses.wrapper(_run, session_id, Path(to_window), Path(from_window))


if __name__ == "__main__":
    main()
