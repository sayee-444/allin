"""Turn what curses reads from the keyboard into typed key chords.

Only curses' key-code constants are used, so it is tested without a terminal.
"""

import curses
from dataclasses import dataclass
from enum import StrEnum


class Key(StrEnum):
    ENTER = "enter"
    BACKSPACE = "backspace"
    PAGE_UP = "page_up"
    PAGE_DOWN = "page_down"
    RESIZE = "resize"  # not a key: curses reports terminal resizes as one


@dataclass(frozen=True)
class KeyChord:
    """A key pressed with its modifiers; ``key`` is the character itself for text keys."""

    key: Key | str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False


@dataclass(frozen=True)
class Paste:
    text: str


type KeyEvent = KeyChord | Paste


_SINGLE: dict[str | int, KeyChord] = {
    "\n": KeyChord(Key.ENTER),
    "\r": KeyChord(Key.ENTER),
    curses.KEY_ENTER: KeyChord(Key.ENTER),
    "\x7f": KeyChord(Key.BACKSPACE),
    curses.KEY_BACKSPACE: KeyChord(Key.BACKSPACE),  # terminfo kbs is ^? on xterm
    "\x08": KeyChord(Key.BACKSPACE, ctrl=True),  # xterm sends ^H for Ctrl+Backspace
    curses.KEY_PPAGE: KeyChord(Key.PAGE_UP),
    curses.KEY_NPAGE: KeyChord(Key.PAGE_DOWN),
    curses.KEY_RESIZE: KeyChord(Key.RESIZE),
}


_ESC = "\x1b"
_PASTE_START = _ESC + "[200~"  # bracketed paste: CSI 200 ~ text CSI 201 ~
_PASTE_END = _ESC + "[201~"
_MODIFY_OTHER_KEYS = "27"  # CSI 27 ; modifiers ; code ~  (xterm modifyOtherKeys)
# The key's own code, so 8 is Backspace here; the ^H byte alone already means Ctrl+Backspace.
_CODES: dict[int, Key] = {13: Key.ENTER, 127: Key.BACKSPACE, 8: Key.BACKSPACE}


class KeyDecoder:
    """Stateful: escape sequences arrive from curses one character at a time."""

    def __init__(self) -> None:
        self._sequence: str | None = None  # what follows an ESC still being read
        self._paste: str | None = None  # text of a bracketed paste still being read

    def feed(self, key: str | int) -> list[KeyEvent]:
        if self._paste is not None:
            return self._feed_paste(key)
        if self._sequence is not None:
            return self._feed_sequence(key)
        if key == _ESC:
            self._sequence = ""
            return []
        if key in _SINGLE:
            return [_SINGLE[key]]
        if isinstance(key, int):
            return []
        return [KeyChord(key)]

    def flush(self) -> None:
        """Drop a half-read escape sequence; call when no key arrived for a while.

        A lone ESC would otherwise swallow the next key. A paste is kept: the terminal sends it
        all at once, and its end marker always comes.
        """
        self._sequence = None

    def _feed_sequence(self, key: str | int) -> list[KeyEvent]:
        assert self._sequence is not None
        if not isinstance(key, str):
            self._sequence = None
            return self.feed(key)
        sequence = self._sequence + key
        if sequence == "[" or (sequence.startswith("[") and not _is_final(key)):
            self._sequence = sequence
            return []
        self._sequence = None
        if _ESC + sequence == _PASTE_START:
            self._paste = ""
            return []
        chord = _decode_csi(sequence[1:]) if sequence.startswith("[") else None
        return [chord] if chord else []

    def _feed_paste(self, key: str | int) -> list[KeyEvent]:
        assert self._paste is not None
        if key == curses.KEY_RESIZE:
            return [_SINGLE[key]]
        if not isinstance(key, str):
            return []  # the terminal sends text only; a curses code here is not part of it
        self._paste += key
        if not self._paste.endswith(_PASTE_END):
            return []
        text, self._paste = self._paste.removesuffix(_PASTE_END), None
        return [Paste(text)]


def _is_final(char: str) -> bool:
    return "\x40" <= char <= "\x7e"


def _decode_csi(body: str) -> KeyChord | None:
    """Decode the part of a CSI sequence after ``ESC [``; None for sequences not handled."""
    params = body[:-1].split(";")
    if body[-1:] != "~" or len(params) != 3 or params[0] != _MODIFY_OTHER_KEYS:
        return None
    try:
        modifiers, code = int(params[1]) - 1, int(params[2])
        key = _CODES.get(code) or chr(code)
    except ValueError:
        return None
    return KeyChord(
        key, ctrl=bool(modifiers & 4), alt=bool(modifiers & 2), shift=bool(modifiers & 1)
    )
