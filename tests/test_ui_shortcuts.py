"""Atajos (#57): the key registry, its overlaps and what a key runs."""

import pytest

from allin.core.errors import (
    KeyNameInvalid,
    LastSendKey,
    ShortcutInvalid,
    ShortcutMissing,
    ShortcutTaken,
)
from allin.ui.keys import Key, KeyChord
from allin.ui.shortcuts import (
    DEFAULT_KEYS,
    Command,
    assign,
    effective_keys,
    key_name,
    parse_key,
    resolve,
    shown_keys,
)


@pytest.mark.parametrize(
    ("typed", "name"),
    [
        ("Enter", "enter"),
        ("shift+enter", "shift+enter"),
        ("Shift+Ctrl+Enter", "ctrl+shift+enter"),
        ("ctrl+B", "ctrl+b"),
        ("alt+x", "alt+x"),
        ("ctrl+alt+x", "ctrl+alt+x"),
        ("f5", "f5"),
        ("pageup", "pageup"),
        ("up", "up"),
    ],
)
def test_a_key_is_named_one_way(typed: str, name: str):
    assert parse_key(typed) == name


@pytest.mark.parametrize(
    "typed",
    [
        "x",  # a plain character would stop it being typed
        "shift+x",  # that is just an uppercase X
        "ctrl+up",  # the Window cannot tell it from up
        "alt+pageup",
        "shift+f5",
        "hyper+x",
        "ctrl+",
        "f13",
        "",
    ],
)
def test_a_key_the_window_cannot_tell_apart_is_refused(typed: str):
    with pytest.raises(KeyNameInvalid):
        parse_key(typed)


@pytest.mark.parametrize(
    ("chord", "name"),
    [
        (KeyChord(Key.ENTER), "enter"),
        (KeyChord(Key.ENTER, shift=True), "shift+enter"),
        (KeyChord(Key.PAGE_UP), "pageup"),
        (KeyChord("b", ctrl=True), "ctrl+b"),
        (KeyChord(Key.F5), "f5"),
    ],
)
def test_a_chord_is_named_as_it_is_assigned(chord: KeyChord, name: str):
    assert key_name(chord) == name


def test_the_defaults_are_todays_keys():
    assert DEFAULT_KEYS == {
        "enter": "send",
        "ctrl+enter": "newline",
        "shift+enter": "interrupt",
        "up": "up",
        "down": "down",
        "pageup": "scroll_up",
        "pagedown": "scroll_down",
        "ctrl+v": "paste",
    }
    assert effective_keys({}) == DEFAULT_KEYS


def test_another_key_to_send_then_enter_off():
    changes = assign({}, "alt+enter", "send")
    changes = assign(changes, "enter", "off")
    assert changes == {"alt+enter": "send", "enter": "off"}
    table = effective_keys(changes)
    assert table["alt+enter"] == "send" and "enter" not in table


def test_a_taken_key_says_what_it_does_unless_replaced():
    with pytest.raises(ShortcutTaken) as taken:
        assign({}, "up", "older")
    assert taken.value.fields == {"key": "up", "bound": "up"}
    assert assign({}, "up", "older", replace=True) == {"up": "older"}


def test_the_last_key_to_send_cannot_go():
    with pytest.raises(LastSendKey):
        assign({}, "enter", "off")
    with pytest.raises(LastSendKey):
        assign({}, "enter", "newline", replace=True)


def test_a_key_back_to_its_default_is_no_change():
    changes = assign({}, "up", "older", replace=True)
    assert assign(changes, "up", "up", replace=True) == {}


def test_off_on_a_key_with_no_shortcut_is_an_error():
    with pytest.raises(ShortcutMissing):
        assign({}, "f5", "off")


def test_a_binding_is_an_action_or_where_allowed_a_command():
    with pytest.raises(ShortcutInvalid):
        assign({}, "f5", "explode")
    assert assign({}, "f5", "/new") == {"f5": "/new"}
    with pytest.raises(ShortcutInvalid):
        assign({}, "f5", "/new", commands=False)


def test_a_key_resolves_to_its_action_or_command():
    table = effective_keys({"f5": "/session abc123", "ctrl+p": "older"})
    assert resolve(KeyChord(Key.F5), table) == Command("/session abc123")
    assert resolve(KeyChord("p", ctrl=True), table) == "older"
    assert resolve(KeyChord(Key.ENTER), table) == "send"
    assert resolve(KeyChord("p"), table) is None  # typing


def test_a_bad_entry_in_a_settings_file_is_left_out():
    assert effective_keys({"x": "send", "f5": "explode"}) == DEFAULT_KEYS


def test_shown_keys_mark_the_changed_ones():
    lines = shown_keys(assign({}, "alt+enter", "send"))
    assert "enter send" in lines
    assert "alt+enter send *" in lines
    lines = shown_keys({"enter": "off", "alt+enter": "send"})
    assert "enter off *" in lines
