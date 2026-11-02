"""Turn what curses reports from the mouse into typed events.

Main and the Window ask the terminal for button presses and releases (``mousemask``) and for
motion while a button is held (xterm's mode 1002, which curses does not ask for). Only curses'
constants are used, so it is tested without a terminal.
"""

import curses
from dataclasses import dataclass
from enum import StrEnum

# Motion with the button held: xterm's mode 1002 reports it, curses' mousemask asks for 1000.
DRAG_REPORTS_ON = "\x1b[?1002h"
DRAG_REPORTS_OFF = "\x1b[?1002l"
MOUSE_MASK = curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION


class MouseAction(StrEnum):
    PRESS = "press"  # the left button
    DRAG = "drag"
    RELEASE = "release"
    WHEEL_UP = "wheel_up"
    WHEEL_DOWN = "wheel_down"


@dataclass(frozen=True)
class Mouse:
    """A mouse event on the terminal's cell at row ``y``, column ``x``."""

    action: MouseAction
    y: int
    x: int


_ACTIONS = (
    (curses.BUTTON1_PRESSED, MouseAction.PRESS),
    (curses.BUTTON1_RELEASED, MouseAction.RELEASE),
    (curses.BUTTON4_PRESSED, MouseAction.WHEEL_UP),
    (curses.BUTTON5_PRESSED, MouseAction.WHEEL_DOWN),
    (curses.REPORT_MOUSE_POSITION, MouseAction.DRAG),
)


def mouse_event(x: int, y: int, bstate: int) -> Mouse | None:
    """What ``curses.getmouse`` reported; ``None`` for buttons AllIn does not use."""
    for flag, action in _ACTIONS:
        if bstate & flag:
            return Mouse(action, y=y, x=x)
    return None
