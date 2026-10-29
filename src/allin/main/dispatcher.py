"""Main's single dispatcher: the only writer of Main's state (ADR-0001)."""

import queue
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from allin.core.errors import (
    AllInError,
    InvalidArguments,
    SessionNotOwned,
    TurnCrashed,
    WindowAlreadyOpen,
)
from allin.core.ids import MainId
from allin.core.settings import MainSettings
from allin.ipc.messages import ChatAppend, ChatDelta, Close, TurnState
from allin.main.conversation import Conversation, Step, Turn
from allin.main.events import (
    ErrorOccurred,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    Shutdown,
    TurnEnded,
    TurnOutput,
    WindowClosed,
    WindowConnected,
)
from allin.main.executors import TurnJob
from allin.main.prompts import Prompt, PromptQueue
from allin.main.sessions import Session, SessionStore
from allin.main.sysprompt import render_sysprompt
from allin.main.turn import OutputKind, TurnRequest


class WindowLink(Protocol):
    """Main's side of an open Window."""

    def send(self, message: BaseModel) -> None: ...

    def close(self) -> None:
        """Release Main's end once the Window is gone or has been sent ``Close``."""
        ...


type OpenWindow = Callable[[str], WindowLink]
type SubmitTurn = Callable[[TurnJob], None]


@dataclass(frozen=True)
class _MainCommand:
    params: tuple[str, ...]
    run: Callable[..., bool]  # takes one str per param; returns whether Main keeps running
    rest: bool = False  # the last param takes the rest of the line verbatim, newlines included


@dataclass(frozen=True)
class _RunningTurn:
    prompt: Prompt
    session: Session


class Dispatcher:
    def __init__(
        self,
        inbox: queue.Queue[MainEvent],
        main_chat: Callable[[str], None],
        sessions: SessionStore,
        open_window: OpenWindow,
        submit_turn: SubmitTurn,
        main_id: MainId,
        settings: MainSettings,
        sysprompts: Path,
    ) -> None:
        self._inbox = inbox
        self._main_chat = main_chat
        self._sessions = sessions
        self._open_window = open_window
        self._submit_turn = submit_turn
        self._main_id = main_id
        self._settings = settings
        self._sysprompts = sysprompts
        self._windows: dict[str, WindowLink] = {}
        self._prompts = PromptQueue()
        self._running: dict[str, _RunningTurn] = {}
        # Command mains are registered once, when Main starts.
        self._commands: dict[str, _MainCommand] = {
            "/new": _MainCommand((), self._new),
            "/open": _MainCommand(("sid",), self._open),
            # The only Command main that queues Prompts; Enter in a Window is /send to its own.
            "/send": _MainCommand(("sid", "text"), self._send, rest=True),
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
                self._send_turn_state(session_id)
            case InputReceived(session_id, text):
                self._run_main_command(f"/send {session_id} {text}")
                self._send_turn_state(session_id)  # unblocks Enter if /send failed
            case TurnOutput(session_id, kind, text):
                self._show_output(session_id, kind, text)
            case TurnEnded(session_id, steps, error):
                self._end_turn(session_id, steps, error)
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
        name = (text.split(maxsplit=1) or [""])[0]
        command = self._commands.get(name)
        if command is None:
            self._main_chat(f"unknown command: {text}")
            return True
        args = _split_rest(text, len(command.params)) if command.rest else text.split()[1:]
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
        if session_id is not None:
            self._to_window(session_id, ChatAppend(role="error", label="error", text=str(error)))

    def _to_window(self, session_id: str, message: BaseModel) -> None:
        link = self._windows.get(session_id)
        if link is not None:
            link.send(message)

    def _new(self) -> bool:
        settings = self._settings.session  # copied: later changes to Main's never reach it
        sysprompt = render_sysprompt(self._sysprompts, settings)
        session = self._sessions.create(self._main_id, settings, sysprompt)
        self._main_chat(f"[{session.id}] session created")
        self._attach_window(session.id)
        return True

    def _open(self, session_id: str) -> bool:
        session = self._owned_session(session_id)
        if session.id in self._windows:
            raise WindowAlreadyOpen(session_id=session.id)
        self._attach_window(session.id)
        return True

    def _send(self, session_id: str, text: str) -> bool:
        session = self._owned_session(session_id)
        self._prompts.push(Prompt(session.id, text))
        label = session.settings.username
        self._to_window(session.id, ChatAppend(role="user", label=label, text=text))
        self._send_turn_state(session.id)
        self._start_turns()
        return True

    def _owned_session(self, session_id: str) -> Session:
        session = self._sessions.load(session_id)
        if session.owner != self._main_id:
            raise SessionNotOwned(session_id=session.id, owner=session.owner)
        return session

    def _attach_window(self, session_id: str) -> None:
        self._windows[session_id] = self._open_window(session_id)
        self._main_chat(f"[{session_id}] opening window")

    def _start_turns(self) -> None:
        """Hand ready Prompts to executors while the server has free slots (ADR-0004)."""
        while len(self._running) < self._settings.parallel:
            prompt = self._prompts.pop_ready(active=self._running)
            if prompt is None:
                return
            try:
                session = self._sessions.load(prompt.session_id)
                conversation = self._conversation(session)
            except AllInError as error:
                self._show_error(error, prompt.session_id)
                self._send_turn_state(prompt.session_id)
                continue
            self._running[session.id] = _RunningTurn(prompt, session)
            request = TurnRequest(
                model=session.settings.model,
                input=conversation.request_input(prompt.text),
                params=session.settings.sampling.request_params(),
            )
            self._main_chat(f"[{session.id}] turn started")
            self._submit_turn(TurnJob(session.id, request))

    def _conversation(self, session: Session) -> Conversation:
        conversation = self._sessions.load_conversation(session)
        if conversation is None:  # a Session from before Conversations were stored
            # Rendered once and kept, like the Sysprompt of a Session made by /new.
            conversation = Conversation(
                sysprompt=render_sysprompt(self._sysprompts, session.settings)
            )
            self._sessions.save_conversation(session, conversation)
        return conversation

    def _show_output(self, session_id: str, kind: OutputKind, text: str) -> None:
        running = self._running.get(session_id)
        if running is None:
            return
        if kind == "reasoning":
            message = ChatDelta(role="reasoning", label="reasoning", text=text)
        else:
            label = running.session.settings.assistant_name
            message = ChatDelta(role="answer", label=label, text=text)
        self._to_window(session_id, message)

    def _end_turn(
        self, session_id: str, steps: list[Step] | None, error: AllInError | None
    ) -> None:
        running = self._running.pop(session_id, None)
        if running is None:
            return
        if error is not None or steps is None:
            self._show_error(error or TurnCrashed(reason="no steps"), session_id)
        else:
            try:
                self._save_turn(running, steps)
            except AllInError as failure:
                self._show_error(failure, session_id)
            else:
                self._main_chat(f"[{session_id}] turn finished")
        self._send_turn_state(session_id)
        self._start_turns()

    def _save_turn(self, running: _RunningTurn, steps: list[Step]) -> None:
        # Saved only now, whole, so a Turn that fails half-way never reaches the disk.
        conversation = self._conversation(running.session)
        turn = Turn(prompt=running.prompt.text, steps=steps)
        self._sessions.save_conversation(running.session, conversation.with_turn(turn))

    def _send_turn_state(self, session_id: str) -> None:
        active = session_id in self._running or self._prompts.has(session_id)
        self._to_window(session_id, TurnState(active=active))


def _split_rest(text: str, params: int) -> list[str]:
    """The arguments of a command whose last param takes the rest of the line.

    Words are separated by any whitespace; the rest starts after a single space or tab and is
    kept as typed (leading spaces and newlines matter in a Prompt). A blank rest is missing.
    """
    words = r"\s*\S+" + r"\s+(\S+)" * (params - 1)
    match = re.fullmatch(words + r"(?:[ \t](.*))?", text, re.DOTALL)
    if match is None:
        return []
    *args, rest = match.groups()
    return [*args, rest] if rest and rest.strip() else args
