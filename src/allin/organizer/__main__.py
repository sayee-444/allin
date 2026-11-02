"""The Organizer process (#53), started by Main inside its own xterm.

Usage: ``python -m allin.organizer <to-organizer-fifo> <from-organizer-fifo>``. It shows the rows
Main sends and tells Main what the human asks for; it touches no Session itself. Esc, its X, or
Main going away closes it.
"""

import curses
import os
import queue
import sys
from contextlib import suppress
from pathlib import Path

from allin.core.errors import AllInError, ChannelBroken
from allin.core.themes import Theme
from allin.ipc.channel import Channel
from allin.ipc.messages import (
    Close,
    MainToOrganizer,
    OrganizerAct,
    OrganizerReady,
    OrganizerRows,
    ThemeSet,
    decode_message_to_organizer,
)
from allin.ui.look import frame_chars, terminal_changes
from allin.ui.mouse import MOUSE_MASK, MouseAction, mouse_event
from allin.ui.organizer_view import QUIT, Act, Line, OrganizerView
from allin.ui.screen import theme_attrs

_POLL_MS = 50
_MOTION_ON = "\x1b[?1003h"  # every mouse motion, to mark the row under it
_MOTION_OFF = "\x1b[?1003l"
_TITLE = " AllIn · Organizer "


class _MainListener:
    """What Main sends goes to the screen's inbox; ``None`` once Main is gone."""

    def __init__(self, inbox: queue.Queue[MainToOrganizer | None]) -> None:
        self._inbox = inbox

    def on_message(self, message: MainToOrganizer) -> None:
        self._inbox.put(message)

    def on_error(self, error: AllInError) -> None:
        if isinstance(error, ChannelBroken):  # Main is gone: a temporary screen goes too
            self._inbox.put(None)

    def on_eof(self) -> None:
        self._inbox.put(None)


class OrganizerScreen:
    def __init__(self, stdscr: curses.window, view: OrganizerView) -> None:
        self._stdscr = stdscr
        self._view = view
        self._theme: Theme | None = None
        self._frame = frame_chars("single", "unicode")
        self._row_attr = self._state_attr = self._border_attr = curses.A_NORMAL

    def run(self, inbox: queue.Queue[MainToOrganizer | None], act: "ActSender") -> None:
        self._stdscr.keypad(True)
        self._stdscr.timeout(_POLL_MS)
        with suppress(curses.error):
            curses.curs_set(0)
        curses.mousemask(MOUSE_MASK)
        curses.mouseinterval(0)
        self._use(Theme())
        _write_terminal(_MOTION_ON)
        try:
            self._loop(inbox, act)
        finally:
            _write_terminal(_MOTION_OFF)

    def _loop(self, inbox: queue.Queue[MainToOrganizer | None], act: "ActSender") -> None:
        dirty = True
        while True:
            while True:
                try:
                    message = inbox.get_nowait()
                except queue.Empty:
                    break
                match message:
                    case None | Close():
                        return
                    case OrganizerRows(rows=rows):
                        self._view.set_rows(rows)
                    case ThemeSet(theme=theme):
                        self._apply(theme)
                dirty = True
            if dirty:
                self._draw()
                dirty = False
            try:
                key = self._stdscr.get_wch()
            except curses.error:
                continue
            dirty = True
            if key == curses.KEY_MOUSE:
                self._mouse(act)
                continue
            name = curses.keyname(key).decode() if isinstance(key, int) else key
            done = self._view.key(name)
            if done == QUIT:
                return
            if done is not None:
                act(done)

    def _mouse(self, act: "ActSender") -> None:
        try:
            _, x, y, _, bstate = curses.getmouse()
        except curses.error:
            return
        event = mouse_event(x, y, bstate)
        if event is None:
            return
        line_y, line_x = event.y - 1, event.x - 2  # inside the frame
        if event.action == MouseAction.DRAG:
            self._view.move(line_y)
        elif event.action == MouseAction.PRESS:
            done = self._view.click(line_y, line_x, self._list_width())
            if done is not None:
                act(done)

    def _list_width(self) -> int:
        return max(0, self._stdscr.getmaxyx()[1] - 4)

    def _apply(self, theme: Theme) -> None:
        sequence = terminal_changes(self._theme, theme, "window")
        self._use(theme)
        if sequence:
            _write_terminal(sequence)

    def _use(self, theme: Theme) -> None:
        self._theme = theme
        roles, border, status, _ = theme_attrs(theme, "window")
        self._row_attr = roles.get("answer", curses.A_NORMAL)
        self._state_attr = roles.get("info", curses.A_NORMAL)
        self._border_attr = border | status
        self._frame = frame_chars(theme.border.style, theme.glyphs.set)

    def _draw(self) -> None:
        stdscr = self._stdscr
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 3 or width < 8:
            stdscr.refresh()
            return
        self._draw_frame(height, width)
        for y, line in enumerate(self._view.lines(self._list_width())[: height - 2], start=1):
            with suppress(curses.error):
                stdscr.addstr(y, 2, line.text, self._attr(line))
        stdscr.refresh()

    def _attr(self, line: Line) -> int:
        attr = {
            "header": self._border_attr | curses.A_BOLD,
            "fold": self._border_attr,
            "help": self._state_attr | curses.A_DIM,
        }.get(line.kind, self._row_attr if line.place != "closed" else self._state_attr)
        if line.selected:
            attr |= curses.A_REVERSE
        if line.hovered and line.item is not None:
            attr |= curses.A_BOLD | curses.A_UNDERLINE
        return attr

    def _draw_frame(self, height: int, width: int) -> None:
        left, right, top, bottom, top_left, top_right, bottom_left, bottom_right = self._frame
        attr, stdscr = self._border_attr, self._stdscr
        with suppress(curses.error):
            stdscr.addstr(0, 0, top_left + top * (width - 2) + top_right, attr)
            for y in range(1, height - 1):
                stdscr.addstr(y, 0, left, attr)
                stdscr.addstr(y, width - 1, right, attr)
            stdscr.addstr(height - 1, 0, bottom_left + bottom * (width - 2), attr)
        with suppress(curses.error):  # the very last cell fails after drawing it
            stdscr.addstr(height - 1, width - 1, bottom_right, attr)
        with suppress(curses.error):
            stdscr.addnstr(0, 2, _TITLE, max(0, width - 4), attr | curses.A_BOLD)


class ActSender:
    """Sends Main what the human asked for."""

    def __init__(self, channel: Channel[MainToOrganizer]) -> None:
        self._channel = channel

    def __call__(self, act: Act) -> None:
        self._channel.send(OrganizerAct(action=act.action, session_id=act.session_id))


def _write_terminal(sequence: str) -> None:
    sys.stdout.write(sequence)
    sys.stdout.flush()


def _run(stdscr: curses.window, to_organizer: Path, from_organizer: Path) -> None:
    inbox: queue.Queue[MainToOrganizer | None] = queue.Queue()
    channel = Channel(
        to_organizer, from_organizer, decode_message_to_organizer, _MainListener(inbox)
    )
    channel.start()
    channel.send(OrganizerReady())
    try:
        OrganizerScreen(stdscr, OrganizerView()).run(inbox, ActSender(channel))
    except KeyboardInterrupt:
        pass
    finally:
        channel.close()


def main() -> None:
    os.environ.setdefault("ESCDELAY", "25")  # Esc closes it at once, not after a second
    to_organizer, from_organizer = sys.argv[1:3]
    curses.wrapper(_run, Path(to_organizer), Path(from_organizer))


if __name__ == "__main__":
    main()
