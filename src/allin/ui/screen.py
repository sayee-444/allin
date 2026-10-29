"""Chat + input screen shared by Main and the Window.

Two ``newwin`` boxes only draw borders; the content lives in one pad per box (Chat and input),
placed by ``compute_layout`` and scrolled through a ``Viewport``. Only the UI thread touches
curses; other threads hand it lines through ``inbox``.
"""

import curses
import queue
import sys
from collections.abc import Callable

from allin.core.errors import ScreenTooSmall
from allin.ui.input_buffer import InputBuffer
from allin.ui.keys import Key, KeyChord, KeyDecoder, KeyEvent, Paste
from allin.ui.layout import Layout, Rect, compute_layout
from allin.ui.viewport import Viewport
from allin.ui.wrap import cell_width, wrap

_POLL_MS = 50  # curses has no blocking wait on both keys and the inbox
_BRACKETED_PASTE_ON = "\x1b[?2004h"
_BRACKETED_PASTE_OFF = "\x1b[?2004l"
_TOO_SMALL = "Terminal too small: please enlarge it"


class ChatScreen:
    def __init__(self, stdscr: curses.window, title: str) -> None:
        self._stdscr = stdscr
        self._title = title
        self._chat: list[str] = []
        self._chat_rows: list[str] = []  # self._chat wrapped to self._chat_width
        self._chat_width = 0
        self._chat_view = Viewport()
        self._input = InputBuffer()
        self._keys = KeyDecoder()
        self._layout: Layout | None = None
        self._boxes: tuple[curses.window, curses.window] | None = None
        self._pads: tuple[curses.window, curses.window] | None = None
        self._dirty = True
        self._repaint = True  # repaint the whole terminal, not just what changed
        # Lines to append from other threads; None asks the screen to quit.
        self.inbox: queue.Queue[str | None] = queue.Queue()

    def append(self, line: str) -> None:
        self._chat.append(line)
        if self._chat_width:
            self._chat_rows += wrap(line, self._chat_width)
        self._dirty = True

    def run(self, on_submit: Callable[[str], None]) -> None:
        """Run the UI loop until ``None`` arrives in the inbox."""
        self._stdscr.keypad(True)
        self._stdscr.timeout(_POLL_MS)
        _write_terminal(_BRACKETED_PASTE_ON)
        try:
            self._loop(on_submit)
        finally:
            _write_terminal(_BRACKETED_PASTE_OFF)

    def _loop(self, on_submit: Callable[[str], None]) -> None:
        while True:
            if not self._drain_inbox():
                return
            if self._dirty:
                self._draw()
            try:
                key = self._stdscr.get_wch()
            except curses.error:
                self._keys.flush()
                continue
            for chord in self._keys.feed(key):
                self._dirty = True
                self._handle(chord, on_submit)

    def _handle(self, chord: KeyEvent, on_submit: Callable[[str], None]) -> None:
        match chord:
            case Paste(text=text):
                self._input.insert(text)
            case KeyChord(key=Key.RESIZE):
                curses.update_lines_cols()
                self._repaint = True
            case KeyChord(key=Key.ENTER, ctrl=True):
                self._input.insert("\n")
            case KeyChord(key=Key.ENTER):
                text = self._input.take()
                if text.strip():
                    on_submit(text)
            case KeyChord(key=Key.BACKSPACE, ctrl=True):
                self._input.delete_word()
            case KeyChord(key=Key.BACKSPACE):
                self._input.backspace()
            case KeyChord(key=Key.PAGE_UP | Key.PAGE_DOWN as key):
                self._scroll_chat(-1 if key == Key.PAGE_UP else 1)
            case KeyChord(key=str() as char, ctrl=False, alt=False) if char.isprintable():
                self._input.insert(char)
            case _:
                pass

    def _drain_inbox(self) -> bool:
        while True:
            try:
                line = self.inbox.get_nowait()
            except queue.Empty:
                return True
            if line is None:
                return False
            self.append(line)

    def _scroll_chat(self, pages: int) -> None:
        if self._layout is None:
            return
        rows = self._layout.chat_pad.height
        self._chat_view.scroll(pages * max(1, rows - 1), len(self._chat_rows), rows)

    def _draw(self) -> None:
        self._dirty = False
        if self._repaint:
            # Without this, stdscr's blank contents (refreshed by get_wch) cover the boxes, and
            # after a resize curses only sends what changed against the old terminal size.
            self._repaint = False
            self._stdscr.clear()
            self._stdscr.noutrefresh()
            self._layout = None
        height, width = self._stdscr.getmaxyx()
        # A trailing space reserves the cell where the cursor sits after the last character.
        input_rows = wrap(self._input.text + " ", max(1, width - 2))
        try:
            layout = compute_layout(height, width, len(input_rows))
        except ScreenTooSmall:
            self._draw_too_small(width)
            return
        if layout != self._layout:
            self._layout = layout
            self._boxes = (_box(layout.chat_box), _box(layout.input_box))
            self._pads = (_pad(layout.chat_pad), _pad(layout.input_pad))
        assert self._boxes is not None and self._pads is not None
        chat_box, input_box = self._boxes
        chat_pad, input_pad = self._pads
        chat_box.addnstr(0, 2, f" {self._title} ", max(0, layout.chat_box.width - 4))
        chat_box.noutrefresh()
        input_box.noutrefresh()

        self._draw_chat(chat_pad, layout.chat_pad)
        input_top = max(0, len(input_rows) - layout.input_pad.height)
        _paint(input_pad, input_rows[input_top:], layout.input_pad)

        last_row = input_rows[-1][:-1]
        cursor_y = layout.input_pad.top + len(input_rows) - 1 - input_top
        cursor_x = layout.input_pad.left + sum(cell_width(c) for c in last_row)
        curses.setsyx(cursor_y, cursor_x)
        curses.doupdate()

    def _draw_chat(self, pad: curses.window, area: Rect) -> None:
        if area.width != self._chat_width:
            self._chat_width = area.width
            self._chat_rows = [row for line in self._chat for row in wrap(line, area.width)]
        top = self._chat_view.top(len(self._chat_rows), area.height)
        _paint(pad, self._chat_rows[top : top + area.height], area)

    def _draw_too_small(self, width: int) -> None:
        self._repaint = True
        self._stdscr.erase()
        try:
            self._stdscr.addnstr(0, 0, _TOO_SMALL, max(0, width - 1))
        except curses.error:
            pass
        self._stdscr.refresh()


def _write_terminal(sequence: str) -> None:
    """Send a control sequence straight to the terminal, bypassing curses."""
    sys.stdout.write(sequence)
    sys.stdout.flush()


def _box(rect: Rect) -> curses.window:
    window = curses.newwin(rect.height, rect.width, rect.top, rect.left)
    window.border()
    return window


def _pad(area: Rect) -> curses.window:
    # Sized to the visible area (one spare column so a full-width row never pushes the cursor
    # out), so drawing costs the same however long the Chat gets.
    return curses.newpad(area.height, area.width + 1)


def _paint(pad: curses.window, rows: list[str], area: Rect) -> None:
    """Show ``rows`` (already the visible slice) in ``area``."""
    pad.erase()
    for y, row in enumerate(rows[: area.height]):
        try:
            pad.addstr(y, 0, row)
        except curses.error:
            pass  # writing the very last cell fails after drawing it, e.g. a wide character
    bottom, right = area.top + area.height - 1, area.left + area.width - 1
    pad.noutrefresh(0, 0, area.top, area.left, bottom, right)
