"""Minimal Chat + one-line input screen shared by Main and the Window.

Placeholder until the responsive layout (bordered newwin + pads) lands; only the UI thread
touches curses, other threads hand it lines through ``inbox``.
"""

import curses
import queue
from collections.abc import Callable

_POLL_MS = 50
_ENTER = ("\n", "\r", curses.KEY_ENTER)
_BACKSPACE = ("\x7f", "\b", curses.KEY_BACKSPACE)


class LineScreen:
    def __init__(self, stdscr: curses.window, title: str) -> None:
        self._stdscr = stdscr
        self._title = title
        self._lines: list[str] = []
        self._buffer = ""
        # Lines to append from other threads; None asks the screen to quit.
        self.inbox: queue.Queue[str | None] = queue.Queue()

    def append(self, line: str) -> None:
        self._lines.extend(line.splitlines() or [""])

    def run(self, on_submit: Callable[[str], None]) -> None:
        """Run the UI loop until ``None`` arrives in the inbox."""
        self._stdscr.timeout(_POLL_MS)
        while True:
            if not self._drain_inbox():
                return
            self._draw()
            try:
                key = self._stdscr.get_wch()
            except curses.error:
                continue
            if key in _ENTER:
                text, self._buffer = self._buffer, ""
                if text.strip():
                    on_submit(text)
            elif key in _BACKSPACE:
                self._buffer = self._buffer[:-1]
            elif isinstance(key, str) and key.isprintable():
                self._buffer += key

    def _drain_inbox(self) -> bool:
        while True:
            try:
                line = self.inbox.get_nowait()
            except queue.Empty:
                return True
            if line is None:
                return False
            self.append(line)

    def _draw(self) -> None:
        height, width = self._stdscr.getmaxyx()
        self._stdscr.erase()
        if height < 3 or width < 4:
            self._stdscr.refresh()
            return
        self._stdscr.addnstr(0, 0, self._title, width - 1, curses.A_REVERSE)
        visible = self._lines[-(height - 2) :]
        for row, line in enumerate(visible, start=1):
            self._stdscr.addnstr(row, 0, line, width - 1)
        prompt = "> " + self._buffer
        self._stdscr.addnstr(height - 1, 0, prompt[-(width - 1) :], width - 1)
        self._stdscr.refresh()
