"""The text being typed in the input pad, kept free of curses so it can be tested.

Text is stored as the pad draws it (see ``wrap.printable``), so what is shown and what is sent
never differ.
"""

from allin.ui.wrap import printable

_DELIMITERS = frozenset(".[](){}\n")
_BLANKS = " "


class InputBuffer:
    def __init__(self) -> None:
        self.text = ""

    def insert(self, text: str) -> None:
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.text += "\n".join(printable(line) for line in lines)

    def take(self) -> str:
        """Empty the input and return what it held (Enter)."""
        text, self.text = self.text, ""
        return text

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


def _is_delimiter(char: str) -> bool:
    return char.isspace() or char in _DELIMITERS
