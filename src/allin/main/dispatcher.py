"""Main's single dispatcher: the only writer of Main's state (ADR-0001)."""

import queue
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel

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


class WindowLink(Protocol):
    """Main's side of an open Window."""

    def send(self, message: BaseModel) -> None: ...


class Dispatcher:
    def __init__(self, inbox: queue.Queue[MainEvent], main_chat: Callable[[str], None]) -> None:
        self._inbox = inbox
        self._main_chat = main_chat
        self._windows: dict[str, WindowLink] = {}

    def register(self, session_id: str, link: WindowLink) -> None:
        self._windows[session_id] = link

    def run(self) -> None:
        """Handle events until a Shutdown event or the /exit command."""
        while self._handle(self._inbox.get()):
            pass
        for link in self._windows.values():
            link.send(Close())

    def _handle(self, event: MainEvent) -> bool:
        match event:
            case WindowConnected(session_id):
                self._main_chat(f"[{session_id}] window connected")
            case InputReceived(session_id, text):
                link = self._windows.get(session_id)
                if link is not None:
                    link.send(ChatAppend(role="echo", text=text))
            case WindowClosed(session_id, returncode):
                self._windows.pop(session_id, None)
                self._main_chat(f"[{session_id}] window closed (exit code {returncode})")
            case ErrorOccurred(error, session_id):
                prefix = f"[{session_id}] " if session_id else ""
                self._main_chat(f"{prefix}error {error}")
            case MainCommandEntered(text):
                return self._run_main_command(text)
            case Shutdown():
                return False
        return True

    def _run_main_command(self, text: str) -> bool:
        if text.strip() == "/exit":
            return False
        self._main_chat(f"unknown command: {text}")
        return True
