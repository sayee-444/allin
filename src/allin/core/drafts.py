"""A Draft as blocks: the one shown in a Window's input first, then those waiting behind it.

A Prompt that comes back to the input never merges with what was there: it becomes a block of
its own, so Enter never sends two texts as one Prompt.
"""

from collections.abc import Iterable


def put_back(blocks: Iterable[str], text: str) -> list[str]:
    """The Draft once ``text`` comes back: shown ahead of the rest, never twice, no empty block."""
    rest = [block for block in blocks if block and block != text]
    return [text, *rest] if text else rest
