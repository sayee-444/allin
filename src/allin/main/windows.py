"""Opening a Session's Window: its FIFO pair, the Channel over it and the Launcher (ADR-0003)."""

import os
import queue
import signal
import time
from pathlib import Path

from pydantic import BaseModel

from allin.core.errors import AllInError, ChannelBroken, WindowLaunchFailed
from allin.core.ids import SessionId
from allin.core.themes import Theme
from allin.ipc.channel import Channel, FifoPair, create_fifo_pair
from allin.ipc.messages import (
    BoxAnswer,
    InputDraft,
    OrganizerAct,
    OrganizerReady,
    OrganizerToMain,
    UserInput,
    WindowReady,
    WindowToMain,
    WithdrawQueued,
    decode_organizer_message,
    decode_window_message,
)
from allin.ipc.messages import WindowUsed as WindowUsedMessage
from allin.main.children import python_module
from allin.main.events import (
    BoxAnswered,
    DraftReceived,
    ErrorOccurred,
    InputReceived,
    MainEvent,
    OrganizerActed,
    OrganizerClosed,
    OrganizerConnected,
    QueuedWithdrawn,
    WindowClosed,
    WindowConnected,
    WindowUsed,
)
from allin.main.launcher import Launcher, xterm_command
from allin.main.open_windows import WindowLink
from allin.ui.look import xterm_options

_CLOSE_GRACE = 2.0
_ORPHAN_GRACE = 2.0


class _WindowListener:
    """Turns what a Window sends into events for the dispatcher."""

    def __init__(self, session_id: SessionId, inbox: queue.Queue[MainEvent]) -> None:
        self._session_id = session_id
        self._inbox = inbox

    def on_message(self, message: WindowToMain) -> None:
        match message:
            case WindowReady():
                self._inbox.put(WindowConnected(self._session_id))
            case UserInput(text=text, interrupt=interrupt):
                self._inbox.put(InputReceived(self._session_id, text, interrupt))
            case WithdrawQueued():
                self._inbox.put(QueuedWithdrawn(self._session_id))
            case InputDraft(blocks=blocks):
                self._inbox.put(DraftReceived(self._session_id, tuple(blocks)))
            case WindowUsedMessage():
                self._inbox.put(WindowUsed(self._session_id))
            case BoxAnswer(box_id=box_id, answer=answer):
                self._inbox.put(BoxAnswered(box_id, answer, window=self._session_id))

    def on_error(self, error: AllInError) -> None:
        self._inbox.put(ErrorOccurred(error, self._session_id))

    def on_eof(self) -> None:
        pass  # the Launcher's watcher reports the closed Window


class _Window[In]:
    """Main's side of one open Window (a ``WindowLink``)."""

    def __init__(
        self,
        channel: Channel[In],
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


def close_orphan_window(session_id: SessionId, pid: int) -> bool:
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

    def _record_path(self, session_id: SessionId) -> Path:
        """Where the Window keeps what it could not tell Main (#33)."""
        folder = self._data_dir / "logs" / "windows"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{session_id}.jsonl"

    def __call__(self, session_id: SessionId, theme: Theme) -> WindowLink:
        try:
            fifos = create_fifo_pair(self._data_dir / "fifo", session_id)
            record = self._record_path(session_id)
        except OSError as error:
            raise WindowLaunchFailed(session_id=session_id, reason=str(error)) from error
        channel = Channel(
            fifos.from_window,
            fifos.to_window,
            decode_window_message,
            _WindowListener(session_id, self._inbox),
        )
        window_command = python_module(
            "allin.window", session_id, str(fifos.to_window), str(fifos.from_window), str(record)
        )
        launcher = Launcher(
            xterm_command(window_command, f"AllIn · {session_id}", xterm_options(theme)),
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


class OrganizerListener:
    """Turns what the Organizer sends into events for the dispatcher (#53)."""

    def __init__(self, inbox: queue.Queue[MainEvent]) -> None:
        self._inbox = inbox

    def on_message(self, message: OrganizerToMain) -> None:
        match message:
            case OrganizerReady():
                self._inbox.put(OrganizerConnected())
            case OrganizerAct(action=action, session_id=session_id):
                self._inbox.put(OrganizerActed(action, session_id))

    def on_error(self, error: AllInError) -> None:
        if isinstance(error, ChannelBroken):
            return  # it closed while Main wrote to it: its watcher reports it, nothing is lost
        self._inbox.put(ErrorOccurred(error))

    def on_eof(self) -> None:
        pass  # the Launcher's watcher reports it closed


_ORGANIZER = "organizer"  # its FIFOs and its xterm's log; never a Session's id


class OrganizerOpener:
    """Opens the Organizer in its own xterm, with its own FIFO pair (#53)."""

    def __init__(self, data_dir: Path, inbox: queue.Queue[MainEvent]) -> None:
        self._data_dir = data_dir
        self._inbox = inbox

    def __call__(self, theme: Theme) -> WindowLink:
        try:
            fifos = create_fifo_pair(self._data_dir / "fifo", _ORGANIZER)
        except OSError as error:
            raise WindowLaunchFailed(session_id=_ORGANIZER, reason=str(error)) from error
        channel = Channel(
            fifos.from_window,
            fifos.to_window,
            decode_organizer_message,
            OrganizerListener(self._inbox),
        )
        command = python_module("allin.organizer", str(fifos.to_window), str(fifos.from_window))

        def exited(_: int) -> None:
            self._inbox.put(OrganizerClosed(launcher.pid))

        launcher = Launcher(
            xterm_command(command, "AllIn · Organizer", xterm_options(theme)),
            self._data_dir / "logs" / "main" / f"{_ORGANIZER}-xterm.log",
            exited,
        )
        try:
            launcher.start()
        except OSError as error:
            fifos.remove()
            raise WindowLaunchFailed(session_id=_ORGANIZER, reason=str(error)) from error
        channel.start()
        return _Window(channel, launcher, fifos)
