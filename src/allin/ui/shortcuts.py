"""Atajos (#57): which key runs which action of the input or, in Main's Chat, which Command
main. Curses-free, so the registry is tested without a terminal.

A Settings file keeps only the changes to the defaults, key by key: an action, a command, or
``off`` for a default key taken away. Keys are named ``ctrl+b``, ``shift+enter``, ``alt+x`` or
``f5``, and only those a Window can tell apart are taken.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import JsonValue

from allin.core.errors import (
    AllInError,
    KeyNameInvalid,
    LastSendKey,
    ShortcutInvalid,
    ShortcutMissing,
    ShortcutTaken,
)
from allin.ui.keys import Key, KeyChord

type Action = Literal[
    "send",
    "newline",
    "interrupt",  # Shift+Enter's: cut the running Turn (ADR-0024)
    "withdraw",  # the last queued Prompt back to the input
    "up",  # a row up; on the first, withdraw while Prompts are queued, else ``older``
    "down",  # a row down; on the last, ``newer``
    "older",  # the Prompt before, from the history
    "newer",
    "scroll_up",
    "scroll_down",
    "paste",
]
ACTIONS: tuple[Action, ...] = get_args(Action.__value__)
OFF = "off"


@dataclass(frozen=True)
class Command:
    """An Atajo that runs a Command main, with the human's Origin (ADR-0013)."""

    text: str


DEFAULT_KEYS: Mapping[str, str] = {
    "enter": "send",
    "ctrl+enter": "newline",
    "shift+enter": "interrupt",
    "up": "up",
    "down": "down",
    "pageup": "scroll_up",
    "pagedown": "scroll_down",
    "ctrl+v": "paste",
}

_MODIFIERS = ("ctrl", "alt", "shift")
_NAMES: dict[Key, str] = {Key.PAGE_UP: "pageup", Key.PAGE_DOWN: "pagedown"}
_BY_NAME: dict[str, Key] = {_NAMES.get(key, key.value): key for key in Key if key != Key.RESIZE}
# What a Window's decoder gives with modifiers: Enter in every chord (xterm's modifyOtherKeys),
# Backspace with Ctrl; any other named key only alone.
_MODIFIABLE: dict[Key, frozenset[str]] = {
    Key.ENTER: frozenset(_MODIFIERS),
    Key.BACKSPACE: frozenset({"ctrl"}),
}


def parse_key(typed: str) -> str:
    """The key's one name; ``KeyNameInvalid`` for a name that is none, or a key a Window
    cannot tell apart."""
    *mods, base = typed.strip().lower().split("+")
    if not base or any(mod not in _MODIFIERS for mod in mods) or len(set(mods)) != len(mods):
        raise KeyNameInvalid(key=typed, reason="name it as ctrl+b, shift+enter, alt+x or f5")
    named = _BY_NAME.get(base)
    if named is not None:
        allowed = _MODIFIABLE.get(named, frozenset[str]())
        if not set(mods) <= allowed:
            raise KeyNameInvalid(key=typed, reason=f"a Window cannot tell {typed} from {base}")
    elif len(base) != 1 or not base.isprintable() or base.isspace():
        raise KeyNameInvalid(key=typed, reason=f"no key is called {base}")
    elif "shift" in mods or not mods:
        raise KeyNameInvalid(key=typed, reason="a character alone, or with shift, is typed")
    return "+".join([*(mod for mod in _MODIFIERS if mod in mods), base])


def key_name(chord: KeyChord) -> str:
    """The name of a key as the decoder gave it, as ``parse_key`` names it."""
    mods = [
        mod for mod, on in zip(_MODIFIERS, (chord.ctrl, chord.alt, chord.shift), strict=True) if on
    ]
    base = _NAMES.get(chord.key, chord.key.value) if isinstance(chord.key, Key) else chord.key
    return "+".join([*mods, base.lower()])


def effective_keys(changes: Mapping[str, str]) -> dict[str, str]:
    """The defaults with ``changes`` on top; an entry that names no key or does nothing it
    could, such as a hand-edited one, is left out."""
    table = dict(DEFAULT_KEYS)
    for typed, binding in changes.items():
        try:
            key = parse_key(typed)
            if binding == OFF:
                table.pop(key, None)
            else:
                table[key] = _checked(binding, commands=True)
        except AllInError:
            continue
    return table


def assign(
    changes: Mapping[str, str],
    typed: str,
    binding: str,
    replace: bool = False,
    commands: bool = True,
) -> dict[str, str]:
    """``changes`` once ``typed`` runs ``binding``, or has no Atajo with ``off``. A taken key
    needs ``replace``, and some key must still send; ``commands`` says whether a Command main
    may be bound (Main's Chat) or not (a Window, until F2's Command sessions)."""
    key = parse_key(typed)
    table = effective_keys(changes)
    if binding == OFF:
        if key not in table:
            raise ShortcutMissing(key=key)
        table.pop(key)
    else:
        binding = _checked(binding, commands)
        if table.get(key, binding) != binding and not replace:
            raise ShortcutTaken(key=key, bound=table[key])
        table[key] = binding
    if "send" not in table.values():
        raise LastSendKey(key=key)
    return _changes(table)


def resolve(chord: KeyChord, table: Mapping[str, str]) -> Action | Command | None:
    """What a key runs; ``None`` for a key with no Atajo, such as one being typed."""
    binding = table.get(key_name(chord))
    if binding is None:
        return None
    if binding.startswith("/"):
        return Command(binding)
    for action in ACTIONS:
        if binding == action:
            return action
    return None


def shown_keys(changes: Mapping[str, str]) -> list[str]:
    """Each key with its Atajo, ``*`` after those that differ from the defaults; a default
    taken away shows as ``off``."""
    table = effective_keys(changes)
    lines: list[str] = []
    for key in dict.fromkeys([*DEFAULT_KEYS, *table]):
        binding = table.get(key, OFF)
        changed = binding != DEFAULT_KEYS.get(key, OFF)
        lines.append(f"{key} {binding}{' *' if changed else ''}")
    return lines


def _checked(binding: str, commands: bool) -> str:
    if binding in ACTIONS or (commands and binding.startswith("/") and len(binding) > 1):
        return binding
    allowed: list[str] = [*ACTIONS, OFF, *(["/<command>"] if commands else [])]
    raise ShortcutInvalid(binding=binding, allowed=list[JsonValue](allowed))


def _changes(table: Mapping[str, str]) -> dict[str, str]:
    """What ``table`` changes of the defaults: each new or different key, ``off`` for each
    default left out."""
    changes = {key: binding for key, binding in table.items() if DEFAULT_KEYS.get(key) != binding}
    changes |= {key: OFF for key in DEFAULT_KEYS if key not in table}
    return changes
