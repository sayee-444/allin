"""Themes: how a Window or Main's Chat looks (ADR-0018).

A Theme is ``<profile>/themes/<name>.toml``, always looked up by name inside that folder. Every
value has a fixed shape, checked here whoever wrote it (a file, a Kit or a loose key of a
Session's Settings), so nothing a Theme says can reach xterm or fontconfig as an order of its
own. Main reads and checks Themes; a Window only applies what Main resolved.
"""

import re
import tomllib
import unicodedata
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from allin.core.errors import ThemeInvalid

# A Theme's name, also its file's: letters, digits and hyphens, like a Profile's.
THEME_NAME_PATTERN = r"^[A-Za-z0-9-]{1,32}$"
_THEME_NAME = re.compile(THEME_NAME_PATTERN)
DEFAULT_THEME = "default"
AUTO = "auto"  # the border colour each Session gets for itself

# The terminal's 16 named colours, with the RGB xterm gives them, for what takes RGB (OSC 10/11,
# ``-bg``/``-fg``) and for finding the nearest of 256.
NAMED_COLORS: dict[str, str] = {
    "black": "#000000",
    "red": "#cd0000",
    "green": "#00cd00",
    "yellow": "#cdcd00",
    "blue": "#0000ee",
    "magenta": "#cd00cd",
    "cyan": "#00cdcd",
    "white": "#e5e5e5",
    "bright_black": "#7f7f7f",
    "bright_red": "#ff0000",
    "bright_green": "#00ff00",
    "bright_yellow": "#ffff00",
    "bright_blue": "#5c5cff",
    "bright_magenta": "#ff00ff",
    "bright_cyan": "#00ffff",
    "bright_white": "#ffffff",
}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_COLOR_PATTERN = r"^(#[0-9a-fA-F]{6}|" + "|".join(NAMED_COLORS) + r")$"
_BORDER_COLOR_PATTERN = r"^(#[0-9a-fA-F]{6}|" + "|".join(NAMED_COLORS) + f"|{AUTO})$"

# The border colours ``auto`` hands out, in order: each new Session takes the one the others
# use least. Twelve, so four Windows seldom share one (ADR-0018).
PALETTE = (
    "#e06c75",
    "#98c379",
    "#e5c07b",
    "#61afef",
    "#c678dd",
    "#56b6c2",
    "#d19a66",
    "#ff79c6",
    "#8be9fd",
    "#50fa7b",
    "#bd93f9",
    "#f1fa8c",
)

type LineType = Literal["user", "reasoning", "answer", "info", "error"]
type BorderStyle = Literal["single", "double", "rounded", "heavy", "ascii"]
type GlyphSet = Literal["nerd", "unicode", "ascii"]

Color = Annotated[str, Field(pattern=_COLOR_PATTERN)]
BorderColor = Annotated[str, Field(pattern=_BORDER_COLOR_PATTERN)]
# Only what a family name needs: no ``:`` (fontconfig properties), no ``,`` (more families),
# nothing that is no character. Main adds ``,monospace`` itself.
FontFamily = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.+-]{0,63}$")]
FontSize = Annotated[int, Field(ge=6, le=48)]
WindowSize = Annotated[str, Field(pattern=r"^[0-9]{2,3}x[0-9]{1,3}$")]
ThemeName = Annotated[str, Field(pattern=THEME_NAME_PATTERN)]

_WINDOW_COLUMNS = (40, 400)
_WINDOW_ROWS = (10, 200)


def _one_visible_char(icon: str) -> str:
    """An icon is a single character that shows: no control, direction mark, space or
    combining mark (a Nerd Font glyph, in the Private Use Area, is one)."""
    if len(icon) != 1 or (unicodedata.category(icon)[0] in "CZM" and not _private(icon)):
        msg = "an icon is one visible character"
        raise ValueError(msg)
    return icon


def _private(char: str) -> bool:
    return unicodedata.category(char) == "Co"


def _window_size(size: str) -> str:
    columns, rows = (int(part) for part in size.split("x"))
    if not _WINDOW_COLUMNS[0] <= columns <= _WINDOW_COLUMNS[1]:
        msg = f"columns go from {_WINDOW_COLUMNS[0]} to {_WINDOW_COLUMNS[1]}"
        raise ValueError(msg)
    if not _WINDOW_ROWS[0] <= rows <= _WINDOW_ROWS[1]:
        msg = f"rows go from {_WINDOW_ROWS[0]} to {_WINDOW_ROWS[1]}"
        raise ValueError(msg)
    return size


Icon = Annotated[str, AfterValidator(_one_visible_char)]
CheckedWindowSize = Annotated[WindowSize, AfterValidator(_window_size)]


class ThemeFont(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    family: FontFamily = "Monospace"
    size: FontSize = 10


class ThemeColors(BaseModel, frozen=True):
    """Each line type's colour; ``None`` for ``background``, ``text`` and ``status`` leaves
    the terminal's own."""

    model_config = ConfigDict(extra="forbid")

    background: Color | None = None
    text: Color | None = None
    user: Color = "cyan"
    reasoning: Color = "magenta"  # shown dimmed
    answer: Color = "green"
    info: Color = "yellow"
    error: Color = "red"
    status: Color | None = None


class ThemeBorder(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    style: BorderStyle = "single"
    color: BorderColor = AUTO


class ThemeWindow(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    size: CheckedWindowSize = "180x40"  # columns x rows


class ThemeGlyphs(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    set: GlyphSet = "unicode"
    icons: dict[LineType, Icon] = Field(default_factory=dict[LineType, Icon])


class Theme(BaseModel, frozen=True):
    """A Theme as its file says it; what is left out keeps the built-in look's value."""

    model_config = ConfigDict(extra="forbid")

    font: ThemeFont = ThemeFont()
    colors: ThemeColors = ThemeColors()
    border: ThemeBorder = ThemeBorder()
    window: ThemeWindow = ThemeWindow()
    glyphs: ThemeGlyphs = ThemeGlyphs()

    @property
    def columns_rows(self) -> tuple[int, int]:
        columns, rows = self.window.size.split("x")
        return int(columns), int(rows)


# The look AllIn had before Themes, but for the border's colour: what ``default`` is when its
# file is missing or broken.
BUILTIN = Theme()


# A Session's loose keys (``/session set font.size=13``): each one replaces the Theme's own,
# in that Session only. Checked as the Theme's are.


class FontKeys(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    family: FontFamily | None = Field(default=None, description="its Theme's letter, replaced")
    size: FontSize | None = Field(default=None, description="its Theme's letter size, replaced")


class ColorKeys(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    background: Color | None = Field(default=None, description="its Theme's background")
    text: Color | None = Field(default=None, description="its Theme's text colour")
    user: Color | None = Field(default=None, description="its Theme's colour for Prompts")
    reasoning: Color | None = Field(default=None, description="its Theme's reasoning colour")
    answer: Color | None = Field(default=None, description="its Theme's answer colour")
    info: Color | None = Field(default=None, description="its Theme's info colour")
    error: Color | None = Field(default=None, description="its Theme's error colour")
    status: Color | None = Field(default=None, description="its Theme's border status colour")


class BorderKeys(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    style: BorderStyle | None = Field(default=None, description="its Theme's border style")
    color: BorderColor | None = Field(default=None, description="its Theme's border colour")


class WindowKeys(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    size: CheckedWindowSize | None = Field(
        default=None, description="its Theme's Window size, columns x rows"
    )


class GlyphKeys(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    set: GlyphSet | None = Field(default=None, description="its Theme's glyph set")


class LooseKeys(BaseModel, frozen=True):
    """What a Session's Settings replace of its Theme."""

    font: FontKeys = FontKeys()
    colors: ColorKeys = ColorKeys()
    border: BorderKeys = BorderKeys()
    window: WindowKeys = WindowKeys()
    glyphs: GlyphKeys = GlyphKeys()


def parse_theme(name: str, text: str) -> Theme:
    """The Theme a file says; ``ThemeInvalid`` says what is wrong with it."""
    try:
        data = tomllib.loads(text)
        return Theme.model_validate(data)
    except tomllib.TOMLDecodeError as error:
        raise ThemeInvalid(theme=name, reason=str(error)) from error
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        # A pattern's own text tells the human nothing: the value refused does.
        if first["type"] == "string_pattern_mismatch":
            reason = f"{where}: {first['input']!r} is not a valid value"
        else:
            reason = f"{where}: {first['msg']}"
        raise ThemeInvalid(theme=name, reason=reason) from error


def font_pattern(family: str) -> str:
    """The fontconfig pattern for a Theme's family: its hyphens escaped, so none reads as a
    size, then ``monospace``, so a letter that is missing is replaced by one of fixed width."""
    return family.replace("-", "\\-") + ",monospace"


def is_theme_name(text: str) -> bool:
    return _THEME_NAME.fullmatch(text) is not None


def with_keys(theme: Theme, keys: LooseKeys) -> Theme:
    """``theme`` with a Session's loose keys in place of its own values."""
    data = theme.model_dump()
    for group, values in keys.model_dump(exclude_none=True).items():
        data[group] = {**data[group], **values}
    return Theme.model_validate(data)


def pick_border_color(used: Sequence[str]) -> str:
    """The palette's colour the other Sessions use least; the first of them on a tie."""
    return min(PALETTE, key=lambda color: (used.count(color), PALETTE.index(color)))


def rgb(color: str) -> str:
    """A colour as ``#rrggbb``: a name's as xterm shows it."""
    return NAMED_COLORS.get(color, color).lower()


def nearest_256(color: str) -> int:
    """The index of the terminal's 256 colours nearest to ``color``: a name is its own of the
    first 16; ``#rrggbb``, the nearest of the cube and the greys (16 to 255), which no terminal
    redefines."""
    if color in NAMED_COLORS:
        return list(NAMED_COLORS).index(color)
    if not _HEX.fullmatch(color):
        msg = f"not a colour: {color}"
        raise ValueError(msg)
    target = _channels(color)
    return min(range(16, 256), key=lambda index: _distance(_xterm_rgb(index), target))


def _channels(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True))


_CUBE = (0, 95, 135, 175, 215, 255)


def _xterm_rgb(index: int) -> tuple[int, int, int]:
    """The RGB of one of xterm's 256 colours from 16 on."""
    if index >= 232:  # the greys
        level = 8 + 10 * (index - 232)
        return level, level, level
    index -= 16
    return _CUBE[index // 36], _CUBE[index // 6 % 6], _CUBE[index % 6]
