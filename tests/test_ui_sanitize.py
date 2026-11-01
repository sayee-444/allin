import pytest

from allin.ui.sanitize import visible


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
