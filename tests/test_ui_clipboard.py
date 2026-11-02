import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from allin.core.errors import ClipboardFailed, ClipboardUnavailable
from allin.ui.clipboard import CommandClipboard, NoClipboard, system_clipboard

EVERY_TOOL = {"wl-copy", "wl-paste", "xclip"}


def which_of(tools: set[str]) -> Callable[[str], str | None]:
    return lambda name: f"/usr/bin/{name}" if name in tools else None


def test_wayland_uses_wl_copy_and_wl_paste():
    clipboard = system_clipboard({"WAYLAND_DISPLAY": "wayland-0"}, which_of(EVERY_TOOL))
    assert isinstance(clipboard, CommandClipboard)
    assert clipboard.copy_command[0] == "wl-copy"
    assert clipboard.paste_command[0] == "wl-paste"


def test_without_wayland_or_without_its_tools_xclip_is_used():
    for env, tools in (({"DISPLAY": ":0"}, EVERY_TOOL), ({"WAYLAND_DISPLAY": "w"}, {"xclip"})):
        clipboard = system_clipboard(env, which_of(tools))
        assert isinstance(clipboard, CommandClipboard)
        assert clipboard.copy_command[0] == clipboard.paste_command[0] == "xclip"


def test_with_no_tool_copying_and_pasting_say_the_clipboard_is_unavailable():
    clipboard = system_clipboard({"WAYLAND_DISPLAY": "w"}, which_of(set()))
    assert isinstance(clipboard, NoClipboard)
    with pytest.raises(ClipboardUnavailable):
        clipboard.copy("hola")
    with pytest.raises(ClipboardUnavailable):
        clipboard.paste()


class FakeRun:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.calls: list[tuple[Sequence[str], bytes | None]] = []
        self._reply = (returncode, stdout, stderr)

    def __call__(self, command: Sequence[str], data: bytes | None) -> tuple[int, bytes, bytes]:
        self.calls.append((command, data))
        return self._reply


def test_copying_hands_the_text_to_the_tool_and_pasting_reads_it():
    run = FakeRun(stdout="línea 1\nlínea 2".encode())
    clipboard = CommandClipboard(["wl-copy"], ["wl-paste", "--no-newline"], run)
    clipboard.copy("¿qué?")
    assert clipboard.paste() == "línea 1\nlínea 2"
    assert run.calls == [(["wl-copy"], "¿qué?".encode()), (["wl-paste", "--no-newline"], None)]


def test_a_tool_that_fails_says_which_and_why():
    clipboard = CommandClipboard(["xclip"], ["xclip"], FakeRun(1, stderr=b"Can't open display"))
    with pytest.raises(ClipboardFailed) as failed:
        clipboard.paste()
    assert failed.value.fields == {"tool": "xclip", "reason": "Can't open display"}


def test_a_tool_that_cannot_run_or_hangs_is_a_failure_too(tmp_path: Path):
    clipboard = CommandClipboard([str(tmp_path / "missing")], ["true"])
    with pytest.raises(ClipboardFailed):
        clipboard.copy("x")

    def hangs(command: Sequence[str], data: bytes | None) -> tuple[int, bytes, bytes]:
        raise subprocess.TimeoutExpired(command, 2)

    with pytest.raises(ClipboardFailed):
        CommandClipboard(["wl-copy"], ["wl-paste"], hangs).copy("x")
