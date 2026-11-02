"""How a Window or Main's Chat applies a Theme that Main resolved (#47, ADR-0018).

Curses redraws colours and borders; the letter, the size and the background change with xterm
control sequences, which are orders only: xterm is never asked anything, since each answer
would arrive as if typed. Only what changed is sent, so new colours never undo a size the
human gave the Window by hand.
"""

from typing import Literal

from allin.core.themes import BorderStyle, GlyphSet, Theme, font_pattern, rgb

type Terminal = Literal["window", "main"]

_OSC = "\x1b]"
_BEL = "\x07"

# Left, right, top, bottom, then the corners: top left, top right, bottom left, bottom right.
_FRAMES: dict[BorderStyle, str] = {
    "single": "││──┌┐└┘",
    "double": "║║══╔╗╚╝",
    "rounded": "││──╭╮╰╯",
    "heavy": "┃┃━━┏┓┗┛",
    "ascii": "||--++++",
}


def frame_chars(style: BorderStyle, glyphs: GlyphSet) -> str:
    """The eight characters of a border; with the ``ascii`` glyph set, always plain ones."""
    return _FRAMES["ascii" if glyphs == "ascii" else style]


def terminal_changes(
    before: Theme | None, theme: Theme, terminal: Terminal, xterm: bool = True
) -> str:
    """The xterm orders that take the terminal from ``before`` to ``theme``.

    A Window's xterm is born with its first Theme (``-fa``, ``-fs``, ``-geometry``, ``-bg``,
    ``-fg``), so its first one needs none. Main's Chat runs in the human's terminal: its size
    is never touched, and its letter only in an xterm.
    """
    if terminal == "window" and before is None:
        return ""
    sequence = ""
    # The letter the terminal has: the Window's last Theme's, or the human's own in Main's.
    old_font = before.font if before is not None else Theme().font
    if theme.font != old_font and (terminal == "window" or xterm):
        pattern = font_pattern(theme.font.family)
        sequence += f"{_OSC}50;xft:{pattern}:size={theme.font.size}{_BEL}"
    if terminal == "window" and before is not None and theme.window != before.window:
        columns, rows = theme.columns_rows
        sequence += f"\x1b[8;{rows};{columns}t"
    old_colors = before.colors if before is not None else None
    for code, color, old in (
        (11, theme.colors.background, old_colors.background if old_colors else None),
        (10, theme.colors.text, old_colors.text if old_colors else None),
    ):
        if color == old:
            continue
        # 110 and 111 give the terminal back its own text and background colours.
        sequence += f"{_OSC}{code};{rgb(color)}{_BEL}" if color else f"{_OSC}1{code}{_BEL}"
    return sequence


def restore_sequence(applied: Theme | None) -> str:
    """What Main's Chat gives back to the human's terminal on its way out: its own text and
    background colours, if a Theme changed them."""
    if applied is None:
        return ""
    sequence = ""
    if applied.colors.background is not None:
        sequence += f"{_OSC}111{_BEL}"
    if applied.colors.text is not None:
        sequence += f"{_OSC}110{_BEL}"
    return sequence


def xterm_options(theme: Theme) -> list[str]:
    """What a Window's xterm is born with, so its first Theme needs no order."""
    columns, rows = theme.columns_rows
    options = ["-fa", font_pattern(theme.font.family), "-fs", str(theme.font.size)]
    options += ["-geometry", f"{columns}x{rows}"]
    if theme.colors.background is not None:
        options += ["-bg", rgb(theme.colors.background)]
    if theme.colors.text is not None:
        options += ["-fg", rgb(theme.colors.text)]
    return options
