"""The text being typed in the input pad, kept free of curses so it can be tested.

Text is stored as the pad draws it (see ``wrap.printable``), so what is shown and what is sent
never differ. ``cursor`` is where typing and deleting happen (#22); ``history`` holds the
Session's earlier Prompts, which Up and Down bring back.
"""

from collections.abc import Iterable

from allin.core.drafts import put_back
from allin.ui.wrap import cell_width, printable

_DELIMITERS = frozenset(".[](){}\n")
_BLANKS = " "


class InputBuffer:
    """``text`` is the block being shown and edited; ``waiting`` the Draft's blocks behind it."""

    def __init__(self) -> None:
        self.text = ""
        self.cursor = 0  # an index into ``text``: typing goes before the character there
        self.waiting: list[str] = []
        self.history: list[str] = []  # earlier Prompts, oldest first
        self._browsing: int | None = None  # the history entry shown, while Up and Down move
        self._aside = ""  # the shown block, put aside while the history is shown

    @property
    def blocks(self) -> tuple[str, ...]:
        """The whole Draft, shown block first; an emptied shown block is left out, and one put
        aside while browsing the history is kept right behind it."""
        aside = self._aside if self._browsing is not None else ""
        return tuple(block for block in (self.text, aside, *self.waiting) if block)

    def insert(self, text: str) -> None:
        text = _printable(text)
        self.text = self.text[: self.cursor] + text + self.text[self.cursor :]
        self.cursor += len(text)

    def take(self) -> str:
        """Return the shown block and show the next waiting one, if any (Enter); a recalled
        Prompt sent gives back the block it had put aside."""
        text, aside = self.text, self._aside if self._browsing is not None else ""
        self._stop_browsing()
        if aside:
            self._show(aside)
        else:
            self._show(self.waiting.pop(0) if self.waiting else "")
        return text

    def restore(self, text: str) -> None:
        """Show a Prompt that came back; what was there waits behind it (``put_back``)."""
        if self._browsing is not None:  # it lands on the Draft, not on a recalled Prompt
            self._show(self._aside)
            self._stop_browsing()
        self.text, *self.waiting = put_back(self.blocks, _printable(text)) or [""]
        self.cursor = len(self.text)

    def backspace(self) -> None:
        if self.cursor > 0:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
            self.cursor -= 1

    def delete(self) -> None:
        """Delete the character under the cursor (Supr)."""
        self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]

    def delete_word(self) -> None:
        """Delete back from the cursor to the previous whitespace, ``.`` or bracket
        (Ctrl+Backspace).

        Trailing spaces go first; then a delimiter (or line break) right before the cursor goes
        on its own, or else the word before it.
        """
        before = self.text[: self.cursor].rstrip(_BLANKS)
        if before and before[-1] in _DELIMITERS:
            before = before[:-1]
        else:
            while before and not _is_delimiter(before[-1]):
                before = before[:-1]
        self.text = before + self.text[self.cursor :]
        self.cursor = len(before)

    def left(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def right(self) -> None:
        self.cursor = min(len(self.text), self.cursor + 1)

    def home(self) -> None:
        self.cursor = 0

    def end(self) -> None:
        self.cursor = len(self.text)

    def up(self, width: int) -> bool:
        """Move to the visual row above, at the same column or the nearest before it;
        ``False`` on the first row, where Up brings back the history instead."""
        return self._row(-1, width)

    def down(self, width: int) -> bool:
        """Move to the visual row below; ``False`` on the last row."""
        return self._row(1, width)

    def load_history(self, prompts: Iterable[str]) -> None:
        """Take the Session's earlier Prompts, oldest first, as the pad draws them."""
        self.history = []
        for prompt in prompts:
            self.remember(_printable(prompt))

    def remember(self, text: str) -> None:
        """Add a Prompt just sent to the history, unless it repeats the last one."""
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)

    def older(self) -> None:
        """Show the Prompt before the one shown; the first time, put the shown block aside."""
        if not self.history:
            return
        if self._browsing is None:
            self._aside, self._browsing = self.text, len(self.history)
        self._browsing = max(0, self._browsing - 1)
        self._show(self.history[self._browsing])

    def newer(self) -> None:
        """Show the Prompt after the one shown; past the newest, the block put aside."""
        if self._browsing is None:
            return
        self._browsing += 1
        if self._browsing < len(self.history):
            self._show(self.history[self._browsing])
            return
        self._show(self._aside)
        self._stop_browsing()

    def _show(self, text: str) -> None:
        self.text, self.cursor = text, len(text)

    def _stop_browsing(self) -> None:
        self._browsing, self._aside = None, ""

    def _row(self, step: int, width: int) -> bool:
        places = _places(self.text, max(1, width))
        row, column = places[self.cursor]
        target = row + step
        candidates = [index for index, (r, _) in enumerate(places) if r == target]
        if not candidates:
            return False
        before = [index for index in candidates if places[index][1] <= column]
        self.cursor = before[-1] if before else candidates[0]
        return True


def cursor_place(text: str, cursor: int, width: int) -> tuple[int, int]:
    """The visual row and cell where the input pad draws the cursor at index ``cursor`` of
    ``text``, wrapped as ``wrap`` does at ``width``."""
    return _places(text, max(1, width))[cursor]


def _places(text: str, width: int) -> list[tuple[int, int]]:
    """The (row, cell) of each index of ``text`` and of its end, wrapped as ``wrap`` wraps
    ``text`` plus the space the cursor takes at its end."""
    places: list[tuple[int, int]] = []
    row, used, started = 0, 0, False
    for char in text + " ":
        if char == "\n":
            places.append((row, used))
            row, used, started = row + 1, 0, False
            continue
        cells = cell_width(char)
        if used + cells > width and started:
            row, used = row + 1, 0
        places.append((row, used))
        used += cells
        started = True
    return places


def _printable(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(printable(line) for line in lines)


def _is_delimiter(char: str) -> bool:
    return char.isspace() or char in _DELIMITERS
