"""What any AllIn screen draws in place of control characters (ADR-0013).

Text from the LLM or a tool reaches the terminal through curses, which passes C1 characters
on as they are: a bare U+009B is a CSI to xterm. Each C0, C1 and DEL character but newline and
tab becomes a visible mark, so no text moves the cursor, touches the clipboard or paints a fake
box.
"""

import unicodedata

_KEPT = frozenset("\n\t")
# What the approval screen also marks: format characters (direction marks such as U+202E,
# zero-width ones, the BOM), separators but the plain space, private and unassigned ones.
_HIDDEN_CATEGORIES = frozenset({"Cf", "Zs", "Zl", "Zp", "Co", "Cn"})
_DEL = 0x7F
_C1 = range(0x80, 0xA0)
_CONTROL_PICTURES = 0x2400  # U+2400..U+241F picture each C0 character; U+2421 is DEL's


def visible(text: str) -> str:
    """``text`` with its control characters replaced by visible marks."""
    if text.isprintable() or not any(_is_control(char) for char in text):
        return text
    return "".join(_mark(char) if _is_control(char) else char for char in text)


def approval_visible(text: str) -> str:
    """``text`` as the approval screen shows code (ADR-0013): control characters marked as on
    any screen, and direction, invisible and unusual space characters as ``<U+202E>``, so no
    code hides what it does (Trojan Source)."""
    return "".join(_hidden(char) for char in visible(text))


def _hidden(char: str) -> str:
    if char == " " or unicodedata.category(char) not in _HIDDEN_CATEGORIES:
        return char
    return f"<U+{ord(char):04X}>"


def _is_control(char: str) -> bool:
    code = ord(char)
    return (code < 0x20 or code == _DEL or code in _C1) and char not in _KEPT


def _mark(char: str) -> str:
    code = ord(char)
    if code < 0x20:
        return chr(_CONTROL_PICTURES + code)
    if code == _DEL:
        return "␡"
    return f"\ufffd{code:02X}"  # U+FFFD, then the code: no control picture exists for C1
