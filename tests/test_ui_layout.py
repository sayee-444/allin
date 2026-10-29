import pytest

from allin.core.errors import AllInError, ScreenTooSmall
from allin.ui.layout import Rect, compute_layout


def test_input_box_starts_with_height_three_at_the_bottom():
    layout = compute_layout(height=40, width=100, input_lines=1)
    assert layout.input_box == Rect(top=37, left=0, height=3, width=100)
    assert layout.input_pad == Rect(top=38, left=1, height=1, width=98)
    assert layout.chat_box == Rect(top=0, left=0, height=37, width=100)
    assert layout.chat_pad == Rect(top=1, left=1, height=35, width=98)


def test_input_grows_line_by_line():
    layout = compute_layout(height=40, width=100, input_lines=5)
    assert layout.input_box == Rect(top=33, left=0, height=7, width=100)
    assert layout.input_pad.height == 5
    assert layout.chat_box.height == 33


def test_input_box_never_exceeds_forty_percent_of_the_height():
    layout = compute_layout(height=40, width=100, input_lines=50)
    assert layout.input_box == Rect(top=24, left=0, height=16, width=100)
    assert layout.input_pad.height == 14
    layout = compute_layout(height=10, width=100, input_lines=50)
    assert layout.input_box.height == 4
    assert layout.input_pad.height == 2


def test_input_keeps_one_line_even_when_forty_percent_is_less_than_three_rows():
    layout = compute_layout(height=6, width=20, input_lines=3)
    assert layout.input_box.height == 3
    assert layout.chat_pad.height == 1


@pytest.mark.parametrize(("height", "width"), [(5, 100), (40, 2), (0, 0)])
def test_no_room_for_a_pad_raises_screen_too_small(height: int, width: int):
    with pytest.raises(ScreenTooSmall) as info:
        compute_layout(height=height, width=width, input_lines=1)
    assert isinstance(info.value, AllInError)
    assert info.value.fields == {"height": height, "width": width}
