"""The text being typed in the input pad, kept free of curses so it can be tested.

Text is stored as the pad draws it (see ``wrap.printable``), so what is shown and what is sent
never differ.
"""

from allin.core.drafts import put_back
from allin.ui.wrap import printable

_DELIMITERS = frozenset(".[](){}\n")
_BLANKS = " "


class InputBuffer:
    """``text`` is the block being shown and edited; ``waiting`` the Draft's blocks behind it."""

    def __init__(self) -> None:
        self.text = ""
        self.waiting: list[str] = []

    @property
    def blocks(self) -> tuple[str, ...]:
        """The whole Draft, shown block first; an emptied shown block is left out."""
        return tuple(block for block in (self.text, *self.waiting) if block)

    def insert(self, text: str) -> None:
        self.text += _printable(text)

    def take(self) -> str:
        """Return the shown block and show the next waiting one, if any (Enter)."""
        text = self.text
        self.text = self.waiting.pop(0) if self.waiting else ""
        return text

    def restore(self, text: str) -> None:
        """Show a Prompt that came back; what was there waits behind it (``put_back``)."""
        self.text, *self.waiting = put_back(self.blocks, _printable(text)) or [""]

    def backspace(self) -> None:
        self.text = self.text[:-1]

    def delete_word(self) -> None:
        """Delete back to the previous whitespace, ``.`` or bracket (Ctrl+Backspace).

        Trailing spaces go first; then a delimiter (or line break) right before the cursor goes
        on its own, or else the word before it.
        """
        text = self.text.rstrip(_BLANKS)
        if text and text[-1] in _DELIMITERS:
            text = text[:-1]
        else:
            while text and not _is_delimiter(text[-1]):
                text = text[:-1]
        self.text = text


def _printable(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(printable(line) for line in lines)


def _is_delimiter(char: str) -> bool:
    return char.isspace() or char in _DELIMITERS
