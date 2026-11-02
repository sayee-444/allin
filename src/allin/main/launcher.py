"""The Launcher: starts a Window's xterm in its own session and watches it (ADR-0003)."""

import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

# Every window operation of xterm 407 (``man xterm``) but SetWinSizeChars, which Themes use.
_WINDOW_OPS_BUT_SIZE = (
    "ColumnMode",
    "GetChecksum",
    "GetIconTitle",
    "GetScreenSizeChars",
    "GetSelection",
    "GetWinPosition",
    "GetWinSizeChars",
    "GetWinSizePixels",
    "GetWinState",
    "GetWinTitle",
    "LowerWin",
    "MaximizeWin",
    "FullscreenWin",
    "MinimizeWin",
    "PopTitle",
    "PushTitle",
    "RaiseWin",
    "RefreshWin",
    "RestoreWin",
    "SetChecksum",
    "SetSelection",
    "SetWinLines",
    "SetWinPosition",
    "SetWinSizePixels",
    "SetXprop",
    "StatusLine",
)

XTERM_RESOURCES = (
    "XTerm*modifyOtherKeys: 2",  # Ctrl+Enter arrives as ESC [27;5;13~
    # xterm keeps Alt+Enter for its full screen; without it, the key reaches the Window (#57).
    "XTerm*omitTranslation: fullscreen",
    # No text on screen may ask xterm anything (its title, the clipboard by OSC 52, a colour
    # or the letter) and have the answer typed as if by the user (ADR-0018). Themes (#47) open
    # only the orders they use: the size in characters, the letter and the colours. With the
    # allow* resources off, xterm takes exactly what the disallowed* lists leave out.
    "XTerm*allowWindowOps: false",
    "XTerm*disallowedWindowOps: " + ",".join(_WINDOW_OPS_BUT_SIZE),
    "XTerm*allowFontOps: false",
    "XTerm*disallowedFontOps: GetFont",
    "XTerm*allowColorOps: false",
    "XTerm*disallowedColorOps: GetColor,GetAnsiColor",
)


def xterm_command(window_command: Sequence[str], title: str, look: Sequence[str] = ()) -> list[str]:
    """``look``: the letter, size and colours it is born with (``ui.look.xterm_options``).
    ``xterm-256color`` lets the Window redefine colours for a Theme's ``#rrggbb``."""
    command = ["xterm", "-title", title, "-tn", "xterm-256color", *look]
    for resource in XTERM_RESOURCES:
        command += ["-xrm", resource]
    return [*command, "-e", *window_command]


class Launcher:
    def __init__(
        self, command: Sequence[str], log_path: Path, on_exit: Callable[[int], None]
    ) -> None:
        self._command = list(command)
        self._log_path = log_path
        self._on_exit = on_exit
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def pid(self) -> int:
        assert self._process is not None, "pid asked for before start()"
        return self._process.pid

    def start(self) -> None:
        """Spawn the process (fork+exec done safely by Popen) and a watcher thread."""
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("ab") as log:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        threading.Thread(target=self._watch, name="launcher-watcher", daemon=True).start()

    def wait(self, timeout: float) -> bool:
        """Wait for the process to exit on its own; return whether it did."""
        if self._process is None:
            return True
        try:
            self._process.wait(timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    def terminate(self, timeout: float) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()

    def _watch(self) -> None:
        assert self._process is not None
        self._on_exit(self._process.wait())
