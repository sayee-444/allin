"""Text selected with the mouse in a pad (the Chat or the input), kept free of curses.

Turning the mouse on for scrolling turns off xterm's own selection, so AllIn has its own: a
drag from one character to another selects both and all between them, over the pad's rows.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from allin.ui.chat import Row
from allin.ui.wrap import cell_width

_AUTOSCROLL_MAX = 10  # rows per tick, however far out the pointer goes


@dataclass(frozen=True, order=True)
class Point:
    """A character: its row among the pad's rows, and its index in that row's text."""

    row: int
    col: int


class Selection:
    def __init__(self) -> None:
        self._anchor: Point | None = None
        self._head: Point | None = None

    @property
    def active(self) -> bool:
        """Whether something is selected: a press alone selects nothing until it drags."""
        return self._head is not None

    def start(self, point: Point) -> None:
        self._anchor, self._head = point, None

    def extend(self, point: Point) -> None:
        if self._anchor is not None:
            self._head = point

    def clear(self) -> None:
        self._anchor = self._head = None

    def span(self, row: int, length: int) -> tuple[int, int] | None:
        """The selected part of row ``row`` (whose text has ``length`` characters), as
        ``(start, end)`` indexes; ``None`` if none of it is."""
        ends = self._ends()
        if ends is None or not ends[0].row <= row <= ends[1].row:
            return None
        first, last = ends
        start = first.col if row == first.row else 0
        end = last.col + 1 if row == last.row else length
        start, end = min(start, length), min(end, length)
        return (start, end) if start < end else None

    def text(self, rows: Sequence[Row]) -> str:
        """The selected text: a row that continues the one before it (``joined``) is glued
        to it, any other starts a new line."""
        ends = self._ends()
        if ends is None:
            return ""
        parts: list[str] = []
        for index in range(ends[0].row, min(ends[1].row, len(rows) - 1) + 1):
            row = rows[index]
            if index > ends[0].row and not row.joined:
                parts.append("\n")
            span = self.span(index, len(row.text))
            if span is not None:
                parts.append(row.text[span[0] : span[1]])
        return "".join(parts)

    def _ends(self) -> tuple[Point, Point] | None:
        if self._anchor is None or self._head is None:
            return None
        return min(self._anchor, self._head), max(self._anchor, self._head)


def char_index(text: str, cell: int) -> int:
    """Index of the character drawn on ``cell`` of a row showing ``text``; past its end, the
    length of ``text``."""
    used = 0
    for index, char in enumerate(text):
        used += cell_width(char)
        if used > cell:
            return index
    return len(text)


def autoscroll_rows(y: int, top: int, height: int) -> int:
    """Rows to scroll each tick while a drag holds the pointer at row ``y``, outside a pad
    whose rows go from ``top`` for ``height``: up (negative) or down, faster the further out."""
    if y < top:
        return -min(top - y, _AUTOSCROLL_MAX)
    bottom = top + height - 1
    if y > bottom:
        return min(y - bottom, _AUTOSCROLL_MAX)
    return 0
