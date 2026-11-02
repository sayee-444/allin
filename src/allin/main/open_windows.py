"""The Windows Main has open, what it sends them and the Draft each one last sent."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, JsonValue

from allin.core.drafts import put_back
from allin.core.errors import AllInError
from allin.core.ids import ServerKey, SessionId
from allin.ipc.messages import (
    ChatDelta,
    Close,
    ContextUsage,
    InputRestore,
    PoolShared,
    PromptHistory,
)
from allin.main.chat_rebuild import error_entry, prompt_history, rebuild_chat, returned_prompts
from allin.main.conversation import Conversation, Step
from allin.main.executors import SHUTTING_DOWN
from allin.main.links import LinkResolver
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


type OpenWindow = Callable[[SessionId], WindowLink]


@dataclass
class Streamed:
    """What a running Turn has given so far, shown again if its Window is reopened mid-Turn."""

    reasoning: str = ""
    answer: str = ""
    steps: list[Step] = field(default_factory=list[Step])  # ended so far


class OpenWindows:
    def __init__(
        self,
        open_window: OpenWindow,
        reporter: Reporter,
        sessions: SessionStore,
        links: LinkResolver,
    ) -> None:
        self._open_window = open_window
        self._link_resolver = links
        self._reporter = reporter
        self._sessions = sessions
        self._links: dict[SessionId, WindowLink] = {}
        # Each Session's Draft, shown block first, as its Window last sent it.
        self._drafts: dict[SessionId, list[str]] = {}
        # Each Server's slot context, once ready: the limit its Sessions' Windows show.
        self._limits: dict[ServerKey, int] = {}
        # With a shared pool: the Sessions whose Steps are in course on each Server, and
        # whether each open Window was last told another one uses its pool.
        self._in_course: dict[ServerKey, frozenset[SessionId]] = {}
        self._shared: dict[SessionId, bool] = {}
        # Windows Main closed itself whose xterm has not said it is gone yet, by Session.
        self._closing: dict[SessionId, int] = {}

    def open(self, session: Session, streamed: Streamed | None) -> None:
        """Open the Session's Window and show it its Chat so far and its Draft.

        ``streamed`` is what its running Turn has given so far, if it has one.
        """
        self._links[session.id] = self._open_window(session.id)
        self._reporter.record(session.id, "window_opened", "opening window")
        try:
            self._send_chat(session, streamed)
        except AllInError as error:
            self.show_error(error, session.id)
        self._shared.pop(session.id, None)
        server = self._server_of(session)
        if server is not None and server in self._in_course:
            self._tell_shared(session.id, server)
        # Last block first: each one shown pushes the one before it back to wait.
        for block in reversed(self._drafts.get(session.id, [])):
            self.send(session.id, InputRestore(text=block))
        self._give_back_failed(session)

    def _give_back_failed(self, session: Session) -> None:
        """What a Turn that failed while the Window was closed left out of every input goes
        back to it, ahead of the Draft (#15)."""
        try:
            texts = returned_prompts(self._sessions.read_log(session))
        except AllInError:
            return  # its Log is unreadable: _send_chat told it already
        if not texts:
            return
        for text in reversed(texts):
            self.put_back(session.id, text)
        fields: list[JsonValue] = list(texts)
        self._reporter.log_session(session.id, "prompt_returned", texts=fields)

    def is_open(self, session_id: SessionId) -> bool:
        return session_id in self._links

    def send(self, session_id: SessionId, message: BaseModel) -> None:
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
            limit=self._limit(session),
            tokens_per_second=tokens_per_second(steps),
        )
        self.send(session.id, usage)

    def set_context_limit(
        self, server: ServerKey, limit: int, streamed: Mapping[SessionId, Streamed]
    ) -> None:
        """Show every open Window of a Session on ``server`` its Context against the Server's
        new limit.

        ``streamed`` is what each running Turn has given so far, by Session.
        """
        self._limits[server] = limit
        for session_id in list(self._links):
            try:
                session = self._sessions.load(session_id)
                if self._server_of(session) != server:
                    continue
                conversation = self._sessions.load_conversation(session)
            except AllInError as error:
                self.show_error(error, session_id)
                continue
            conversation = conversation or Conversation(sysprompt="")
            self.show_context(session, _last_steps(streamed.get(session_id), conversation))

    def set_in_course(self, server: ServerKey, sessions: frozenset[SessionId]) -> None:
        """The Sessions whose Steps are in course on ``server``'s shared pool, whatever Main
        runs them: each open Window on it learns whether another one uses its pool now."""
        if self._in_course.get(server) == sessions:
            return
        self._in_course[server] = sessions
        for session_id in list(self._links):
            try:
                session = self._sessions.load(session_id)
            except AllInError:
                continue
            if self._server_of(session) == server:
                self._tell_shared(session_id, server)

    def _tell_shared(self, session_id: SessionId, server: ServerKey) -> None:
        shared = bool(self._in_course.get(server, frozenset()) - {session_id})
        if self._shared.get(session_id) != shared:
            self._shared[session_id] = shared
            self.send(session_id, PoolShared(shared=shared))

    def _server_of(self, session: Session) -> ServerKey | None:
        try:
            return self._link_resolver.resolve(session.settings).key
        except AllInError:  # its Prompt tells it when it would start
            return None

    def _limit(self, session: Session) -> int | None:
        server = self._server_of(session)
        return None if server is None else self._limits.get(server)

    def show_error(self, error: AllInError, session_id: SessionId | None = None) -> None:
        """Tell Main's Chat and Logs, and the Session's Window if it concerns one."""
        self._reporter.error(error, session_id)
        if session_id is not None:
            self.send(session_id, error_entry(str(error)))

    def closed(self, session_id: SessionId, returncode: int | None) -> bool:
        """The Window is gone: release Main's end of it. ``False`` if it was one Main had
        closed itself (``close``), which is told already and changes nothing now."""
        if self._closing.get(session_id, 0) > 0:
            self._closing[session_id] -= 1
            return False
        link = self._links.pop(session_id, None)
        if link is not None:
            link.close()
        text = f"window closed (exit code {returncode})"
        self._reporter.record(session_id, "window_closed", text, returncode=returncode)
        return True

    def close(self, session_id: SessionId, reason: str) -> None:
        """Ask a Window to close and release it, keeping its Draft: its Session goes on."""
        link = self._links.pop(session_id, None)
        if link is None:
            return
        link.send(Close())
        link.close()
        self._closing[session_id] = self._closing.get(session_id, 0) + 1
        self._shared.pop(session_id, None)
        shown = f"window closed: {reason}"
        self._reporter.record(session_id, "window_closed", shown, returncode=None, reason=reason)

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

    def pids(self) -> dict[SessionId, int]:
        """Each open Window's xterm pid, in the order they opened."""
        return {session_id: link.pid for session_id, link in self._links.items()}

    def keep_draft(self, session_id: SessionId, blocks: Sequence[str]) -> None:
        """What a Session's Window holds in its input now, shown block first."""
        self._drafts[session_id] = list(blocks)

    def put_back(self, session_id: SessionId, text: str) -> None:
        """A Prompt that did not run goes back to its input, ahead of the Draft."""
        self._drafts[session_id] = put_back(self._drafts.get(session_id, []), text)
        self.send(session_id, InputRestore(text=text))

    def replace_drafts(self, drafts: Mapping[SessionId, Sequence[str]]) -> None:
        """Every Draft becomes the one in ``drafts``. An open Window keeps what its input
        holds, and the blocks of ``drafts`` it lacks come back ahead of them, so nothing typed
        is lost."""
        replaced = {sid: list(draft) for sid, draft in drafts.items() if sid not in self._links}
        for session_id in self._links:
            blocks = self._drafts.get(session_id, [])
            missing = [block for block in drafts.get(session_id, []) if block not in blocks]
            for block in reversed(missing):  # each one shown pushes the one before it back
                blocks = put_back(blocks, block)
                self.send(session_id, InputRestore(text=block))
            if blocks:
                replaced[session_id] = blocks
        self._drafts = replaced

    def clear_draft(self, session_id: SessionId) -> None:
        """The Window sent its input, which is empty now."""
        self._drafts.pop(session_id, None)

    def drafts(self) -> dict[SessionId, list[str]]:
        """Every Session's Draft that is not empty."""
        return {session_id: draft for session_id, draft in self._drafts.items() if draft}

    def _send_chat(self, session: Session, streamed: Streamed | None) -> None:
        """Show a Window the Session's Chat so far, rebuilt from its Conversation and Log."""
        conversation = self._sessions.load_conversation(session) or Conversation(sysprompt="")
        log, archived = self._sessions.read_log(session), self._sessions.load_archived(session)
        for message in rebuild_chat(conversation, log, session.settings, archived):
            self.send(session.id, message)
        history = prompt_history(log)
        if history:  # what Up brings back to its input (#22)
            self.send(session.id, PromptHistory(prompts=history))
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
