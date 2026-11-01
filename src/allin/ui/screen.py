"""Chat + input screen shared by Main and the Window.

Two ``newwin`` boxes only draw borders; the content lives in one pad per box (Chat and input),
placed by ``compute_layout`` and scrolled through a ``Viewport``. Only the UI thread touches
curses; other threads hand it updates through ``inbox``.
"""

import curses
import queue
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from allin.core.errors import ScreenTooSmall
from allin.core.ids import BoxId
from allin.ui.boxes import VIEW_KEYS, Box, BoxClosed, BoxStack, box_rows
from allin.ui.chat import Chat, Row
from allin.ui.draft import DraftReporter
from allin.ui.input_buffer import InputBuffer
from allin.ui.keys import Key, KeyChord, KeyDecoder, KeyEvent, Paste
from allin.ui.layout import Layout, Rect, compute_layout
from allin.ui.sanitize import visible
from allin.ui.viewport import Viewport
from allin.ui.wrap import cell_width, wrap

_BOX_MAX_WIDTH = 64
_BOX_MIN_WIDTH = 16
_BOX_MIN_HEIGHT = 3
_POLL_MS = 50  # curses has no blocking wait on both keys and the inbox
_BRACKETED_PASTE_ON = "\x1b[?2004h"
_BRACKETED_PASTE_OFF = "\x1b[?2004l"
_TOO_SMALL = "Terminal too small: please enlarge it"
_LOCKED = " turn running: Enter is blocked "
_WAITING = " +{} waiting "  # Draft blocks behind the shown one
_ROLE_COLORS = {
    "user": curses.COLOR_CYAN,
    "reasoning": curses.COLOR_MAGENTA,
    "answer": curses.COLOR_GREEN,
    "info": curses.COLOR_YELLOW,
    "error": curses.COLOR_RED,
}


@dataclass(frozen=True)
class ChatLine:
    """A whole entry for the Chat."""

    text: str
    role: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class ChatStream:
    """Streamed text: extends the Chat's open entry of the same role and label."""

    text: str
    role: str
    label: str


@dataclass(frozen=True)
class InputLock:
    """Block or unblock Enter (the text being typed is kept); unblocking closes the open entry."""

    locked: bool


@dataclass(frozen=True)
class BorderStatus:
    """Text shown on the Chat's border, after the title; it replaces the previous one."""

    text: str


@dataclass(frozen=True)
class TitleMark:
    """A mark shown on the Chat's border before the title, such as ``--devastation``."""

    text: str


@dataclass(frozen=True)
class InputText:
    """A Prompt put back in the input: shown, while whatever was there waits behind it."""

    text: str


@dataclass(frozen=True)
class ShowBox:
    """Show a Notice or Confirmation over the Chat; it waits behind any already shown."""

    box: Box


@dataclass(frozen=True)
class HideBox:
    """Drop a box answered elsewhere or no longer valid."""

    box_id: BoxId


# A plain str is a ChatLine with no role; None asks the screen to quit.
type ScreenUpdate = (
    str
    | ChatLine
    | ChatStream
    | InputLock
    | BorderStatus
    | TitleMark
    | InputText
    | ShowBox
    | HideBox
    | None
)
type OnBoxClosed = Callable[[BoxClosed], None]


class ChatScreen:
    def __init__(self, stdscr: curses.window, title: str) -> None:
        self._stdscr = stdscr
        self._title = visible(title)
        self._status = ""
        self._mark = ""
        self._chat = Chat()
        self._chat_rows: list[Row] = []  # self._chat wrapped to the Chat pad's width
        self._chat_view = Viewport()
        self._role_attrs: dict[str, int] = {}
        self._locked = False
        self._input = InputBuffer()
        self._keys = KeyDecoder()
        self._boxes_shown = BoxStack()
        self._on_box_closed: OnBoxClosed = lambda _: None
        self._layout: Layout | None = None
        self._boxes: tuple[curses.window, curses.window] | None = None
        self._pads: tuple[curses.window, curses.window] | None = None
        self._dirty = True
        self._repaint = True  # repaint the whole terminal, not just what changed
        self.inbox: queue.Queue[ScreenUpdate] = queue.Queue()

    def lock_input(self) -> None:
        """Block Enter from the UI thread, e.g. right after a submit; ``InputLock`` undoes it."""
        self._locked = True
        self._dirty = True

    def run(
        self,
        on_submit: Callable[[str], None],
        on_draft: Callable[[tuple[str, ...]], None] | None = None,
        on_box_closed: OnBoxClosed | None = None,
    ) -> None:
        """Run the UI loop until ``None`` arrives in the inbox.

        ``on_draft`` gets the input's Draft once the user stops typing for a moment, and at
        once when a block is sent while others wait. ``on_box_closed`` gets each answer to a
        box.
        """
        if on_box_closed is not None:
            self._on_box_closed = on_box_closed
        self._stdscr.keypad(True)
        self._stdscr.timeout(_POLL_MS)
        self._role_attrs = _role_attrs()
        _write_terminal(_BRACKETED_PASTE_ON)
        try:
            self._loop(on_submit, DraftReporter(on_draft) if on_draft else None)
        finally:
            _write_terminal(_BRACKETED_PASTE_OFF)

    def _loop(self, on_submit: Callable[[str], None], draft: DraftReporter | None) -> None:
        while True:
            if not self._drain_inbox():
                return
            if draft is not None:
                draft.update(self._input.blocks, time.monotonic())
            if self._dirty:
                self._draw()
            try:
                key = self._stdscr.get_wch()
            except curses.error:
                self._keys.flush()
                continue
            for chord in self._keys.feed(key):
                self._dirty = True
                self._handle(chord, on_submit, draft)

    def _handle(
        self, chord: KeyEvent, on_submit: Callable[[str], None], draft: DraftReporter | None
    ) -> None:
        if self._boxes_shown.takes_keys and not _is_view_key(chord):
            closed = self._boxes_shown.key(chord)
            if closed is not None:
                self._on_box_closed(closed)
            return
        match chord:
            case Paste(text=text):
                self._input.insert(text)
            case KeyChord(key=Key.RESIZE):
                curses.update_lines_cols()
                self._repaint = True
            case KeyChord(key=Key.ENTER, ctrl=True):
                self._input.insert("\n")
            case KeyChord(key=Key.ENTER) if not self._locked:
                text = self._input.take()  # an empty block is dropped: the next one shows
                if text.strip():
                    on_submit(text)
                    if draft is not None and self._input.blocks:
                        # So a Main that dies right after keeps the blocks still waiting.
                        draft.report_now(self._input.blocks, time.monotonic())
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
                update = self.inbox.get_nowait()
            except queue.Empty:
                return True
            match update:
                case None:
                    return False
                case str() as text:
                    self._chat.add(visible(text))
                case ChatLine(text, role, label):
                    self._chat.add(visible(text), role, _visible_label(label))
                case ChatStream(text, role, label):
                    self._chat.stream(visible(text), role, visible(label))
                case InputLock(locked):
                    self._locked = locked
                    if not locked:  # the Turn is over; a lock must not split a replayed stream
                        self._chat.end_stream()
                case BorderStatus(text):
                    self._status = visible(text)
                case TitleMark(text):
                    self._mark = visible(text)
                case InputText(text):
                    self._input.restore(text)
                case ShowBox(box):
                    self._boxes_shown.show(box)
                case HideBox(box_id):
                    self._boxes_shown.hide(box_id)
            self._dirty = True

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
        input_rows = wrap(visible(self._input.text) + " ", max(1, width - 2))
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
        chat_box.border()  # wipes a longer status drawn before
        # The mark goes first: a long title or status never hides it.
        parts = [part for part in (self._mark, self._title, self._status) if part]
        title = " " + " ─ ".join(parts) + " "
        chat_box.addnstr(0, 2, title, max(0, layout.chat_box.width - 4))
        chat_box.noutrefresh()
        input_box.border()
        if self._locked:
            input_box.addnstr(0, 2, _LOCKED, max(0, layout.input_box.width - 4))
        if self._input.waiting:
            waiting = _WAITING.format(len(self._input.waiting))
            x = max(2, layout.input_box.width - 2 - len(waiting))
            input_box.addnstr(0, x, waiting, max(0, layout.input_box.width - 2 - x))
        input_box.noutrefresh()

        self._draw_chat(chat_pad, layout.chat_pad)
        input_top = max(0, len(input_rows) - layout.input_pad.height)
        _paint(input_pad, [Row(None, row) for row in input_rows[input_top:]], layout.input_pad)

        self._draw_box(layout.chat_pad)
        last_row = input_rows[-1][:-1]
        cursor_y = layout.input_pad.top + len(input_rows) - 1 - input_top
        cursor_x = layout.input_pad.left + sum(cell_width(c) for c in last_row)
        curses.setsyx(cursor_y, cursor_x)
        curses.doupdate()

    def _draw_chat(self, pad: curses.window, area: Rect) -> None:
        self._chat_rows = self._chat.rows(area.width)
        top = self._chat_view.top(len(self._chat_rows), area.height)
        _paint(pad, self._chat_rows[top : top + area.height], area, self._role_attrs)

    def _draw_box(self, area: Rect) -> None:
        """The shown box, centred over the Chat, on top of what was drawn."""
        box = self._boxes_shown.current
        if box is None:
            return
        width = min(area.width, _BOX_MAX_WIDTH)
        rows = box_rows(Box(box.box_id, box.kind, box.title, visible(box.text)), width - 4)
        height = min(area.height, len(rows) + 2)
        if width < _BOX_MIN_WIDTH or height < _BOX_MIN_HEIGHT:
            return
        top = area.top + (area.height - height) // 2
        left = area.left + (area.width - width) // 2
        window = curses.newwin(height, width, top, left)
        window.erase()
        window.border()
        title = f" {visible(box.title)} "
        window.addnstr(0, 2, title, max(0, width - 4), curses.A_BOLD)
        for y, row in enumerate(rows[: height - 2], start=1):
            attr = curses.A_BOLD if y == len(rows) else curses.A_NORMAL
            with suppress(curses.error):
                window.addnstr(y, 2, row, width - 4, attr)
        window.noutrefresh()
        # The Chat pad is drawn again next time, so the box leaves nothing behind once gone.
        self._layout = None

    def _draw_too_small(self, width: int) -> None:
        self._repaint = True
        self._stdscr.erase()
        with suppress(curses.error):
            self._stdscr.addnstr(0, 0, _TOO_SMALL, max(0, width - 1))
        self._stdscr.refresh()


def _is_view_key(chord: KeyEvent) -> bool:
    """Keys that change only the view: they work while a box waits for its answer."""
    return isinstance(chord, KeyChord) and chord.key in VIEW_KEYS


def _visible_label(label: str | None) -> str | None:
    return None if label is None else visible(label)


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


def _role_attrs() -> dict[str, int]:
    """The curses attribute that colours each Chat role; none on a terminal without colours."""
    if not curses.has_colors():
        return {}
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1  # the terminal's own
    except curses.error:
        background = curses.COLOR_BLACK
    attrs: dict[str, int] = {}
    for pair, (role, colour) in enumerate(_ROLE_COLORS.items(), start=1):
        curses.init_pair(pair, colour, background)
        attrs[role] = curses.color_pair(pair)
    attrs["reasoning"] |= curses.A_DIM
    return attrs


def _paint(
    pad: curses.window, rows: list[Row], area: Rect, role_attrs: dict[str, int] | None = None
) -> None:
    """Show ``rows`` (already the visible slice) in ``area``."""
    pad.erase()
    for y, row in enumerate(rows[: area.height]):
        attr = (role_attrs or {}).get(row.role or "", curses.A_NORMAL)
        # Writing the very last cell fails after drawing it, e.g. a wide character.
        with suppress(curses.error):
            pad.addstr(y, 0, row.text, attr)
    bottom, right = area.top + area.height - 1, area.left + area.width - 1
    pad.noutrefresh(0, 0, area.top, area.left, bottom, right)
