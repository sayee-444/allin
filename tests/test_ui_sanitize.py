import pytest

from allin.ui.sanitize import approval_visible, visible


def test_plain_text_newlines_and_tabs_are_left_alone():
    assert visible("hola\n\tmundo ñ 漢") == "hola\n\tmundo ñ 漢"


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("\x1b[2J", "␛[2J"),  # C0: ESC can no longer clear the screen
        ("\x07", "␇"),  # BEL
        ("\x00", "␀"),
        ("\r", "␍"),  # nor move the cursor back over what was drawn
        ("\x7f", "␡"),  # DEL
        ("\x9b2J", "\ufffd9B2J"),  # C1: CSI on its own, which curses does not stop
        ("\x85", "\ufffd85"),
    ],
)
def test_control_characters_become_visible_marks(raw: str, shown: str):
    assert visible(raw) == shown


def test_every_control_character_is_marked():
    controls = [chr(c) for c in [*range(0x20), 0x7F, *range(0x80, 0xA0)] if chr(c) not in "\n\t"]
    shown = visible("".join(controls))
    assert not any(c in shown for c in controls)


def test_the_approval_screen_also_marks_direction_and_invisible_characters():
    # Trojan Source: an RLO and its isolates reorder what the eye reads (ADR-0013). Written
    # with chr(), so this file holds none of them.
    rlo, lri, pdi, zwsp, bom, nbsp = map(chr, (0x202E, 0x2066, 0x2069, 0x200B, 0xFEFF, 0xA0))
    code = f'if user != "admin{rlo} {lri}// check{pdi} {lri}":\n\tx{zwsp} = 1{bom}{nbsp}'
    assert approval_visible(code) == (
        'if user != "admin<U+202E> <U+2066>// check<U+2069> <U+2066>":\n'
        "\tx<U+200B> = 1<U+FEFF><U+00A0>"
    )


def test_the_approval_screen_marks_control_characters_as_any_screen_does():
    assert approval_visible("a\x1b[2Jb") == "a␛[2Jb"


def test_plain_code_and_other_scripts_stay_as_they_are():
    assert approval_visible("año = 'ñandú' # 日本\n") == "año = 'ñandú' # 日本\n"
