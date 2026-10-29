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
    lines: list[str] = []
    for logical in text.split("\n"):
        line, used = "", 0
        for char in printable(logical):
            cells = cell_width(char)
            if used + cells > width and line:
                lines.append(line)
                line, used = "", 0
            line += char
            used += cells
        lines.append(line)
    return lines


def printable(line: str) -> str:
    """``line`` (with no newlines) as the pad draws it."""
    line = line.replace("\t", _TAB)
    return "".join(char for char in line if unicodedata.category(char) != "Cc")
