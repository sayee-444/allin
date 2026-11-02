"""Chat + input screen shared by Main and the Window.

Two ``newwin`` boxes only draw borders; the content lives in one pad per box (Chat and input),
placed by ``compute_layout`` and scrolled through a ``Viewport``. Only the UI thread touches
curses; other threads hand it updates through ``inbox``.
"""

import curses
import os
import queue
import shutil
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from allin.core.errors import AllInError, ScreenTooSmall
from allin.core.ids import BoxId
from allin.core.themes import AUTO, NAMED_COLORS, Theme, nearest_256, rgb
from allin.ui.boxes import VIEW_KEYS, Box, BoxClosed, BoxStack, box_rows
from allin.ui.chat import Chat, Row
from allin.ui.clipboard import Clipboard, system_clipboard
from allin.ui.draft import DraftReporter
from allin.ui.input_buffer import InputBuffer, cursor_place
from allin.ui.keys import Key, KeyChord, KeyDecoder, KeyEvent, Paste, typed_char
from allin.ui.layout import Layout, Rect, compute_layout
from allin.ui.look import Terminal, frame_chars, restore_sequence, terminal_changes
from allin.ui.mouse import (
    DRAG_REPORTS_OFF,
    DRAG_REPORTS_ON,
    MOUSE_MASK,
    Mouse,
    MouseAction,
    mouse_event,
)
from allin.ui.sanitize import visible
from allin.ui.selection import Point, Selection, autoscroll_rows, char_index
from allin.ui.shortcuts import DEFAULT_KEYS, Action, Command, resolve
from allin.ui.viewport import Viewport
from allin.ui.wrap import cell_width, wrap_rows

_BOX_MAX_WIDTH = 64
_BOX_MIN_WIDTH = 16
_BOX_MIN_HEIGHT = 3
_POLL_MS = 50  # curses has no blocking wait on both keys and the inbox
_BRACKETED_PASTE_ON = "\x1b[?2004h"
_BRACKETED_PASTE_OFF = "\x1b[?2004l"
_TOO_SMALL = "Terminal too small: please enlarge it"
_LOCKED = " read only "  # Main is gone: nothing typed reaches it
_WAITING = " +{} waiting "  # Draft blocks behind the shown one
_WHEEL_ROWS = 3


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
    """Block Enter for good once Main is gone (the text being typed is kept); unblocking, as a
    Turn ends, closes the open entry."""

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
class ApplyTheme:
    """The Theme Main resolved for this screen, applied as it is (#47)."""

    theme: Theme
    terminal: Terminal


@dataclass(frozen=True)
class ClearChat:
    """Every entry of the Chat gone: a rebuilt one follows (#58)."""


@dataclass(frozen=True)
class KeyBindings:
    """The screen's Atajos, key name to action or Command main (``ui.shortcuts``) (#57)."""

    table: dict[str, str]


@dataclass(frozen=True)
class QueuedPrompts:
    """The Prompts sent while a Turn ran, waiting for theirs: shown below the Chat, and Up
    on the input's first row withdraws the last one (ADR-0024)."""

    texts: tuple[str, ...]


@dataclass(frozen=True)
class InputHistory:
    """The Session's earlier Prompts, oldest first, which Up brings back to the input."""

    prompts: tuple[str, ...]


@dataclass(frozen=True)
class ShowBox:
    """Show a Notice or Confirmation over the Chat; it waits behind any already shown."""

    box: Box


@dataclass(frozen=True)
class HideBox:
    """Drop a box answered elsewhere or no longer valid."""

    box_id: BoxId


type PadName = Literal["chat", "input"]

# A plain str is a ChatLine with no role; None asks the screen to quit.
type ScreenUpdate = (
    str
    | ChatLine
    | ChatStream
    | InputLock
    | BorderStatus
    | TitleMark
    | InputText
    | InputHistory
    | QueuedPrompts
    | KeyBindings
    | ApplyTheme
    | ClearChat
    | ShowBox
    | HideBox
    | None
)
type OnBoxClosed = Callable[[BoxClosed], None]


class ChatScreen:
    def __init__(
        self,
        stdscr: curses.window,
        title: str,
        clipboard: Clipboard | None = None,
        commands: bool = False,
    ) -> None:
        """``commands``: whether an Atajo may run a Command main, sent as ``on_submit`` would
        send what was typed (Main's Chat); a Window takes none until F2."""
        self._stdscr = stdscr
        self._commands = commands
        self._bindings: dict[str, str] = dict(DEFAULT_KEYS)
        self._clipboard = clipboard or system_clipboard(os.environ, shutil.which)
        self._title = visible(title)
        self._status = ""
        self._mark = ""
        self._chat = Chat()
        self._chat_rows: list[Row] = []  # self._chat wrapped to the Chat pad's width
        self._chat_view = Viewport()
        self._role_attrs: dict[str, int] = {}
        # What the Theme applied gives (#47): the border's characters and colour, the status's
        # colour and the boxes' fixed pair, which no Theme changes.
        self._theme: Theme | None = None
        self._terminal: Terminal = "window"
        self._frame = frame_chars("single", "unicode")
        self._border_attr = curses.A_NORMAL
        self._status_attr = curses.A_NORMAL
        self._box_attr = curses.A_NORMAL
        self._locked = False
        self._input = InputBuffer()
        self._input_rows: list[Row] = []  # the input wrapped to its pad's width
        self._input_view = Viewport()
        self._reveal_cursor = True  # bring the cursor's row into view on the next draw
        # Text selected with the mouse, in one pad; _dragging while its button is held, at
        # _pointer (y, x) once it moved, which may be outside the pad (autoscroll).
        self._selection = Selection()
        self._selecting: PadName | None = None
        self._dragging = False
        self._pointer: tuple[int, int] | None = None
        self._scrolled_at = 0.0  # when the last autoscroll tick ran
        self._keys = KeyDecoder()
        self._boxes_shown = BoxStack()
        self._on_box_closed: OnBoxClosed = lambda _: None
        self._on_interrupt: Callable[[str], None] | None = None
        self._on_withdraw: Callable[[], None] | None = None
        self._on_click: Callable[[], None] | None = None
        self._queued: tuple[str, ...] = ()
        self._layout: Layout | None = None
        self._shown: Layout | None = None  # where the pads are on screen now, for the mouse
        self._boxes: tuple[curses.window, curses.window] | None = None
        self._pads: tuple[curses.window, curses.window] | None = None
        self._dirty = True
        self._repaint = True  # repaint the whole terminal, not just what changed
        self.inbox: queue.Queue[ScreenUpdate] = queue.Queue()

    def run(
        self,
        on_submit: Callable[[str], None],
        on_draft: Callable[[tuple[str, ...]], None] | None = None,
        on_box_closed: OnBoxClosed | None = None,
        on_interrupt: Callable[[str], None] | None = None,
        on_withdraw: Callable[[], None] | None = None,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        """Run the UI loop until ``None`` arrives in the inbox.

        ``on_draft`` gets the input's Draft once the user stops typing for a moment, and at
        once when a block is sent while others wait. ``on_box_closed`` gets each answer to a
        box. ``on_interrupt`` gets the input's block on Shift+Enter, even an empty one, and
        ``on_withdraw`` is called on Up while Prompts are queued (ADR-0024); without them,
        Shift+Enter is Enter and Up only browses the history. ``on_click`` is called on each
        mouse press (#52).
        """
        if on_box_closed is not None:
            self._on_box_closed = on_box_closed
        self._on_interrupt, self._on_withdraw = on_interrupt, on_withdraw
        self._on_click = on_click
        self._stdscr.keypad(True)
        self._stdscr.timeout(_POLL_MS)
        self._role_attrs, self._border_attr, self._status_attr, self._box_attr = _theme_attrs(
            Theme(), "window"
        )
        curses.mousemask(MOUSE_MASK)
        curses.mouseinterval(0)  # presses and releases as they come, never made into clicks
        _write_terminal(_BRACKETED_PASTE_ON + DRAG_REPORTS_ON)
        try:
            self._loop(on_submit, DraftReporter(on_draft) if on_draft else None)
        finally:
            restore = restore_sequence(self._theme) if self._terminal == "main" else ""
            _write_terminal(_BRACKETED_PASTE_OFF + DRAG_REPORTS_OFF + restore)

    def _loop(self, on_submit: Callable[[str], None], draft: DraftReporter | None) -> None:
        while True:
            if not self._drain_inbox():
                return
            now = time.monotonic()
            if draft is not None:
                draft.update(self._input.blocks, now)
            self._autoscroll(now)
            if self._dirty:
                self._draw()
            try:
                key = self._stdscr.get_wch()
            except curses.error:
                self._keys.flush()
                continue
            if key == curses.KEY_MOUSE:
                self._mouse()
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
        shortcut = resolve(chord, self._bindings) if isinstance(chord, KeyChord) else None
        if self._locked and not read_only_allows(chord, shortcut):
            return  # Main is gone: the input stays as it was, which Main kept as a Draft (#33)
        if not _is_view_key(chord) or chord == KeyChord(Key.RESIZE):
            self._clear_selection()  # a key that edits, or new rows: its places are gone
        if shortcut is not None:
            self._run_shortcut(shortcut, on_submit, draft)
            self._reveal_cursor = True
            return
        match chord:
            case Paste(text=text):
                self._input.insert(text)
            case KeyChord(key=Key.RESIZE):
                curses.update_lines_cols()
                self._repaint = True
            case KeyChord(key=Key.BACKSPACE, ctrl=True):
                self._input.delete_word()
            case KeyChord(key=Key.BACKSPACE):
                self._input.backspace()
            case KeyChord(key=Key.DELETE):
                self._input.delete()
            case KeyChord(key=Key.LEFT):
                self._input.left()
            case KeyChord(key=Key.RIGHT):
                self._input.right()
            case KeyChord(key=Key.HOME):
                self._input.home()
            case KeyChord(key=Key.END):
                self._input.end()
            case KeyChord() if (char := typed_char(chord)) is not None:
                self._input.insert(char)
            case _:
                pass
        self._reveal_cursor = True

    def _run_shortcut(
        self,
        shortcut: Action | Command,
        on_submit: Callable[[str], None],
        draft: DraftReporter | None,
    ) -> None:
        """Do what an Atajo runs (#57)."""
        match shortcut:
            case Command(text=text):
                if self._commands:  # a Command main, with the human's Origin
                    on_submit(text)
            case "send" | "interrupt" if self._locked:
                pass
            case "interrupt" if self._on_interrupt is not None:
                text = self._input.take()
                if text.strip():
                    self._input.remember(text)
                self._on_interrupt(text if text.strip() else "")
            case "send" | "interrupt":  # with nothing running here to interrupt, it sends
                text = self._input.take()  # an empty block is dropped: the next one shows
                if text.strip():
                    self._input.remember(text)
                    on_submit(text)
                    if draft is not None and self._input.blocks:
                        # So a Main that dies right after keeps the blocks still waiting.
                        draft.report_now(self._input.blocks, time.monotonic())
            case "newline":
                self._input.insert("\n")
            case "withdraw":
                if self._queued and self._on_withdraw is not None:
                    self._on_withdraw()
            case "up":
                if self._input.up(self._input_width()):
                    pass
                elif self._queued and self._on_withdraw is not None:  # the queue goes first
                    self._on_withdraw()
                else:  # on the first row: the history
                    self._input.older()
            case "down":
                if not self._input.down(self._input_width()):
                    self._input.newer()
            case "older":
                self._input.older()
            case "newer":
                self._input.newer()
            case "scroll_up" | "scroll_down":
                self._scroll_chat(-1 if shortcut == "scroll_up" else 1)
            case "paste":
                self._paste()

    def _input_width(self) -> int:
        """The columns the input wraps at, as ``_draw`` wraps it."""
        return max(1, self._stdscr.getmaxyx()[1] - 2)

    def _paste(self) -> None:
        """Ctrl+V: the clipboard's text into the input, as a paste from the terminal."""
        try:
            self._input.insert(self._clipboard.paste())
        except AllInError as error:
            self._chat.add(visible(str(error)), "error", "error")

    def _mouse(self) -> None:
        try:
            _, x, y, _, bstate = curses.getmouse()
        except curses.error:
            return  # a report curses could not read
        event = mouse_event(x, y, bstate)
        if event is None or self._shown is None:
            return
        self._dirty = True
        match event.action:
            case MouseAction.WHEEL_UP | MouseAction.WHEEL_DOWN:
                pad = _pad_at(self._shown, event)
                if pad is not None:
                    up = event.action == MouseAction.WHEEL_UP
                    self._scroll(pad, -_WHEEL_ROWS if up else _WHEEL_ROWS)
            case MouseAction.PRESS:
                if self._on_click is not None:
                    self._on_click()
                self._clear_selection()
                self._selecting = _pad_at(self._shown, event)
                if self._selecting is not None:
                    self._dragging = True
                    self._selection.start(self._point(self._selecting, event.y, event.x))
            case MouseAction.DRAG:
                if self._dragging and self._selecting is not None:
                    self._pointer = (event.y, event.x)
                    self._selection.extend(self._point(self._selecting, event.y, event.x))
            case MouseAction.RELEASE:
                self._dragging, self._pointer = False, None
                if self._selection.active:
                    self._copy()

    def _autoscroll(self, now: float) -> None:
        """While a drag holds the pointer above or below its pad, scroll it once a tick,
        faster the further out, and take the selection along."""
        if not self._dragging or self._pointer is None or self._selecting is None:
            return
        if now - self._scrolled_at < _POLL_MS / 1000:
            return
        self._scrolled_at = now
        area = self._area(self._selecting)
        if area is None:
            return
        y, x = self._pointer
        rows = autoscroll_rows(y, area.top, area.height)
        if rows:
            self._scroll(self._selecting, rows)
            self._selection.extend(self._point(self._selecting, y, x))
            self._dirty = True

    def _copy(self) -> None:
        text = self._selection.text(self._selectable_rows())
        try:
            self._clipboard.copy(text)
        except AllInError as error:
            self._chat.add(visible(str(error)), "error", "error")

    def _selectable_rows(self) -> list[Row]:
        if self._selecting == "chat":
            return self._chat_rows
        rows = list(self._input_rows)
        if rows:  # its last cell is where the cursor sits, not text
            last = rows[-1]
            rows[-1] = Row(last.role, last.text.removesuffix(" "), last.joined)
        return rows

    def _clear_selection(self) -> None:
        self._selection.clear()
        self._selecting, self._dragging, self._pointer = None, False, None

    def _point(self, pad: PadName, y: int, x: int) -> Point:
        """The character of ``pad`` at the terminal's cell ``(y, x)``, or the nearest one
        in view if it is outside the pad."""
        area = self._area(pad)
        rows = self._chat_rows if pad == "chat" else self._input_rows
        if area is None or not rows:
            return Point(0, 0)
        view = self._chat_view if pad == "chat" else self._input_view
        top = view.top(len(rows), area.height)
        row = min(top + min(max(y - area.top, 0), area.height - 1), len(rows) - 1)
        return Point(row, char_index(rows[row].text, max(0, x - area.left)))

    def _area(self, pad: PadName) -> Rect | None:
        if self._shown is None:
            return None
        return self._shown.chat_pad if pad == "chat" else self._shown.input_pad

    def _scroll(self, pad: PadName, rows: int) -> None:
        area = self._area(pad)
        if area is None:
            return
        if pad == "chat":
            self._chat_view.scroll(rows, len(self._chat_rows), area.height)
        else:
            self._input_view.scroll(rows, len(self._input_rows), area.height)

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
                    self._reveal_cursor = True
                case InputHistory(prompts):
                    self._input.load_history(prompts)
                case ApplyTheme(theme, terminal):
                    self._apply_theme(theme, terminal)
                case ClearChat():
                    self._chat.clear()
                    self._clear_selection()
                case KeyBindings(table):
                    self._bindings = dict(table)
                case QueuedPrompts(texts):
                    self._queued = texts
                    self._chat.set_queued(tuple(visible(text) for text in texts), "queued")
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
        text = visible(self._input.text) + " "
        input_rows = [Row(None, row, joined) for row, joined in wrap_rows(text, max(1, width - 2))]
        self._input_rows = input_rows
        try:
            layout = compute_layout(height, width, len(input_rows))
        except ScreenTooSmall:
            self._shown = None
            self._draw_too_small(width)
            return
        self._shown = layout
        if layout != self._layout:
            self._layout = layout
            self._boxes = (_box(layout.chat_box), _box(layout.input_box))
            self._pads = (_pad(layout.chat_pad), _pad(layout.input_pad))
        assert self._boxes is not None and self._pads is not None
        chat_box, input_box = self._boxes
        chat_pad, input_pad = self._pads
        self._draw_frame(chat_box)  # wipes a longer status drawn before
        # The mark goes first: a long title or status never hides it.
        parts = [part for part in (self._mark, self._title, self._status) if part]
        title = " " + f" {self._frame[2]} ".join(parts) + " "
        chat_box.addnstr(0, 2, title, max(0, layout.chat_box.width - 4), self._status_attr)
        chat_box.noutrefresh()
        self._draw_frame(input_box)
        if self._locked:
            input_box.addnstr(0, 2, _LOCKED, max(0, layout.input_box.width - 4))
        if self._input.waiting:
            waiting = _WAITING.format(len(self._input.waiting))
            x = max(2, layout.input_box.width - 2 - len(waiting))
            input_box.addnstr(0, x, waiting, max(0, layout.input_box.width - 2 - x))
        input_box.noutrefresh()

        self._draw_chat(chat_pad, layout.chat_pad)
        input_area = layout.input_pad
        cursor_row, cursor_cell = cursor_place(self._input.text, self._input.cursor, width - 2)
        if self._reveal_cursor:  # a key was pressed: the cursor comes back into view
            self._reveal_cursor = False
            self._input_view.show(cursor_row, len(input_rows), input_area.height)
        input_top = self._input_view.top(len(input_rows), input_area.height)
        shown = input_rows[input_top : input_top + input_area.height]
        _paint(input_pad, shown, input_area, first=input_top, selection=self._selected("input"))

        self._draw_box(layout.chat_pad)
        # Scrolled away with the wheel, the cursor waits on the nearest row in view.
        shown_row = min(max(cursor_row - input_top, 0), input_area.height - 1)
        curses.setsyx(input_area.top + shown_row, input_area.left + cursor_cell)
        curses.doupdate()

    def _draw_chat(self, pad: curses.window, area: Rect) -> None:
        self._chat_rows = self._chat.rows(area.width)
        top = self._chat_view.top(len(self._chat_rows), area.height)
        rows = self._chat_rows[top : top + area.height]
        _paint(pad, rows, area, self._role_attrs, first=top, selection=self._selected("chat"))

    def _selected(self, pad: PadName) -> Selection | None:
        return self._selection if self._selecting == pad else None

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
        # The same high-contrast pair under any Theme, so none hides a Confirmation (#47).
        window.bkgd(" ", self._box_attr)
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

    def _apply_theme(self, theme: Theme, terminal: Terminal) -> None:
        sequence = terminal_changes(self._theme, theme, terminal, xterm=_in_xterm())
        self._theme, self._terminal = theme, terminal
        attrs = _theme_attrs(theme, terminal)
        self._role_attrs, self._border_attr, self._status_attr, self._box_attr = attrs
        self._frame = frame_chars(theme.border.style, theme.glyphs.set)
        self._chat.set_icons({str(role): icon for role, icon in theme.glyphs.icons.items()})
        if sequence:
            _write_terminal(sequence)
        self._layout = None  # every box is drawn again, with the new border
        self._repaint = True

    def _draw_frame(self, window: curses.window) -> None:
        """A box's border in the Theme's style and colour. ``border()`` takes no Unicode
        corners, so the other styles are written as text; the very last cell fails after
        drawing its character, as in ``_paint``."""
        height, width = window.getmaxyx()
        left, right, top, bottom, top_left, top_right, bottom_left, bottom_right = self._frame
        attr = self._border_attr
        if self._frame == frame_chars("single", "unicode"):
            window.attrset(attr)
            window.border()
            window.attrset(curses.A_NORMAL)
            return
        with suppress(curses.error):
            window.addstr(0, 0, top_left + top * (width - 2) + top_right, attr)
            for y in range(1, height - 1):
                window.addstr(y, 0, left, attr)
                window.addstr(y, width - 1, right, attr)
            window.addstr(height - 1, 0, bottom_left + bottom * (width - 2), attr)
        with suppress(curses.error):
            window.addstr(height - 1, width - 1, bottom_right, attr)

    def _draw_too_small(self, width: int) -> None:
        self._repaint = True
        self._stdscr.erase()
        with suppress(curses.error):
            self._stdscr.addnstr(0, 0, _TOO_SMALL, max(0, width - 1))
        self._stdscr.refresh()


def _pad_at(layout: Layout, event: Mouse) -> PadName | None:
    pads: tuple[tuple[PadName, Rect], ...] = (
        ("chat", layout.chat_pad),
        ("input", layout.input_pad),
    )
    for name, area in pads:
        inside_rows = area.top <= event.y < area.top + area.height
        if inside_rows and area.left <= event.x < area.left + area.width:
            return name
    return None


def read_only_allows(chord: KeyEvent, shortcut: Action | Command | None) -> bool:
    """Whether a read-only screen takes this key: only those that move the view do (#33)."""
    return chord == KeyChord(Key.RESIZE) or shortcut in ("scroll_up", "scroll_down")


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
    return curses.newwin(rect.height, rect.width, rect.top, rect.left)


def _pad(area: Rect) -> curses.window:
    # Sized to the visible area (one spare column so a full-width row never pushes the cursor
    # out), so drawing costs the same however long the Chat gets.
    return curses.newpad(area.height, area.width + 1)


_ROLES = ("user", "reasoning", "answer", "info", "error")
_BORDER_PAIR, _STATUS_PAIR, _BOX_PAIR = 6, 7, 8
_FIRST_FREE_COLOR = 16  # a Window's own colours go from here on: the 16 with a name stay


def _theme_attrs(theme: Theme, terminal: Terminal) -> tuple[dict[str, int], int, int, int]:
    """The curses attributes a Theme gives: each Chat role's, the border's, the status's, and
    the boxes' fixed pair. A Window's xterm redefines colours for each ``#rrggbb``; Main's
    Chat takes the nearest of the terminal's 256, never redefining the human's palette, which
    a ``kill -9`` would leave changed. None on a terminal without colours."""
    if not curses.has_colors():
        return {}, curses.A_NORMAL, curses.A_NORMAL, curses.A_REVERSE
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1  # the terminal's own, which the Theme sets in xterm itself
    except curses.error:
        background = curses.COLOR_BLACK
    own = terminal == "window" and curses.can_change_color() and curses.COLORS >= 256
    redefined: dict[str, int] = {}

    def color(value: str) -> int:
        if value in NAMED_COLORS:
            index = list(NAMED_COLORS).index(value)
            return index if index < curses.COLORS else index % 8
        if own:
            if value not in redefined:
                index = _FIRST_FREE_COLOR + len(redefined)
                red, green, blue = (int(value[n : n + 2], 16) * 1000 // 255 for n in (1, 3, 5))
                curses.init_color(index, red, green, blue)
                redefined[value] = index
            return redefined[value]
        nearest = nearest_256(value)
        return nearest if nearest < curses.COLORS else _nearest_8(value)

    colors = theme.colors
    attrs: dict[str, int] = {}
    for pair, role in enumerate(_ROLES, start=1):
        curses.init_pair(pair, color(getattr(colors, role)), background)
        attrs[role] = curses.color_pair(pair)
    attrs["reasoning"] |= curses.A_DIM
    border = curses.A_NORMAL  # ``auto`` not resolved yet: the terminal's own, as before
    if theme.border.color != AUTO:
        curses.init_pair(_BORDER_PAIR, color(theme.border.color), background)
        border = curses.color_pair(_BORDER_PAIR)
    status = curses.A_NORMAL
    if colors.status is not None:
        curses.init_pair(_STATUS_PAIR, color(colors.status), background)
        status = curses.color_pair(_STATUS_PAIR)
    curses.init_pair(_BOX_PAIR, curses.COLOR_BLACK, curses.COLOR_WHITE)
    return attrs, border, status, curses.color_pair(_BOX_PAIR)


def _nearest_8(value: str) -> int:
    """The nearest of the 8 basic colours, for a terminal with no more."""
    target = rgb(value)
    eight = list(NAMED_COLORS.items())[:8]

    def distance(named: str) -> int:
        return sum((int(target[n : n + 2], 16) - int(named[n : n + 2], 16)) ** 2 for n in (1, 3, 5))

    return min(range(8), key=lambda index: distance(eight[index][1]))


def _in_xterm() -> bool:
    """Whether Main's Chat runs in an xterm, the only terminal whose letter it changes."""
    return "XTERM_VERSION" in os.environ


def _paint(
    pad: curses.window,
    rows: list[Row],
    area: Rect,
    role_attrs: dict[str, int] | None = None,
    first: int = 0,
    selection: Selection | None = None,
) -> None:
    """Show ``rows`` (already the visible slice, from row ``first`` on) in ``area``, with
    what ``selection`` holds of them in reverse video."""
    pad.erase()
    for y, row in enumerate(rows[: area.height]):
        attr = (role_attrs or {}).get(row.role or "", curses.A_NORMAL)
        # Writing the very last cell fails after drawing it, e.g. a wide character.
        with suppress(curses.error):
            pad.addstr(y, 0, row.text, attr)
        span = selection.span(first + y, len(row.text)) if selection else None
        if span is not None:
            start, end = span
            x = sum(cell_width(char) for char in row.text[:start])
            with suppress(curses.error):
                pad.addstr(y, x, row.text[start:end], attr | curses.A_REVERSE)
    bottom, right = area.top + area.height - 1, area.left + area.width - 1
    pad.noutrefresh(0, 0, area.top, area.left, bottom, right)
