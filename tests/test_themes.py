"""Themes as data (#47, ADR-0018): their shape, the built-in look, a Session's loose keys and
the colour each Session's border takes."""

import pytest
from pydantic import ValidationError

from allin.core.errors import ThemeInvalid
from allin.core.themes import (
    BUILTIN,
    PALETTE,
    LooseKeys,
    Theme,
    is_theme_name,
    nearest_256,
    parse_theme,
    pick_border_color,
    rgb,
    with_keys,
)

OCEANO = """
[font]
family = "DejaVu Sans Mono"
size = 12

[colors]
background = "#002b36"
text = "#eee8d5"
user = "bright_cyan"

[border]
style = "rounded"
color = "#268bd2"

[window]
size = "120x35"

[glyphs]
set = "nerd"
icons = { user = "\\uf007", error = "!" }
"""


def test_the_built_in_look_is_the_one_from_before_themes_but_its_border():
    assert (BUILTIN.font.family, BUILTIN.font.size, BUILTIN.window.size) == (
        "Monospace",
        10,
        "180x40",
    )
    colors = BUILTIN.colors
    assert (colors.user, colors.reasoning, colors.answer, colors.info, colors.error) == (
        "cyan",
        "magenta",
        "green",
        "yellow",
        "red",
    )
    assert colors.background is None and colors.text is None
    assert BUILTIN.border.style == "single" and BUILTIN.border.color == "auto"


def test_a_theme_file_is_read_and_what_it_leaves_out_keeps_the_built_in_value():
    theme = parse_theme("oceano", OCEANO)
    assert theme.font.family == "DejaVu Sans Mono" and theme.font.size == 12
    assert theme.colors.background == "#002b36" and theme.colors.answer == "green"
    assert theme.border.style == "rounded" and theme.columns_rows == (120, 35)
    assert theme.glyphs.icons == {"user": "", "error": "!"}


@pytest.mark.parametrize(
    "text",
    [
        '[colors]\nuser = "rojo"',
        '[colors]\nuser = "#12345"',
        '[border]\ncolor = "#zzzzzz"',
        '[colors]\nuser = "auto"',  # only the border takes auto
        '[font]\nfamily = "Mono:weight=200"',
        '[font]\nfamily = "Mono,Serif"',
        '[font]\nfamily = "Mono\\u0007"',
        "[font]\nsize = 200",
        '[border]\nstyle = "wavy"',
        '[window]\nsize = "9x9"',
        '[window]\nsize = "120x500"',
        '[glyphs]\nset = "emoji"',
        '[glyphs]\nicons = { user = "ab" }',
        '[glyphs]\nicons = { user = "\\u202e" }',  # a direction mark
        '[glyphs]\nicons = { user = " " }',
        '[glyphs]\nicons = { prompt = "x" }',
        "[sound]\nbell = true",
        "not toml at all [",
    ],
)
def test_a_value_without_its_shape_makes_the_theme_invalid(text: str):
    with pytest.raises(ThemeInvalid):
        parse_theme("malo", text)


def test_the_invalid_theme_says_where():
    with pytest.raises(ThemeInvalid) as raised:
        parse_theme("malo", '[colors]\nuser = "rojo"')
    assert raised.value.fields["theme"] == "malo"
    assert raised.value.fields["reason"] == "colors.user: 'rojo' is not a valid value"


@pytest.mark.parametrize("name", ["default", "oceano", "alto-contraste", "T2"])
def test_a_theme_name_is_letters_digits_and_hyphens(name: str):
    assert is_theme_name(name)


@pytest.mark.parametrize("name", ["", "../x", "a/b", "a.toml", "a b", "x" * 33])
def test_anything_else_is_no_theme_name(name: str):
    assert not is_theme_name(name)


def test_a_sessions_loose_keys_replace_only_what_they_name():
    theme = parse_theme("oceano", OCEANO)
    keys = LooseKeys.model_validate({"font": {"size": 13}, "colors": {"user": "red"}})
    mine = with_keys(theme, keys)
    assert mine.font.size == 13 and mine.font.family == "DejaVu Sans Mono"
    assert mine.colors.user == "red" and mine.colors.background == "#002b36"
    assert with_keys(theme, LooseKeys()) == theme


def test_a_loose_key_is_checked_as_the_theme_is():
    with pytest.raises(ValidationError, match="family"):
        LooseKeys.model_validate({"font": {"family": "Mono:weight=200"}})


def test_each_new_session_takes_the_colour_the_others_use_least():
    assert pick_border_color([]) == PALETTE[0]
    assert pick_border_color([PALETTE[0]]) == PALETTE[1]
    four = [pick_border_color(PALETTE[:n]) for n in range(4)]
    assert len(set(four)) == 4  # four Windows, four colours
    every = list(PALETTE)
    assert pick_border_color(every) == PALETTE[0]  # all taken once: the first again
    assert pick_border_color([*every, PALETTE[0]]) == PALETTE[1]


def test_colours_as_rgb_and_as_the_nearest_of_256():
    assert rgb("cyan") == "#00cdcd" and rgb("#ABCDEF") == "#abcdef"
    assert nearest_256("red") == 1 and nearest_256("bright_white") == 15
    assert nearest_256("#000000") == 16 and nearest_256("#ffffff") == 231
    assert nearest_256("#808080") == 244
    assert 16 <= nearest_256("#268bd2") <= 255


def test_a_theme_round_trips_its_defaults():
    assert Theme.model_validate(BUILTIN.model_dump()) == BUILTIN
