"""The Window process, started by the Launcher inside xterm.

Usage: ``python -m allin.window <session-id> <to-window-fifo> <from-window-fifo>``
"""

import curses
import sys
from pathlib import Path

from allin.core.errors import AllInError
from allin.ipc.channel import Channel
from allin.ipc.messages import (
    BoxAnswer,
    BoxHide,
    BoxShow,
    ChatAppend,
    ChatDelta,
    Close,
    ContextUsage,
    Coverage,
    InputDraft,
    InputRestore,
    MainToWindow,
    PoolShared,
    PromptHistory,
    TurnState,
    UserInput,
    WindowReady,
    WithdrawQueued,
    decode_main_message,
)
from allin.ui.boxes import Box, BoxClosed
from allin.ui.marks import DEVASTATION_MARK
from allin.ui.screen import (
    BorderStatus,
    ChatLine,
    ChatScreen,
    ChatStream,
    HideBox,
    InputHistory,
    InputLock,
    InputText,
    QueuedPrompts,
    ShowBox,
    TitleMark,
)
from allin.ui.status import context_status


class _MainListener:
    """Turns what Main sends into updates for the Window's screen."""

    def __init__(self, screen: ChatScreen) -> None:
        self._screen = screen
        # What the border shows, as Main last said each part.
        self._usage = ContextUsage(context=None, limit=None, tokens_per_second=None)
        self._shared = False
        self._waiting: str | None = None

    def _border(self) -> BorderStatus:
        usage = self._usage
        return BorderStatus(
            context_status(
                usage.context, usage.limit, usage.tokens_per_second, self._shared, self._waiting
            )
        )

    def on_message(self, message: MainToWindow) -> None:
        match message:
            case ChatAppend(role=role, label=label, text=text):
                self._screen.inbox.put(ChatLine(text, role, label))
            case ChatDelta(role=role, label=label, text=text):
                self._screen.inbox.put(ChatStream(text, role, label))
            case TurnState(active=active, waiting=waiting, queued=queued):
                # Enter is never blocked (ADR-0024); a Turn over closes what it streamed.
                if not active:
                    self._screen.inbox.put(InputLock(False))
                self._screen.inbox.put(QueuedPrompts(tuple(queued)))
                if waiting != self._waiting:
                    self._waiting = waiting
                    self._screen.inbox.put(self._border())
            case ContextUsage():
                self._usage = message
                self._screen.inbox.put(self._border())
            case PoolShared(shared=shared):
                self._shared = shared
                self._screen.inbox.put(self._border())
            case InputRestore(text=text):
                self._screen.inbox.put(InputText(text))
            case PromptHistory(prompts=prompts):
                self._screen.inbox.put(InputHistory(tuple(prompts)))
            case BoxShow(box_id=box_id, kind=kind, title=title, text=text):
                self._screen.inbox.put(ShowBox(Box(box_id, kind, title, text)))
            case BoxHide(box_id=box_id):
                self._screen.inbox.put(HideBox(box_id))
            case Coverage(covered=covered):
                self._screen.inbox.put(TitleMark(DEVASTATION_MARK if covered else ""))
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
        # Enter is never blocked: sent during a Turn, it waits as queued (ADR-0024).
        channel.send(UserInput(text=text))

    def interrupt(text: str) -> None:
        channel.send(UserInput(text=text, interrupt=True))

    def box_closed(closed: BoxClosed) -> None:
        channel.send(BoxAnswer(box_id=closed.box_id, answer=closed.answer))

    try:
        screen.run(
            submit,
            lambda blocks: channel.send(InputDraft(blocks=list(blocks))),
            box_closed,
            on_interrupt=interrupt,
            on_withdraw=lambda: channel.send(WithdrawQueued()),
        )
    except KeyboardInterrupt:
        pass
    finally:
        channel.close()


def main() -> None:
    session_id, to_window, from_window = sys.argv[1:4]
    curses.wrapper(_run, session_id, Path(to_window), Path(from_window))


if __name__ == "__main__":
    main()
