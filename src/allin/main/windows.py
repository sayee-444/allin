"""Opening a Session's Window: its FIFO pair, the Channel over it and the Launcher (ADR-0003)."""

import os
import queue
import signal
import sys
import time
from pathlib import Path

from pydantic import BaseModel

from allin.core.errors import AllInError, WindowLaunchFailed
from allin.ipc.channel import Channel, FifoPair, create_fifo_pair
from allin.ipc.messages import (
    InputDraft,
    UserInput,
    WindowReady,
    WindowToMain,
    decode_window_message,
)
from allin.main.dispatcher import WindowLink
from allin.main.events import (
    DraftReceived,
    ErrorOccurred,
    InputReceived,
    MainEvent,
    WindowClosed,
    WindowConnected,
)
from allin.main.launcher import Launcher, xterm_command

_CLOSE_GRACE = 2.0
_ORPHAN_GRACE = 2.0


class _WindowListener:
    """Turns what a Window sends into events for the dispatcher."""

    def __init__(self, session_id: str, inbox: queue.Queue[MainEvent]) -> None:
        self._session_id = session_id
        self._inbox = inbox

    def on_message(self, message: WindowToMain) -> None:
        match message:
            case WindowReady():
                self._inbox.put(WindowConnected(self._session_id))
            case UserInput(text=text):
                self._inbox.put(InputReceived(self._session_id, text))
            case InputDraft(text=text):
                self._inbox.put(DraftReceived(self._session_id, text))

    def on_error(self, error: AllInError) -> None:
        self._inbox.put(ErrorOccurred(error, self._session_id))

    def on_eof(self) -> None:
        pass  # the Launcher's watcher reports the closed Window


class _Window:
    """Main's side of one open Window (a ``WindowLink``)."""

    def __init__(
        self,
        channel: Channel[WindowToMain],
        launcher: Launcher,
        fifos: FifoPair,
    ) -> None:
        self._channel = channel
        self._launcher = launcher
        self._fifos = fifos

    @property
    def pid(self) -> int:
        return self._launcher.pid

    def send(self, message: BaseModel) -> None:
        self._channel.send(message)

    def close(self) -> None:
        self._channel.close()
        if not self._launcher.wait(_CLOSE_GRACE):
            self._launcher.terminate(_CLOSE_GRACE)
        self._fifos.remove()


def close_orphan_window(session_id: str, pid: int) -> bool:
    """Close the xterm a dead Main left open for ``session_id``; return whether there was one.

    Only a process running this Session's Window is touched: since that Main died, ``pid`` may
    belong to an unrelated process now.
    """
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:  # gone
        return False
    if b"allin.window" not in args or session_id.encode() not in args:
        return False
    try:
        os.kill(pid, signal.SIGTERM)  # xterm hangs up its Window as it goes
    except ProcessLookupError:
        return False
    # Not Main's child, so it cannot be waited for: watch it go, so it is gone before the new
    # Window opens.
    deadline = time.monotonic() + _ORPHAN_GRACE
    while _running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return True


def _running(pid: int) -> bool:
    """Whether ``pid`` is a live process; a zombie has already exited."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rpartition(")")[2].split()[0] != "Z"  # the state follows the command's name


class WindowOpener:
    """Opens Windows for Main's dispatcher; their events go to ``inbox``."""

    def __init__(self, data_dir: Path, inbox: queue.Queue[MainEvent]) -> None:
        self._data_dir = data_dir
        self._inbox = inbox

    def __call__(self, session_id: str) -> WindowLink:
        try:
            fifos = create_fifo_pair(self._data_dir / "fifo", session_id)
        except OSError as error:
            raise WindowLaunchFailed(session_id=session_id, reason=str(error)) from error
        channel = Channel(
            fifos.from_window,
            fifos.to_window,
            decode_window_message,
            _WindowListener(session_id, self._inbox),
        )
        window_command = [
            sys.executable,
            "-m",
            "allin.window",
            session_id,
            str(fifos.to_window),
            str(fifos.from_window),
        ]
        launcher = Launcher(
            xterm_command(window_command, title=f"AllIn · {session_id}"),
            self._data_dir / "logs" / "main" / f"{session_id}-xterm.log",
            lambda returncode: self._inbox.put(WindowClosed(session_id, returncode)),
        )
        try:
            launcher.start()
        except OSError as error:
            fifos.remove()
            raise WindowLaunchFailed(session_id=session_id, reason=str(error)) from error
        channel.start()
        return _Window(channel, launcher, fifos)
