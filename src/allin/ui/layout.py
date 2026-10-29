"""Screen geometry shared by Main and the Window, kept free of curses so it can be tested.

Two bordered boxes stacked vertically: the Chat on top and the input at the bottom. Each box
holds a pad one cell inside its border.
"""

from dataclasses import dataclass

from allin.core.errors import ScreenTooSmall

INPUT_MAX_SHARE = 0.4
_BORDER = 1


@dataclass(frozen=True)
class Rect:
    top: int
    left: int
    height: int
    width: int

    def inner(self) -> "Rect":
        return Rect(
            self.top + _BORDER,
            self.left + _BORDER,
            self.height - 2 * _BORDER,
            self.width - 2 * _BORDER,
        )


@dataclass(frozen=True)
class Layout:
    chat_box: Rect
    chat_pad: Rect
    input_box: Rect
    input_pad: Rect


def compute_layout(height: int, width: int, input_lines: int) -> Layout:
    """Place both boxes for a terminal of ``height`` x ``width`` and an input of ``input_lines``.

    The input box starts with one line inside its border, grows with its content and never takes
    more than 40 % of the height (though it always keeps one line).
    """
    max_input_rows = max(1, int(height * INPUT_MAX_SHARE) - 2 * _BORDER)
    input_rows = min(max(1, input_lines), max_input_rows)
    input_box_height = input_rows + 2 * _BORDER

    chat_box = Rect(0, 0, height - input_box_height, width)
    input_box = Rect(chat_box.height, 0, input_box_height, width)
    layout = Layout(chat_box, chat_box.inner(), input_box, input_box.inner())
    if min(layout.chat_pad.height, layout.chat_pad.width, layout.input_pad.width) < 1:
        raise ScreenTooSmall(height=height, width=width)
    return layout
