"""A Window whose Main is gone stays read-only and sends Main nothing more (#33)."""

import json
from pathlib import Path

from pydantic import BaseModel

from allin.core.errors import ChannelBroken, MalformedMessage
from allin.ipc.messages import InputDraft, UserInput
from allin.ui.keys import Key, KeyChord, KeyEvent, Paste
from allin.ui.screen import ChatLine, InputLock, ScreenUpdate, read_only_allows
from allin.ui.shortcuts import Action
from allin.window.__main__ import MainLink, MainListener, WindowRecord


class Window:
    """The Window's side of its channel, with a fake one that records what it would send."""

    def __init__(self, tmp_path: Path) -> None:
        self.shown: list[ScreenUpdate] = []
        self.sent: list[BaseModel] = []
        self.record_path = tmp_path / "window.jsonl"
        self.listener = MainListener(self.shown.append, WindowRecord(self.record_path))
        self.link = MainLink(self.sent.append, self.listener.gone)

    def record(self) -> list[dict[str, str]]:
        if not self.record_path.exists():
            return []
        return [json.loads(line) for line in self.record_path.read_text().splitlines()]


def test_after_eof_the_window_sends_main_nothing(tmp_path: Path):
    window = Window(tmp_path)
    window.link.send(UserInput(text="antes"))
    window.listener.on_eof()
    window.link.send(InputDraft(blocks=["escrito después"]))
    window.link.send(UserInput(text="después"))
    assert window.sent == [UserInput(text="antes")]
    assert window.shown == [ChatLine("Main disconnected", "info", "info"), InputLock(True)]


def test_a_broken_fifo_goes_to_the_windows_record_not_its_chat(tmp_path: Path):
    window = Window(tmp_path)
    window.listener.on_error(ChannelBroken(path="x.out", reason="[Errno 32] Broken pipe"))
    window.listener.on_eof()  # the reader's EOF too: told only once
    window.link.send(InputDraft(blocks=["hola"]))
    assert window.sent == []
    assert not any(isinstance(u, ChatLine) and u.role == "error" for u in window.shown)
    assert window.shown == [ChatLine("Main disconnected", "info", "info"), InputLock(True)]
    [entry] = window.record()
    assert entry["event"] == "channel_broken" and "Broken pipe" in entry["error"]


def test_other_errors_still_show_in_the_chat(tmp_path: Path):
    window = Window(tmp_path)
    window.listener.on_error(MalformedMessage(line="{", reason="bad"))
    assert [u.role for u in window.shown if isinstance(u, ChatLine)] == ["error"]
    assert not window.listener.gone.is_set()


def test_a_read_only_screen_takes_only_the_keys_that_move_the_view():
    assert read_only_allows(KeyChord(Key.RESIZE), None)
    assert read_only_allows(KeyChord(Key.PAGE_UP), "scroll_up")
    assert read_only_allows(KeyChord(Key.PAGE_DOWN), "scroll_down")
    refused: list[tuple[KeyEvent, Action | None]] = [
        (KeyChord("a"), None),
        (Paste("pegado"), None),
        (KeyChord(Key.BACKSPACE), None),
        (KeyChord(Key.ENTER), "send"),
        (KeyChord(Key.ENTER, ctrl=True), "newline"),
        (KeyChord(Key.UP), "up"),
        (KeyChord("v", ctrl=True), "paste"),
    ]
    for chord, shortcut in refused:
        assert not read_only_allows(chord, shortcut), chord
