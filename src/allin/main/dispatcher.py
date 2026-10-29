"""Main's single dispatcher: the only writer of Main's state (ADR-0001)."""

import queue
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from allin.core.errors import AllInError, InvalidArguments, SessionNotOwned, WindowAlreadyOpen
from allin.core.ids import MainId
from allin.ipc.messages import ChatAppend, Close
from allin.main.events import (
    ErrorOccurred,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    Shutdown,
    WindowClosed,
    WindowConnected,
)
from allin.main.sessions import SessionStore


class WindowLink(Protocol):
    """Main's side of an open Window."""

    def send(self, message: BaseModel) -> None: ...

    def close(self) -> None:
        """Release Main's end once the Window is gone or has been sent ``Close``."""
        ...


type OpenWindow = Callable[[str], WindowLink]


@dataclass(frozen=True)
class _MainCommand:
    params: tuple[str, ...]
    run: Callable[..., bool]  # takes one str per param; returns whether Main keeps running


class Dispatcher:
    def __init__(
        self,
        inbox: queue.Queue[MainEvent],
        main_chat: Callable[[str], None],
        sessions: SessionStore,
        open_window: OpenWindow,
        main_id: MainId,
    ) -> None:
        self._inbox = inbox
        self._main_chat = main_chat
        self._sessions = sessions
        self._open_window = open_window
        self._main_id = main_id
        self._windows: dict[str, WindowLink] = {}
        # Command mains are registered once, when Main starts.
        self._commands: dict[str, _MainCommand] = {
            "/new": _MainCommand((), self._new),
            "/open": _MainCommand(("sid",), self._open),
            "/exit": _MainCommand((), lambda: False),
        }

    def run(self) -> None:
        """Handle events until a Shutdown event or the /exit command."""
        while self._handle(self._inbox.get()):
            pass
        for link in self._windows.values():
            link.send(Close())
        for link in self._windows.values():
            link.close()

    def _handle(self, event: MainEvent) -> bool:
        match event:
            case WindowConnected(session_id):
                self._main_chat(f"[{session_id}] window connected")
            case InputReceived(session_id, text):
                link = self._windows.get(session_id)
                if link is not None:
                    link.send(ChatAppend(role="echo", text=text))
            case WindowClosed(session_id, returncode):
                link = self._windows.pop(session_id, None)
                if link is not None:
                    link.close()
                self._main_chat(f"[{session_id}] window closed (exit code {returncode})")
            case ErrorOccurred(error, session_id):
                self._show_error(error, session_id)
            case MainCommandEntered(text):
                return self._run_main_command(text)
            case Shutdown():
                return False
        return True

    def _run_main_command(self, text: str) -> bool:
        name, *args = text.split() or [""]
        command = self._commands.get(name)
        if command is None:
            self._main_chat(f"unknown command: {text}")
            return True
        try:
            if len(args) != len(command.params):
                usage = " ".join([name, *(f"<{param}>" for param in command.params)])
                raise InvalidArguments(usage=usage)
            return command.run(*args)
        except AllInError as error:
            self._show_error(error)
            return True

    def _show_error(self, error: AllInError, session_id: str | None = None) -> None:
        prefix = f"[{session_id}] " if session_id else ""
        self._main_chat(f"{prefix}error {error}")

    def _new(self) -> bool:
        session = self._sessions.create(owner=self._main_id)
        self._main_chat(f"[{session.id}] session created")
        self._attach_window(session.id)
        return True

    def _open(self, session_id: str) -> bool:
        session = self._sessions.load(session_id)
        if session.owner != self._main_id:
            raise SessionNotOwned(session_id=session.id, owner=session.owner)
        if session.id in self._windows:
            raise WindowAlreadyOpen(session_id=session.id)
        self._attach_window(session.id)
        return True

    def _attach_window(self, session_id: str) -> None:
        self._windows[session_id] = self._open_window(session_id)
        self._main_chat(f"[{session_id}] opening window")
