"""The Windows Main has open, what it sends them and the Draft each one last sent."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from allin.core.errors import AllInError
from allin.ipc.messages import ChatDelta, Close, ContextUsage, InputRestore
from allin.main.chat_rebuild import error_entry, rebuild_chat
from allin.main.conversation import Conversation, Step
from allin.main.executors import SHUTTING_DOWN
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.turn import OutputKind
from allin.main.usage import context_tokens, tokens_per_second


class WindowLink(Protocol):
    """Main's side of an open Window."""

    @property
    def pid(self) -> int:
        """Its xterm's, kept in MainState to close it if Main dies and leaves it open."""
        ...

    def send(self, message: BaseModel) -> None: ...

    def close(self) -> None:
        """Release Main's end once the Window is gone or has been sent ``Close``."""
        ...


type OpenWindow = Callable[[str], WindowLink]


@dataclass
class Streamed:
    """What a running Turn has given so far, shown again if its Window is reopened mid-Turn."""

    reasoning: str = ""
    answer: str = ""
    steps: list[Step] = field(default_factory=list[Step])  # ended so far


class OpenWindows:
    def __init__(self, open_window: OpenWindow, reporter: Reporter, sessions: SessionStore) -> None:
        self._open_window = open_window
        self._reporter = reporter
        self._sessions = sessions
        self._links: dict[str, WindowLink] = {}
        # Each Session's Draft, shown block first, as its Window last sent it.
        self._drafts: dict[str, list[str]] = {}
        self._limit: int | None = None  # each slot's context, once the Server is ready

    def attach(self, session: Session, streamed: Streamed | None) -> None:
        """Open the Session's Window and show it its Chat so far and its Draft.

        ``streamed`` is what its running Turn has given so far, if it has one.
        """
        self._links[session.id] = self._open_window(session.id)
        self._reporter.record(session.id, "window_opened", "opening window")
        try:
            self._send_chat(session, streamed)
        except AllInError as error:
            self.show_error(error, session.id)
        # Last block first: each one shown pushes the one before it back to wait.
        for block in reversed(self._drafts.get(session.id, [])):
            self.send(session.id, InputRestore(text=block))

    def is_open(self, session_id: str) -> bool:
        return session_id in self._links

    def send(self, session_id: str, message: BaseModel) -> None:
        """Send a Session's Window a message; nothing if it has none."""
        link = self._links.get(session_id)
        if link is not None:
            link.send(message)

    def stream(self, session: Session, kind: OutputKind, text: str) -> None:
        """Show what a running Turn streams."""
        self.send(session.id, _delta(session, kind, text))

    def show_context(self, session: Session, steps: list[Step]) -> None:
        """Show the Window the Context ``steps`` leave and how fast the last one generated."""
        usage = ContextUsage(
            context=context_tokens(steps),
            limit=self._limit,
            tokens_per_second=tokens_per_second(steps),
        )
        self.send(session.id, usage)

    def set_context_limit(self, limit: int, streamed: Mapping[str, Streamed]) -> None:
        """Show every open Window its Context against the Server's new limit.

        ``streamed`` is what each running Turn has given so far, by Session.
        """
        self._limit = limit
        for session_id in list(self._links):
            try:
                session = self._sessions.load(session_id)
                conversation = self._sessions.load_conversation(session)
            except AllInError as error:
                self.show_error(error, session_id)
                continue
            conversation = conversation or Conversation(sysprompt="")
            self.show_context(session, _last_steps(streamed.get(session_id), conversation))

    def show_error(self, error: AllInError, session_id: str | None = None) -> None:
        """Tell Main's Chat and Logs, and the Session's Window if it concerns one."""
        self._reporter.error(error, session_id)
        if session_id is not None:
            self.send(session_id, error_entry(str(error)))

    def closed(self, session_id: str, returncode: int | None) -> None:
        """The Window is gone: release Main's end of it."""
        link = self._links.pop(session_id, None)
        if link is not None:
            link.close()
        text = f"window closed (exit code {returncode})"
        self._reporter.record(session_id, "window_closed", text, returncode=returncode)

    def close_all(self) -> None:
        """On shutdown: ask every Window to close, then release them."""
        for link in self._links.values():
            link.send(Close())
        for session_id, link in self._links.items():
            link.close()
            shown = f"window closed: {SHUTTING_DOWN}"
            self._reporter.record(
                session_id, "window_closed", shown, returncode=None, reason=SHUTTING_DOWN
            )
        self._links.clear()

    def pids(self) -> dict[str, int]:
        """Each open Window's xterm pid, in the order they opened."""
        return {session_id: link.pid for session_id, link in self._links.items()}

    def draft(self, session_id: str, blocks: Sequence[str]) -> None:
        """What a Session's Window holds in its input now, shown block first."""
        self._drafts[session_id] = list(blocks)

    def clear_draft(self, session_id: str) -> None:
        """The Window sent its input, which is empty now."""
        self._drafts.pop(session_id, None)

    def drafts(self) -> dict[str, list[str]]:
        """Every Session's Draft that is not empty."""
        return {session_id: draft for session_id, draft in self._drafts.items() if draft}

    def _send_chat(self, session: Session, streamed: Streamed | None) -> None:
        """Show a Window the Session's Chat so far, rebuilt from its Conversation and Log."""
        conversation = self._sessions.load_conversation(session) or Conversation(sysprompt="")
        for message in rebuild_chat(
            conversation, self._sessions.read_log(session), session.settings
        ):
            self.send(session.id, message)
        self.show_context(session, _last_steps(streamed, conversation))
        if streamed is not None:  # what the running Turn has streamed so far; it goes on
            if streamed.reasoning:
                self.send(session.id, _delta(session, "reasoning", streamed.reasoning))
            if streamed.answer:
                self.send(session.id, _delta(session, "answer", streamed.answer))


def _last_steps(streamed: Streamed | None, conversation: Conversation) -> list[Step]:
    """The Steps whose Context the Window shows: the running Turn's, else the last Turn's."""
    if streamed is not None and streamed.steps:
        return streamed.steps
    return conversation.turns[-1].steps if conversation.turns else []


def _delta(session: Session, kind: OutputKind, text: str) -> ChatDelta:
    if kind == "reasoning":
        return ChatDelta(role="reasoning", label="reasoning", text=text)
    return ChatDelta(role="answer", label=session.settings.assistant_name, text=text)
