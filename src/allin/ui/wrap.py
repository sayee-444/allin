"""Split text into the visual lines a pad of a given width shows.

Wrapping is by character, never dropping whitespace, so re-wrapping after a resize keeps every
printable character. Tabs become spaces and other control characters are dropped, since curses
would draw them with a width the layout cannot predict.
"""

import unicodedata

_TAB = "    "


def cell_width(char: str) -> int:
    """Terminal cells taken by ``char``: 0 for combining marks, 2 for wide characters, else 1."""
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def wrap(text: str, width: int) -> list[str]:
    return [row for row, _ in wrap_rows(text, width)]


def wrap_rows(text: str, width: int) -> list[tuple[str, bool]]:
    """``wrap``, saying of each row whether it continues the one before it (no line break)."""
    rows: list[tuple[str, bool]] = []
    for logical in text.split("\n"):
        line, used, joined = "", 0, False
        for char in printable(logical):
            cells = cell_width(char)
            if used + cells > width and line:
                rows.append((line, joined))
                line, used, joined = "", 0, True
            line += char
            used += cells
        rows.append((line, joined))
    return rows


def printable(line: str) -> str:
    """``line`` (with no newlines) as the pad draws it."""
    line = line.replace("\t", _TAB)
    return "".join(char for char in line if unicodedata.category(char) != "Cc")
