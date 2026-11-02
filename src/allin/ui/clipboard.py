"""The system clipboard, through the tools of the desktop the terminal runs on.

Wayland's ``wl-copy`` and ``wl-paste`` if ``WAYLAND_DISPLAY`` says it is there and they are
installed, else X's ``xclip``; with neither, copying and pasting raise ``ClipboardUnavailable``.
xterm under XWayland reaches both, so Wayland's own tools go first.
"""

import subprocess
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from allin.core.errors import ClipboardFailed, ClipboardUnavailable

_TIMEOUT = 2.0

# Runs a tool with ``data`` (if any) on its stdin: its exit code, stdout and stderr.
type Runner = Callable[[Sequence[str], bytes | None], tuple[int, bytes, bytes]]


class Clipboard(Protocol):
    def copy(self, text: str) -> None: ...

    def paste(self) -> str: ...


def _run(command: Sequence[str], data: bytes | None) -> tuple[int, bytes, bytes]:
    # wl-copy and xclip stay in the background to serve the copy: reading their output to
    # its end would wait for them, so a copy only reads their exit code.
    copying = data is not None
    done = subprocess.run(
        command,
        input=data,
        stdout=subprocess.DEVNULL if copying else subprocess.PIPE,
        stderr=subprocess.DEVNULL if copying else subprocess.PIPE,
        timeout=_TIMEOUT,
        check=False,
    )
    return done.returncode, done.stdout or b"", done.stderr or b""


class CommandClipboard:
    def __init__(
        self, copy_command: Sequence[str], paste_command: Sequence[str], run: Runner = _run
    ) -> None:
        self.copy_command = list(copy_command)
        self.paste_command = list(paste_command)
        self._run = run

    def copy(self, text: str) -> None:
        self._call(self.copy_command, text.encode())

    def paste(self) -> str:
        return self._call(self.paste_command, None).decode(errors="replace")

    def _call(self, command: Sequence[str], data: bytes | None) -> bytes:
        tool = command[0]
        try:
            code, out, err = self._run(command, data)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ClipboardFailed(tool=tool, reason=str(error)) from error
        if code != 0:
            reason = err.decode(errors="replace").strip() or f"exit code {code}"
            raise ClipboardFailed(tool=tool, reason=reason)
        return out


class NoClipboard:
    """No tool reaches the clipboard: every use says so."""

    def copy(self, text: str) -> None:
        del text  # there is nowhere to put it
        raise ClipboardUnavailable(tools="wl-copy and wl-paste, or xclip")

    def paste(self) -> str:
        raise ClipboardUnavailable(tools="wl-copy and wl-paste, or xclip")


def system_clipboard(
    env: Mapping[str, str], which: Callable[[str], str | None]
) -> CommandClipboard | NoClipboard:
    """The clipboard this desktop offers; ``which`` finds a tool (``shutil.which``)."""
    if env.get("WAYLAND_DISPLAY") and which("wl-copy") and which("wl-paste"):
        return CommandClipboard(["wl-copy"], ["wl-paste", "--no-newline"])
    if which("xclip"):
        return CommandClipboard(
            ["xclip", "-selection", "clipboard", "-in"],
            ["xclip", "-selection", "clipboard", "-out"],
        )
    return NoClipboard()
