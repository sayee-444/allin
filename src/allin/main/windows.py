"""Opening a Session's Window: its FIFO pair, the Channel over it and the Launcher (ADR-0003)."""

import queue
import sys
from pathlib import Path

from pydantic import BaseModel

from allin.core.errors import AllInError, WindowLaunchFailed
from allin.ipc.channel import Channel, FifoPair, create_fifo_pair
from allin.ipc.messages import UserInput, WindowReady, decode_window_message
from allin.main.dispatcher import WindowLink
from allin.main.events import ErrorOccurred, InputReceived, MainEvent, WindowClosed, WindowConnected
from allin.main.launcher import Launcher, xterm_command

_CLOSE_GRACE = 2.0


class _WindowListener:
    """Turns what a Window sends into events for the dispatcher."""

    def __init__(self, session_id: str, inbox: queue.Queue[MainEvent]) -> None:
        self._session_id = session_id
        self._inbox = inbox

    def on_message(self, message: WindowReady | UserInput) -> None:
        match message:
            case WindowReady():
                self._inbox.put(WindowConnected(self._session_id))
            case UserInput(text=text):
                self._inbox.put(InputReceived(self._session_id, text))

    def on_error(self, error: AllInError) -> None:
        self._inbox.put(ErrorOccurred(error, self._session_id))

    def on_eof(self) -> None:
        pass  # the Launcher's watcher reports the closed Window


class _Window:
    """Main's side of one open Window (a ``WindowLink``)."""

    def __init__(
        self,
        channel: Channel[WindowReady | UserInput],
        launcher: Launcher,
        fifos: FifoPair,
    ) -> None:
        self._channel = channel
        self._launcher = launcher
        self._fifos = fifos

    def send(self, message: BaseModel) -> None:
        self._channel.send(message)

    def close(self) -> None:
        self._channel.close()
        if not self._launcher.wait(_CLOSE_GRACE):
            self._launcher.terminate(_CLOSE_GRACE)
        self._fifos.remove()


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
