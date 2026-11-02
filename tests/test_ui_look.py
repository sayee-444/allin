"""What a Window or Main's Chat sends its terminal to apply a Theme (#47): orders only, and
only what changed."""

from allin.core.themes import BUILTIN, Theme, parse_theme
from allin.ui.look import frame_chars, restore_sequence, terminal_changes, xterm_options

OCEANO = parse_theme(
    "oceano",
    '[font]\nfamily = "DejaVu Sans Mono"\nsize = 12\n[colors]\nbackground = "#002b36"\n'
    'text = "white"\n[window]\nsize = "120x35"',
)


def test_a_windows_first_theme_needs_no_order_since_its_xterm_was_born_with_it():
    assert terminal_changes(None, OCEANO, "window") == ""
    assert xterm_options(OCEANO) == [
        "-fa",
        "DejaVu Sans Mono,monospace",
        "-fs",
        "12",
        "-geometry",
        "120x35",
        "-bg",
        "#002b36",
        "-fg",
        "#e5e5e5",
    ]


def test_a_new_theme_changes_letter_size_and_colours_with_xterm_orders():
    sequence = terminal_changes(BUILTIN, OCEANO, "window")
    assert "\x1b]50;xft:DejaVu Sans Mono,monospace:size=12\x07" in sequence
    assert "\x1b[8;35;120t" in sequence
    assert "\x1b]11;#002b36\x07" in sequence and "\x1b]10;#e5e5e5\x07" in sequence


def test_new_colours_never_undo_a_size_the_human_gave_the_window():
    bluer = OCEANO.model_copy(
        update={"colors": OCEANO.colors.model_copy(update={"background": "#00008b"})}
    )
    sequence = terminal_changes(OCEANO, bluer, "window")
    assert sequence == "\x1b]11;#00008b\x07"


def test_a_colour_taken_away_gives_the_terminal_its_own_back():
    assert terminal_changes(OCEANO, BUILTIN, "window").count("\x1b]111\x07") == 1


def test_no_order_ever_asks_xterm_anything():
    for sequence in (
        terminal_changes(BUILTIN, OCEANO, "window"),
        terminal_changes(None, OCEANO, "main"),
    ):
        assert "?" not in sequence  # OSC 10/11/50 with ? would ask


def test_mains_chat_keeps_its_size_and_touches_its_letter_only_in_an_xterm():
    in_xterm = terminal_changes(None, OCEANO, "main", xterm=True)
    elsewhere = terminal_changes(None, OCEANO, "main", xterm=False)
    assert "\x1b[8;" not in in_xterm
    assert "\x1b]50;" in in_xterm and "\x1b]50;" not in elsewhere
    assert "\x1b]11;#002b36\x07" in elsewhere
    assert terminal_changes(None, BUILTIN, "main") == ""  # the default touches nothing


def test_on_its_way_out_mains_chat_gives_back_the_terminals_colours():
    assert restore_sequence(OCEANO) == "\x1b]111\x07\x1b]110\x07"
    assert restore_sequence(BUILTIN) == "" and restore_sequence(None) == ""


def test_borders_by_style_and_plain_with_the_ascii_glyph_set():
    assert frame_chars("rounded", "unicode") == "││──╭╮╰╯"
    assert frame_chars("heavy", "nerd")[4] == "┏"
    assert frame_chars("double", "ascii") == "||--++++"
    assert Theme().border.style == "single"
