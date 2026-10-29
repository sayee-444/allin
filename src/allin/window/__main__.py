"""The Window process, started by the Launcher inside xterm.

Usage: ``python -m allin.window <session-id> <to-window-fifo> <from-window-fifo>``
"""

import curses
import sys
from pathlib import Path

from allin.core.errors import AllInError
from allin.ipc.channel import Channel
from allin.ipc.messages import (
    ChatAppend,
    ChatDelta,
    Close,
    ContextUsage,
    InputDraft,
    InputRestore,
    MainToWindow,
    TurnState,
    UserInput,
    WindowReady,
    decode_main_message,
)
from allin.ui.screen import (
    BorderStatus,
    ChatLine,
    ChatScreen,
    ChatStream,
    InputLock,
    InputText,
)
from allin.ui.status import context_status


class _MainListener:
    """Turns what Main sends into updates for the Window's screen."""

    def __init__(self, screen: ChatScreen) -> None:
        self._screen = screen

    def on_message(self, message: MainToWindow) -> None:
        match message:
            case ChatAppend(role=role, label=label, text=text):
                self._screen.inbox.put(ChatLine(text, role, label))
            case ChatDelta(role=role, label=label, text=text):
                self._screen.inbox.put(ChatStream(text, role, label))
            case TurnState(active=active):
                self._screen.inbox.put(InputLock(active))
            case ContextUsage(context=context, limit=limit, tokens_per_second=speed):
                self._screen.inbox.put(BorderStatus(context_status(context, limit, speed)))
            case InputRestore(text=text):
                self._screen.inbox.put(InputText(text))
            case Close():
                self._screen.inbox.put(None)

    def on_error(self, error: AllInError) -> None:
        self._screen.inbox.put(ChatLine(str(error), "error", "error"))

    def on_eof(self) -> None:
        # Nothing reaches Main any more: the Window stays read-only until it is closed.
        self._screen.inbox.put(ChatLine("Main disconnected", "info", "info"))
        self._screen.inbox.put(InputLock(True))


def _run(stdscr: curses.window, session_id: str, to_window: Path, from_window: Path) -> None:
    screen = ChatScreen(stdscr, f"AllIn · {session_id}")
    channel = Channel(to_window, from_window, decode_main_message, _MainListener(screen))
    channel.start()
    channel.send(WindowReady())

    def submit(text: str) -> None:
        # Main shows the Prompt in the Chat and unblocks Enter when the Turn ends (TurnState).
        screen.lock_input()
        channel.send(UserInput(text=text))

    try:
        screen.run(submit, lambda text: channel.send(InputDraft(text=text)))
    except KeyboardInterrupt:
        pass
    finally:
        channel.close()


def main() -> None:
    session_id, to_window, from_window = sys.argv[1:4]
    curses.wrapper(_run, session_id, Path(to_window), Path(from_window))


if __name__ == "__main__":
    main()
