"""The Windows Main has open, what it sends them and the Draft each one last sent."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from pydantic import BaseModel, JsonValue

from allin.core.drafts import put_back
from allin.core.errors import AllInError
from allin.core.ids import ServerKey, SessionId
from allin.core.settings import WINDOWS_MAX_DEFAULT
from allin.core.themes import BUILTIN, Theme
from allin.ipc.messages import (
    ChatClear,
    ChatDelta,
    Close,
    ContextUsage,
    InputRestore,
    KeyTable,
    PoolShared,
    PromptHistory,
    ThemeSet,
)
from allin.main.chat_rebuild import error_entry, prompt_history, rebuild_chat, returned_prompts
from allin.main.conversation import Conversation, Step
from allin.main.executors import SHUTTING_DOWN
from allin.main.links import LinkResolver
from allin.main.prompt_commands import is_secret_command
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.themes import ThemeKeeper
from allin.main.turn import OutputKind
from allin.main.usage import context_tokens, tokens_per_second
from allin.main.window_room import displaced, fits
from allin.ui.shortcuts import effective_keys


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


# Opens a Session's Window, its xterm born with the letter, size and colours of its Theme.
type OpenWindow = Callable[[SessionId, Theme], WindowLink]

IN_BACKGROUND = "sent to the background"


@dataclass
class Streamed:
    """What a running Turn has given so far, shown again if its Window is reopened mid-Turn."""

    reasoning: str = ""
    answer: str = ""
    steps: list[Step] = field(default_factory=list[Step])  # ended so far


@dataclass(frozen=True)
class _Compacted:
    """The Conversation a Compactación saved and its Context, as the Server measured it;
    ``None`` until it does, or if it cannot (#157)."""

    conversation: Conversation
    context: int | None = None


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
        self._themes: ThemeKeeper | None = None
        # Sessions open without their Window in view (#52): they go on as with one.
        self._background: dict[SessionId, None] = {}
        # When the human last used each open Session (wrote in it, clicked it, opened it): a
        # count, higher is later, for the Máximo de Ventanas to send the least used away.
        self._used: dict[SessionId, int] = {}
        self._uses = 0
        self._maximum = WINDOWS_MAX_DEFAULT
        self._asking: Callable[[SessionId], bool] = lambda _: False
        # What each Session's last Compactación left, until a Turn's Step shows another
        # Context: what its border shows, rather than the Context of before (#157).
        self._compacted: dict[SessionId, _Compacted] = {}

    def use_themes(self, themes: ThemeKeeper) -> None:
        """What resolves each Window's Theme (#47); without it, Windows show the built-in."""
        self._themes = themes

    def open_ids(self) -> list[SessionId]:
        return list(self._links)

    def watch_asking(self, asking: Callable[[SessionId], bool]) -> None:
        """Whether a Session waits for a Confirmation: it leaves the view last."""
        self._asking = asking

    @property
    def maximum(self) -> int:
        return self._maximum

    def set_maximum(self, maximum: int) -> None:
        """``windows.max``; when it goes down, those over it go to the background at once,
        least used first."""
        self._maximum = maximum
        for session_id in self._displaced(coming=0):
            self.to_background(session_id)

    def touch(self, session_id: SessionId) -> None:
        """The human used the Session's Window: it leaves the view after the others."""
        if session_id in self._links or session_id in self._background:
            self._uses += 1
            self._used[session_id] = self._uses

    def show(self, session: Session, streamed: Streamed | None, human: bool = True) -> bool:
        """Open the Session's Window under the Máximo de Ventanas; whether it is in view.

        What a human opens always shows, sending the least used Session to the background if
        there is no room. What anyone else opens never displaces another: without room, it
        stays in the background (ADR-0020).
        """
        if not human and not fits(len(self._links), self._maximum):
            self.keep_in_background(session.id)
            return False
        for session_id in self._displaced(coming=1):
            self.to_background(session_id)
        self.open(session, streamed)
        return True

    def _displaced(self, coming: int) -> list[SessionId]:
        asking = {session_id for session_id in self._links if self._asking(session_id)}
        return displaced(list(self._links), self._used, asking, self._maximum, coming)

    def to_background(self, session_id: SessionId) -> None:
        """Close the Session's xterm and keep it open in the background: nothing is
        cancelled, its Draft is kept, and its Notices and Confirmations wait in Main's Chat."""
        if session_id in self._links:
            self.close(session_id, IN_BACKGROUND)
        self.keep_in_background(session_id)

    def keep_in_background(self, session_id: SessionId) -> None:
        """The Session is open in the background, with no xterm to close."""
        if session_id in self._background:
            return
        self._background[session_id] = None
        self.touch(session_id)
        self._reporter.record(session_id, "window_background", "in the background")

    def is_background(self, session_id: SessionId) -> bool:
        return session_id in self._background

    def background_ids(self) -> list[SessionId]:
        """The Sessions in the background, in the order they went there."""
        return list(self._background)

    def forget(self, session_id: SessionId) -> None:
        """The Session is no longer open in the background (``/close``, a Profile left)."""
        self._background.pop(session_id, None)
        self._used.pop(session_id, None)

    def open(self, session: Session, streamed: Streamed | None) -> None:
        """Open the Session's Window and show it its Chat so far and its Draft.

        ``streamed`` is what its running Turn has given so far, if it has one.
        """
        self._background.pop(session.id, None)
        theme = BUILTIN if self._themes is None else self._themes.opening(session)
        self._links[session.id] = self._open_window(session.id, theme)
        self.touch(session.id)
        self._reporter.record(session.id, "window_opened", "opening window")
        self.send(session.id, ThemeSet(theme=theme))  # before its Chat (#47)
        if self._themes is not None:
            self._themes.opened(session.id, theme)
        if session.settings.keys:  # its Atajos, where they differ from the defaults (#57)
            self.send(session.id, KeyTable(bindings=effective_keys(session.settings.keys)))
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
        """Show the Window the Context a Turn's ``steps`` leave and how fast the last one
        generated; what a Compactación measured before them no longer holds."""
        self._compacted.pop(session.id, None)
        self._send_usage(session, context_tokens(steps), steps)

    def show_saved(self, session: Session, conversation: Conversation) -> None:
        """Show the Window the Context its saved ``conversation`` leaves (``saved_context``)."""
        compacted = self._compacted.get(session.id)
        if compacted is not None and compacted.conversation != conversation:
            # It changed since, by a Rewind say: a measure still to come no longer holds.
            del self._compacted[session.id]
        context = self.saved_context(session.id, conversation)
        self._send_usage(session, context, _last_turn_steps(conversation))

    def saved_context(self, session_id: SessionId, conversation: Conversation) -> int | None:
        """The Context its saved ``conversation`` leaves: what the Server measured of it, if
        a Compactación saved it after its last Turn; unknown if it could not, or if this Main
        did not see it measured; else its last Turn's."""
        compacted = self._compacted.get(session_id)
        if compacted is not None and compacted.conversation == conversation:
            return compacted.context
        if conversation.compacted_since_turn:
            return None
        return context_tokens(_last_turn_steps(conversation))

    def compacted(self, session: Session, conversation: Conversation) -> None:
        """A Compactación saved ``conversation``: its Context is unknown until the Server
        measures it, never the one before (#157)."""
        self._compacted[session.id] = _Compacted(conversation)
        self._send_usage(session, None, _last_turn_steps(conversation))

    def compacted_measured(self, session: Session, context: int | None) -> None:
        """What the Server measured of the Conversation the last Compactación saved; nothing
        if a Turn's Step showed another Context since. ``None``: it could not measure."""
        compacted = self._compacted.get(session.id)
        if compacted is None:
            return
        self._compacted[session.id] = replace(compacted, context=context)
        self._send_usage(session, context, _last_turn_steps(compacted.conversation))

    def _show_current(
        self, session: Session, streamed: Streamed | None, conversation: Conversation
    ) -> None:
        """The running Turn's Context, if a Step of it ended; else the saved one's."""
        if streamed is not None and streamed.steps:
            self.show_context(session, streamed.steps)
        else:
            self.show_saved(session, conversation)

    def _send_usage(self, session: Session, context: int | None, steps: list[Step]) -> None:
        usage = ContextUsage(
            context=context,
            limit=self.context_limit(session),
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
            self._show_current(session, streamed.get(session_id), conversation)

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

    def context_limit(self, session: Session) -> int | None:
        """The slot of the Session's Server, once Main knows it."""
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
        self._forget_theme(session_id)
        self._used.pop(session_id, None)
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
        self._forget_theme(session_id)
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

    def _forget_theme(self, session_id: SessionId) -> None:
        if self._themes is not None:
            self._themes.closed(session_id)

    def pids(self) -> dict[SessionId, int]:
        """Each open Window's xterm pid, in the order they opened."""
        return {session_id: link.pid for session_id, link in self._links.items()}

    def keep_draft(self, session_id: SessionId, blocks: Sequence[str]) -> None:
        """What a Session's Window holds in its input now, shown block first; never an
        ``@secret …``, which MainState would keep on disk with a value typed in it (#155)."""
        self._drafts[session_id] = [block for block in blocks if not is_secret_command(block)]

    def rename(self, old: SessionId, new: SessionId) -> None:
        """A Session took a new id (#59): its Draft follows it. Its Window, if it had one, was
        closed already, and opens again under the new id."""
        if old in self._drafts:
            self._drafts[new] = self._drafts.pop(old)
        if old in self._background:
            self._background = {new if sid == old else sid: None for sid in self._background}
        if old in self._used:
            self._used[new] = self._used.pop(old)

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

    def rebuild_chat(self, session: Session) -> None:
        """Show an open Window its Chat again from scratch, as after a Rewind (#58)."""
        if session.id not in self._links:
            return
        self.send(session.id, ChatClear())
        try:
            self._send_chat(session, None)
        except AllInError as error:
            self.show_error(error, session.id)

    def _send_chat(self, session: Session, streamed: Streamed | None) -> None:
        """Show a Window the Session's Chat so far, rebuilt from its Conversation and Log."""
        conversation = self._sessions.load_conversation(session) or Conversation(sysprompt="")
        log, archived = self._sessions.read_log(session), self._sessions.load_archived(session)
        for message in rebuild_chat(conversation, log, session.settings, archived):
            self.send(session.id, message)
        history = prompt_history(log)
        if history:  # what Up brings back to its input (#22)
            self.send(session.id, PromptHistory(prompts=history))
        self._show_current(session, streamed, conversation)
        if streamed is not None:  # what the running Turn has streamed so far; it goes on
            if streamed.reasoning:
                self.send(session.id, _delta(session, "reasoning", streamed.reasoning))
            if streamed.answer:
                self.send(session.id, _delta(session, "answer", streamed.answer))


def _last_turn_steps(conversation: Conversation) -> list[Step]:
    """The Steps of its last Turn, whose Context it holds unless a Compactación followed."""
    return conversation.turns[-1].steps if conversation.turns else []


def _delta(session: Session, kind: OutputKind, text: str) -> ChatDelta:
    if kind == "reasoning":
        return ChatDelta(role="reasoning", label="reasoning", text=text)
    return ChatDelta(role="answer", label=session.settings.assistant_name, text=text)
