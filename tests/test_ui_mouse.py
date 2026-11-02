import curses

import pytest

from allin.ui.mouse import Mouse, MouseAction, mouse_event


@pytest.mark.parametrize(
    ("bstate", "action"),
    [
        (curses.BUTTON1_PRESSED, MouseAction.PRESS),
        (curses.REPORT_MOUSE_POSITION, MouseAction.DRAG),
        (curses.BUTTON1_RELEASED, MouseAction.RELEASE),
        (curses.BUTTON4_PRESSED, MouseAction.WHEEL_UP),
        (curses.BUTTON5_PRESSED, MouseAction.WHEEL_DOWN),
    ],
)
def test_curses_mouse_reports_become_typed_events(bstate: int, action: MouseAction):
    assert mouse_event(x=7, y=3, bstate=bstate) == Mouse(action, y=3, x=7)


def test_other_buttons_and_clicks_are_dropped():
    assert mouse_event(x=0, y=0, bstate=curses.BUTTON3_PRESSED) is None
    assert mouse_event(x=0, y=0, bstate=curses.BUTTON2_RELEASED) is None
