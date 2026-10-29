import curses

import pytest

from allin.ui.keys import Key, KeyChord, KeyDecoder, KeyEvent, Paste


def feed_all(*keys: str | int) -> list[KeyEvent]:
    decoder = KeyDecoder()
    return [chord for key in keys for chord in decoder.feed(key)]


def feed_text(text: str) -> list[KeyEvent]:
    return feed_all(*text)


def test_a_printable_character_is_a_chord_of_itself():
    assert KeyDecoder().feed("a") == [KeyChord("a")]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("\n", KeyChord(Key.ENTER)),
        ("\r", KeyChord(Key.ENTER)),
        (curses.KEY_ENTER, KeyChord(Key.ENTER)),
        ("\x7f", KeyChord(Key.BACKSPACE)),
        (curses.KEY_BACKSPACE, KeyChord(Key.BACKSPACE)),
        ("\x08", KeyChord(Key.BACKSPACE, ctrl=True)),
        (curses.KEY_PPAGE, KeyChord(Key.PAGE_UP)),
        (curses.KEY_NPAGE, KeyChord(Key.PAGE_DOWN)),
        (curses.KEY_RESIZE, KeyChord(Key.RESIZE)),
    ],
)
def test_control_bytes_and_curses_codes_become_keys(key: str | int, expected: KeyChord):
    assert KeyDecoder().feed(key) == [expected]


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ("\x1b[27;5;13~", KeyChord(Key.ENTER, ctrl=True)),
        ("\x1b[27;2;13~", KeyChord(Key.ENTER, shift=True)),
        ("\x1b[27;5;127~", KeyChord(Key.BACKSPACE, ctrl=True)),
        ("\x1b[27;5;8~", KeyChord(Key.BACKSPACE, ctrl=True)),
        ("\x1b[27;5;97~", KeyChord("a", ctrl=True)),
        ("\x1b[27;3;120~", KeyChord("x", alt=True)),
    ],
)
def test_modify_other_keys_sequences_carry_their_modifiers(sequence: str, expected: KeyChord):
    assert feed_text(sequence) == [expected]


def test_text_around_a_sequence_is_kept_in_order():
    assert feed_text("a\x1b[27;5;13~b") == [
        KeyChord("a"),
        KeyChord(Key.ENTER, ctrl=True),
        KeyChord("b"),
    ]


def test_unknown_sequences_and_curses_codes_are_dropped():
    assert feed_all("\x1b", "[", "1", "5", "~", curses.KEY_F1, "a") == [KeyChord("a")]


def test_a_bracketed_paste_arrives_whole_and_its_newlines_are_not_enter():
    assert feed_text("x\x1b[200~uno\rdos\ntres\x1b[201~y") == [
        KeyChord("x"),
        Paste("uno\rdos\ntres"),
        KeyChord("y"),
    ]


def test_curses_codes_inside_a_paste_are_ignored_except_resizes():
    assert feed_all(*"\x1b[200~a", curses.KEY_ENTER, curses.KEY_RESIZE, *"b\x1b[201~") == [
        KeyChord(Key.RESIZE),
        Paste("ab"),
    ]


def test_an_escape_inside_a_paste_that_is_not_its_end_stays_in_the_text():
    assert feed_text("\x1b[200~a\x1b[1mb\x1b[201~") == [Paste("a\x1b[1mb")]


def test_flushing_after_a_lone_escape_lets_the_next_key_through():
    decoder = KeyDecoder()
    assert decoder.feed("\x1b") == []
    decoder.flush()
    assert decoder.feed("a") == [KeyChord("a")]


def test_flushing_does_not_cut_a_paste_short():
    decoder = KeyDecoder()
    for char in "\x1b[200~ab":
        decoder.feed(char)
    decoder.flush()
    assert [chord for char in "c\x1b[201~" for chord in decoder.feed(char)] == [Paste("abc")]
