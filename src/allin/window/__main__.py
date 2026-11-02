"""The Window process, started by the Launcher inside xterm.

Usage: ``python -m allin.window <session-id> <to-window-fifo> <from-window-fifo> <record>``,
where ``record`` is the Window's own record (``WindowRecord``).
"""

import curses
import json
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from allin.core.errors import AllInError, ChannelBroken
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
    KeyTable,
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
    KeyBindings,
    QueuedPrompts,
    ScreenUpdate,
    ShowBox,
    TitleMark,
)
from allin.ui.status import context_status


class WindowRecord:
    """The Window's own record, one JSON object per line: what it could not tell Main, kept
    out of its Chat (#33)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, event: str, **fields: str) -> None:
        line = {"ts": datetime.now().astimezone().isoformat(), "event": event, **fields}
        try:
            with self._path.open("a", encoding="utf-8") as record:
                record.write(json.dumps(line) + "\n")
        except OSError:
            pass  # nowhere else to put it: the Chat would show it raw


class MainLink:
    """What the Window sends to Main: nothing once Main is gone (#33)."""

    def __init__(self, send: Callable[[BaseModel], None], gone: threading.Event) -> None:
        self._send = send
        self._gone = gone

    def send(self, message: BaseModel) -> None:
        if not self._gone.is_set():
            self._send(message)


class MainListener:
    """Turns what Main sends into updates for the Window's screen; ``gone`` is set once Main
    is, and nothing more is sent to it."""

    def __init__(self, show: Callable[[ScreenUpdate], None], record: WindowRecord) -> None:
        self._show = show
        self._record = record
        self.gone = threading.Event()
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
                self._show(ChatLine(text, role, label))
            case ChatDelta(role=role, label=label, text=text):
                self._show(ChatStream(text, role, label))
            case TurnState(active=active, waiting=waiting, queued=queued):
                # Enter is never blocked (ADR-0024); a Turn over closes what it streamed.
                if not active:
                    self._show(InputLock(False))
                self._show(QueuedPrompts(tuple(queued)))
                if waiting != self._waiting:
                    self._waiting = waiting
                    self._show(self._border())
            case ContextUsage():
                self._usage = message
                self._show(self._border())
            case PoolShared(shared=shared):
                self._shared = shared
                self._show(self._border())
            case InputRestore(text=text):
                self._show(InputText(text))
            case KeyTable(bindings=bindings):
                self._show(KeyBindings(bindings))
            case PromptHistory(prompts=prompts):
                self._show(InputHistory(tuple(prompts)))
            case BoxShow(box_id=box_id, kind=kind, title=title, text=text):
                self._show(ShowBox(Box(box_id, kind, title, text)))
            case BoxHide(box_id=box_id):
                self._show(HideBox(box_id))
            case Coverage(covered=covered):
                self._show(TitleMark(DEVASTATION_MARK if covered else ""))
            case Close():
                self._show(None)

    def on_error(self, error: AllInError) -> None:
        if isinstance(error, ChannelBroken):  # Main is gone: its FIFO has no reader any more
            self._record.append(error.code, error=str(error))
            self.on_eof()
            return
        self._show(ChatLine(str(error), "error", "error"))

    def on_eof(self) -> None:
        # Nothing reaches Main any more: the Window stays read-only until it is closed.
        if self.gone.is_set():
            return
        self.gone.set()
        self._show(ChatLine("Main disconnected", "info", "info"))
        self._show(InputLock(True))


def _run(
    stdscr: curses.window, session_id: str, to_window: Path, from_window: Path, record: Path
) -> None:
    screen = ChatScreen(stdscr, f"AllIn · {session_id}")
    listener = MainListener(screen.inbox.put, WindowRecord(record))
    channel = Channel(to_window, from_window, decode_main_message, listener)
    link = MainLink(channel.send, listener.gone)
    channel.start()
    link.send(WindowReady())

    def submit(text: str) -> None:
        # Enter is never blocked: sent during a Turn, it waits as queued (ADR-0024).
        link.send(UserInput(text=text))

    def interrupt(text: str) -> None:
        link.send(UserInput(text=text, interrupt=True))

    def box_closed(closed: BoxClosed) -> None:
        link.send(BoxAnswer(box_id=closed.box_id, answer=closed.answer))

    try:
        screen.run(
            submit,
            lambda blocks: link.send(InputDraft(blocks=list(blocks))),
            box_closed,
            on_interrupt=interrupt,
            on_withdraw=lambda: link.send(WithdrawQueued()),
        )
    except KeyboardInterrupt:
        pass
    finally:
        channel.close()


def main() -> None:
    session_id, to_window, from_window, record = sys.argv[1:5]
    curses.wrapper(_run, session_id, Path(to_window), Path(from_window), Path(record))


if __name__ == "__main__":
    main()
