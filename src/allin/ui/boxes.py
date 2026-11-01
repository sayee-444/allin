"""The box Main speaks through without the Chat: a Notice that only informs, or a Confirmation
that asks yes or no (ADR-0009). Curses-free; the screen only draws what this decides.

Boxes wait in arrival order and the first takes every key until it is answered, so a key meant
for the input never answers a question by accident.
"""

from dataclasses import dataclass
from typing import Literal

from allin.core.ids import BoxId
from allin.ui.keys import Key, KeyChord, KeyEvent
from allin.ui.wrap import wrap

type BoxKind = Literal["notice", "confirm"]

_YES = frozenset("sSyY")
_NO = frozenset("nN")
_NOT_ANSWERS = frozenset({Key.RESIZE, Key.PAGE_UP, Key.PAGE_DOWN})
_HINTS: dict[BoxKind, str] = {"notice": "any key closes", "confirm": "[s]i / [n]o"}


@dataclass(frozen=True)
class Box:
    box_id: BoxId
    kind: BoxKind
    title: str
    text: str


@dataclass(frozen=True)
class BoxClosed:
    """The user answered a box: ``True`` or ``False`` to a Confirmation, ``None`` for a Notice."""

    box_id: BoxId
    answer: bool | None


class BoxStack:
    def __init__(self) -> None:
        self._boxes: list[Box] = []

    @property
    def current(self) -> Box | None:
        return self._boxes[0] if self._boxes else None

    @property
    def takes_keys(self) -> bool:
        return bool(self._boxes)

    def show(self, box: Box) -> None:
        if all(shown.box_id != box.box_id for shown in self._boxes):
            self._boxes.append(box)

    def hide(self, box_id: BoxId) -> None:
        """Drop a box answered elsewhere, or no longer valid."""
        self._boxes = [box for box in self._boxes if box.box_id != box_id]

    def key(self, event: KeyEvent) -> BoxClosed | None:
        """What a key does to the shown box; ``None`` if it leaves it open."""
        box = self.current
        if box is None or not isinstance(event, KeyChord) or event.key in _NOT_ANSWERS:
            return None
        answer = _answer(box, event)
        if box.kind == "confirm" and answer is None:
            return None
        self._boxes.pop(0)
        return BoxClosed(box.box_id, answer)


def box_rows(box: Box, width: int) -> list[str]:
    """The box's text wrapped to ``width``, then the keys that answer it."""
    return [*wrap(box.text, width), _HINTS[box.kind]]


def _answer(box: Box, chord: KeyChord) -> bool | None:
    if box.kind == "notice" or chord.ctrl or chord.alt:
        return None
    if chord.key in _YES:
        return True
    return False if chord.key in _NO else None
