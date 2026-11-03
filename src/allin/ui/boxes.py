"""The box Main speaks through without the Chat: a Notice that only informs, a Confirmation
that asks yes or no (ADR-0009), or the one that takes a Secreto's value, which it never shows
(#124). Curses-free; the screen only draws what this decides.

Boxes wait in arrival order and the first takes every key until it is answered, so a key meant
for the input never answers a question by accident.
"""

from dataclasses import dataclass, field
from typing import Literal

from allin.core.ids import BoxId
from allin.ui.keys import Key, KeyChord, KeyEvent, Paste, typed_char
from allin.ui.wrap import wrap

type BoxKind = Literal["notice", "confirm", "secret"]

_YES = frozenset("sSyY")
_NO = frozenset("nN")
# Keys that change only the view: they never answer a box, and work while one waits.
VIEW_KEYS = frozenset({Key.RESIZE, Key.PAGE_UP, Key.PAGE_DOWN})
_HINTS: dict[BoxKind, str] = {
    "notice": "any key closes",
    "confirm": "[s]i / [n]o",
    "secret": "Enter saves · empty Enter cancels",
}
_HIDDEN = "•"  # what a Secreto's box shows for each character typed


@dataclass(frozen=True)
class Box:
    box_id: BoxId
    kind: BoxKind
    title: str
    text: str


@dataclass(frozen=True)
class BoxClosed:
    """The user answered a box: ``True`` or ``False`` to a Confirmation, ``None`` for a Notice;
    ``value`` is what they typed in a Secreto's, ``None`` if they left it empty."""

    box_id: BoxId
    answer: bool | None
    value: str | None = field(default=None, repr=False)  # never in a traceback


class BoxStack:
    def __init__(self) -> None:
        self._boxes: list[Box] = []
        self._typed = ""  # in the shown Secreto's box: kept here alone, never drawn

    @property
    def typed(self) -> int:
        """How many characters were typed in the shown Secreto's box."""
        return len(self._typed)

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
        if self.current is not None and self.current.box_id == box_id:
            self._typed = ""
        self._boxes = [box for box in self._boxes if box.box_id != box_id]

    def key(self, event: KeyEvent) -> BoxClosed | None:
        """What a key does to the shown box; ``None`` if it leaves it open."""
        box = self.current
        if box is not None and box.kind == "secret":
            return self._secret_key(box, event)
        if box is None or not isinstance(event, KeyChord) or event.key in VIEW_KEYS:
            return None
        answer = _answer(box, event)
        if box.kind == "confirm" and answer is None:
            return None
        self._boxes.pop(0)
        return BoxClosed(box.box_id, answer)

    def _secret_key(self, box: Box, event: KeyEvent) -> BoxClosed | None:
        """A Secreto is one line: a paste loses its line breaks, and Enter gives what was
        typed."""
        match event:
            case Paste(text=text):
                self._typed += text.replace("\r", "").replace("\n", "")
            case KeyChord(key=Key.BACKSPACE, ctrl=False, alt=False):
                self._typed = self._typed[:-1]
            case KeyChord(key=Key.ENTER, ctrl=False, alt=False, shift=False):
                value, self._typed = self._typed, ""
                self._boxes.pop(0)
                return BoxClosed(box.box_id, None, value=value or None)
            case KeyChord() if (char := typed_char(event)) is not None:
                self._typed += char
            case _:
                pass
        return None


def box_rows(box: Box, width: int, hidden: int = 0) -> list[str]:
    """The box's text wrapped to ``width``, then the keys that answer it; a Secreto's shows
    one ``•`` for each of the ``hidden`` characters typed in it, as many as fit."""
    rows = wrap(box.text, width)
    if box.kind == "secret":
        rows.append(_HIDDEN * min(hidden, width))
    return [*rows, _HINTS[box.kind]]


def _answer(box: Box, chord: KeyChord) -> bool | None:
    if box.kind == "notice" or chord.ctrl or chord.alt:
        return None
    if chord.key in _YES:
        return True
    return False if chord.key in _NO else None
