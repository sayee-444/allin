"""Main's single dispatcher: the only writer of Main's state (ADR-0001).

High-level events go to Main's Log and, when they concern a Session, to its Log too; Main's
Chat shows them prefixed with ``[sid]``. Raw stream events go only to Main's Log, and to Main's
Chat for Sessions under ``/raw on``; streamed text is in neither Log.
"""

import json
import queue
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from allin.core.drafts import put_back
from allin.core.errors import (
    AllInError,
    InvalidArguments,
    LlmUnavailable,
    SessionNotOwned,
    TurnCancelled,
    TurnCrashed,
    UnknownCommand,
    WindowAlreadyOpen,
)
from allin.core.ids import MainId
from allin.core.settings import MainSettings
from allin.ipc.messages import (
    ChatAppend,
    ChatDelta,
    Close,
    ContextUsage,
    InputRestore,
    TurnState,
)
from allin.main.chat_rebuild import dropped_entry, error_entry, rebuild_chat, tokens_entry
from allin.main.conversation import Conversation, Step, Turn
from allin.main.events import (
    DraftReceived,
    ErrorOccurred,
    InputReceived,
    LlmAvailable,
    MainCommandEntered,
    MainEvent,
    Shutdown,
    StepEnded,
    TurnEnded,
    TurnOutput,
    TurnRaw,
    WindowClosed,
    WindowConnected,
)
from allin.main.executors import SHUTTING_DOWN, TurnJob
from allin.main.log import Log
from allin.main.prompts import Prompt, PromptQueue
from allin.main.sessions import Session, SessionStore
from allin.main.slots import Slots
from allin.main.state import MainState, MainStateStore
from allin.main.sysprompt import render_sysprompt
from allin.main.turn import Cancellation, OutputKind, TurnRequest
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
# Closes the Window of a Session (by its xterm's pid) that a killed Main left open; returns
# whether there was one.
type CloseOrphanWindow = Callable[[str, int], bool]
type SubmitTurn = Callable[[TurnJob], None]
# Starts watching the server's /health; once it is OK, ``LlmAvailable`` reaches the inbox.
type WatchHealth = Callable[[], None]

_PAUSED_TEXT = "queue paused: the LLM server cannot be reached, waiting for its /health"
_STARTING_TEXT = "queue paused until the LLM server's /health is OK"
_READY_TEXT = "queue resumed: the LLM server is ready"
_RESUMED_TEXT = "queue resumed: the LLM server is back"
_MAIN_DIED = "main stopped without closing it"
_TURN_ENDINGS = ("turn_finished", "turn_failed", "turn_cancelled")


@dataclass(frozen=True)
class _MainCommand:
    params: tuple[str, ...]
    run: Callable[..., bool]  # takes one str per param; returns whether Main keeps running
    rest: bool = False  # the last param takes the rest of the line verbatim, newlines included
    optional: tuple[str, ...] = ()  # params after ``params`` that may be left out

    def usage(self, name: str) -> str:
        words = [name, *(f"<{param}>" for param in self.params)]
        return " ".join(words + [f"[{param}]" for param in self.optional])


@dataclass
class _RunningTurn:
    prompt: Prompt
    session: Session
    cancellation: Cancellation
    reasoning: str = ""  # streamed so far, shown again if its Window is reopened mid-Turn
    answer: str = ""
    steps: list[Step] = field(default_factory=list[Step])  # ended so far


@dataclass
class _RawFilter:
    """Which Sessions show their raw stream events in Main's Chat (``/raw``)."""

    everyone: bool = False
    shown: set[str] = field(default_factory=set[str])
    hidden: set[str] = field(default_factory=set[str])  # exceptions while ``everyone``

    def switch(self, on: bool, session_id: str | None) -> None:
        if session_id is None:
            self.everyone = on
            self.shown.clear()
            self.hidden.clear()
        elif on:
            self.shown.add(session_id)
            self.hidden.discard(session_id)
        else:
            self.shown.discard(session_id)
            self.hidden.add(session_id)

    def __contains__(self, session_id: str) -> bool:
        if self.everyone:
            return session_id not in self.hidden
        return session_id in self.shown


class Dispatcher:
    def __init__(
        self,
        inbox: queue.Queue[MainEvent],
        main_chat: Callable[[str], None],
        main_log: Log,
        sessions: SessionStore,
        open_window: OpenWindow,
        submit_turn: SubmitTurn,
        main_id: MainId,
        settings: MainSettings,
        slots: Slots,
        sysprompts: Path,
        watch_health: WatchHealth,
        state: MainStateStore,
        close_orphan_window: CloseOrphanWindow,
    ) -> None:
        self._inbox = inbox
        self._main_chat = main_chat
        self._main_log = main_log
        self._sessions = sessions
        self._open_window = open_window
        self._submit_turn = submit_turn
        self._main_id = main_id
        self._settings = settings
        self._slots = slots
        self._sysprompts = sysprompts
        self._watch_health = watch_health
        self._state = state
        self._close_orphan_window = close_orphan_window
        self._saved = MainState()  # what ``state`` holds now
        self._paused = False  # no Turn starts while the server cannot be reached
        self._paused_text = _PAUSED_TEXT
        # Each Session's Draft, shown block first, as its Window last sent it.
        self._drafts: dict[str, list[str]] = {}
        self._stopping = False  # nor once Main is shutting down
        self._windows: dict[str, WindowLink] = {}
        self._prompts = PromptQueue()
        self._running: dict[str, _RunningTurn] = {}
        self._raw = _RawFilter()
        # Command mains are registered once, when Main starts.
        self._commands: dict[str, _MainCommand] = {
            "/new": _MainCommand((), self._new),
            "/open": _MainCommand(("sid",), self._open),
            # The only Command main that queues Prompts; Enter in a Window is /send to its own.
            "/send": _MainCommand(("sid", "text"), self._send, rest=True),
            "/raw": _MainCommand(("state",), self._set_raw, optional=("sid",)),
            "/exit": _MainCommand((), lambda: False),
        }

    def run(self) -> None:
        """Handle events until a Shutdown event or the /exit command."""
        slots = self._slots
        self._report(
            None,
            "main_started",
            slots.describe(),
            main_id=self._main_id,
            slots=slots.count,
            slots_source=slots.source,
            server_slots=slots.server_slots,
        )
        if (warning := slots.warning()) is not None:
            self._report(None, "warning", f"warning {warning}", warning=warning)
        self._close_left_open()
        self._restore()
        while self._handle(self._inbox.get()):
            self._save_state()
        self._stopping = True
        self._take_turn_events()
        self._save_state()  # Windows, queue and running Turns as they are: a restart restores them
        self._stop_turns()
        for link in self._windows.values():
            link.send(Close())
        for session_id, link in self._windows.items():
            link.close()
            shown = f"window closed: {SHUTTING_DOWN}"
            self._record(session_id, "window_closed", shown, returncode=None, reason=SHUTTING_DOWN)
        self._log_main("main_stopped", main_id=self._main_id)

    def _take_turn_events(self) -> None:
        """Handle what Turns and drafts reported before Main stopped, so a Turn that already
        ended is saved instead of being cancelled and the last draft is kept; no command or
        other Window event is taken any more."""
        while True:
            try:
                event = self._inbox.get(block=False)
            except queue.Empty:
                return
            if isinstance(event, TurnOutput | StepEnded | TurnEnded | DraftReceived):
                self._handle(event)

    def _restore(self) -> None:
        """Put the user back where they were when Main last stopped, however it stopped.

        A Turn that was running is reverted (only whole Turns reach a Conversation): its Prompt
        goes back to its Window's input, shown ahead of the Draft, and is not sent again by
        itself.
        The queue waits for the server's ``/health``, which may still be loading the model.
        """
        try:
            self._saved = self._state.load()
        except AllInError as error:
            self._show_error(error)
        saved = self._saved
        self._drafts = dict(saved.drafts)
        for prompt in saved.running:
            draft = self._drafts.get(prompt.session_id, [])
            self._drafts[prompt.session_id] = put_back(draft, prompt.text)
        for prompt in saved.queue:
            self._prompts.push(prompt)
        for session_id, pid in saved.windows.items():
            # Only one Main of this id runs (ADR-0007), so a Window of its still up is an orphan.
            if self._close_orphan_window(session_id, pid):
                shown = "window left open by the last main closed"
                self._record(session_id, "orphan_window_closed", shown, pid=pid)
            try:
                self._attach_window(self._owned_session(session_id))
            except AllInError as error:
                self._show_error(error, session_id)
        self._pause(_STARTING_TEXT)  # once the Windows are open, so they are told why
        self._save_state()

    def _save_state(self) -> None:
        state = MainState(
            windows={session_id: link.pid for session_id, link in self._windows.items()},
            drafts={session_id: draft for session_id, draft in self._drafts.items() if draft},
            queue=self._prompts.all(),
            # A Turn cancelled because its Window closed must not come back as a draft.
            running=[
                running.prompt
                for running in self._running.values()
                if running.cancellation.reason is None
            ],
        )
        if state == self._saved:
            return
        try:
            self._state.save(state)
        except AllInError as error:
            self._show_error(error)
        else:
            self._saved = state

    def _close_left_open(self) -> None:
        """Mark closed the Sessions a Main killed outright left with an open Window or a Turn.

        No Window outlives the Main that opened it and no Turn either, so on start every Session
        is closed; their Logs only need saying so where the dead Main could not. A Turn left
        unfinished also gets ``turn_failed`` (beyond what F1-11 asked), so its rebuilt Chat says
        why its Prompt has no answer instead of showing it as if it were still waiting.
        """
        for session_id in self._sessions.ids():
            try:
                session = self._owned_session(session_id)
                events = [entry.event for entry in self._sessions.read_log(session)]
            except AllInError:
                continue  # another Main's, or unreadable: /open reports it if it is ever asked for
            if _last_index(events, "turn_started") > _last_index(events, *_TURN_ENDINGS):
                # Whatever it streamed died with Main; its Prompt stays in the Chat.
                error = str(TurnCrashed(reason=_MAIN_DIED))
                fields = {"error": error, "reasoning": "", "answer": ""}
                self._record(session.id, "turn_failed", f"turn failed: {error}", **fields)
            if _last_index(events, "window_opened") > _last_index(events, "window_closed"):
                shown = f"window closed: {_MAIN_DIED}"
                self._record(session.id, "window_closed", shown, returncode=None)

    def _handle(self, event: MainEvent) -> bool:
        match event:
            case WindowConnected(session_id):
                self._record(session_id, "window_connected", "window connected")
                self._send_turn_state(session_id)
            case InputReceived(session_id, text):
                self._drafts.pop(session_id, None)  # the Window's input is empty now
                self._run_main_command(f"/send {session_id} {text}")
                self._send_turn_state(session_id)  # unblocks Enter if /send failed
            case DraftReceived(session_id, blocks):
                self._drafts[session_id] = list(blocks)
            case TurnOutput(session_id, kind, text):
                self._show_output(session_id, kind, text)
            case TurnRaw(session_id, raw):
                self._show_raw(session_id, raw)
            case StepEnded(session_id, step):
                self._end_step(session_id, step)
            case TurnEnded(session_id, steps, error):
                self._end_turn(session_id, steps, error)
            case WindowClosed(session_id, returncode):
                link = self._windows.pop(session_id, None)
                if link is not None:
                    link.close()
                text = f"window closed (exit code {returncode})"
                self._record(session_id, "window_closed", text, returncode=returncode)
                self._cancel(session_id, "window closed")
            case ErrorOccurred(error, session_id):
                self._show_error(error, session_id)
            case LlmAvailable():
                self._resume()
            case MainCommandEntered(text):
                return self._run_main_command(text)
            case Shutdown():
                return False
        return True

    def _run_main_command(self, text: str) -> bool:
        name = (text.split(maxsplit=1) or [""])[0]
        command = self._commands.get(name)
        args = text.split()[1:]
        if command is not None and command.rest:
            args = _split_rest(text, len(command.params))
        session_id = None
        keep_running = True
        try:
            if command is None:
                raise UnknownCommand(command=name)
            names = command.params + command.optional
            session_id = dict(zip(names, args, strict=False)).get("sid")
            if not len(command.params) <= len(args) <= len(names):
                raise InvalidArguments(usage=command.usage(name))
            keep_running = command.run(*args)
        except AllInError as error:
            self._report_command(session_id, text, error)
        else:
            self._report_command(session_id, text, None)
        return keep_running

    def _report_command(self, session_id: str | None, text: str, error: AllInError | None) -> None:
        result = "ok" if error is None else f"error {error}"
        shown = f"{_summary(text)}: {result}"
        self._report(session_id, "command", shown, command=text, result=result)

    def _report(self, session_id: str | None, event: str, shown: str, **fields: Any) -> None:
        """Show a high-level event in Main's Chat and keep it in Main's Log."""
        self._log_main(event, **({"sid": session_id} if session_id else {}), **fields)
        self._main_chat(f"[{session_id}] {shown}" if session_id else shown)

    def _record(self, session_id: str, event: str, shown: str, **fields: Any) -> None:
        """A high-level event of a Session: its Log, Main's Log and Main's Chat."""
        self._log_session(session_id, event, **fields)
        self._report(session_id, event, shown, **fields)

    def _log_main(self, event: str, **fields: Any) -> None:
        try:
            self._main_log.append(event, **fields)
        except AllInError as error:  # never logged again, or a broken Log would loop
            self._main_chat(f"error {error}")

    def _log_session(self, session_id: str, event: str, **fields: Any) -> None:
        try:
            self._sessions.log(session_id).append(event, **fields)
        except AllInError as error:
            self._report(session_id, "error", f"error {error}", error=str(error))

    def _show_error(self, error: AllInError, session_id: str | None = None) -> None:
        if session_id is None:
            self._report(None, "error", f"error {error}", error=str(error))
            return
        self._record(session_id, "error", f"error {error}", error=str(error))
        self._to_window(session_id, error_entry(str(error)))

    def _to_window(self, session_id: str, message: BaseModel) -> None:
        link = self._windows.get(session_id)
        if link is not None:
            link.send(message)

    def _new(self) -> bool:
        settings = self._settings.session  # copied: later changes to Main's never reach it
        sysprompt = render_sysprompt(self._sysprompts, settings)
        session = self._sessions.create(self._main_id, settings, sysprompt)
        self._record(session.id, "session_created", "session created", owner=self._main_id)
        self._attach_window(session)
        return True

    def _open(self, session_id: str) -> bool:
        session = self._owned_session(session_id)
        if session.id in self._windows:
            raise WindowAlreadyOpen(session_id=session.id)
        self._attach_window(session)
        return True

    def _send(self, session_id: str, text: str) -> bool:
        session = self._owned_session(session_id)
        self._prompts.push(Prompt(session.id, text))
        self._log_session(session.id, "prompt", text=text)
        label = session.settings.username
        self._to_window(session.id, ChatAppend(role="user", label=label, text=text))
        self._send_turn_state(session.id)
        if self._paused:
            self._to_window(session.id, self._paused_entry())
        self._start_turns()
        return True

    def _set_raw(self, state: str, session_id: str | None = None) -> bool:
        if state not in ("on", "off"):
            raise InvalidArguments(usage=self._commands["/raw"].usage("/raw"))
        if session_id is not None:
            session_id = self._owned_session(session_id).id
        self._raw.switch(state == "on", session_id)
        return True

    def _owned_session(self, session_id: str) -> Session:
        session = self._sessions.load(session_id)
        if session.owner != self._main_id:
            raise SessionNotOwned(session_id=session.id, owner=session.owner)
        return session

    def _attach_window(self, session: Session) -> None:
        self._windows[session.id] = self._open_window(session.id)
        self._record(session.id, "window_opened", "opening window")
        try:
            self._send_chat(session)
        except AllInError as error:
            self._show_error(error, session.id)
        # Last block first: each one shown pushes the one before it back to wait.
        for block in reversed(self._drafts.get(session.id, [])):
            self._to_window(session.id, InputRestore(text=block))

    def _send_chat(self, session: Session) -> None:
        """Show a Window the Session's Chat so far, rebuilt from its Conversation and Log."""
        conversation = self._sessions.load_conversation(session) or Conversation(sysprompt="")
        for message in rebuild_chat(
            conversation, self._sessions.read_log(session), session.settings
        ):
            self._to_window(session.id, message)
        running = self._running.get(session.id)
        if running is not None and running.steps:
            last_steps = running.steps
        else:
            last_steps = conversation.turns[-1].steps if conversation.turns else []
        self._send_context(session, last_steps)
        if running is not None:  # what the running Turn has streamed so far; it goes on streaming
            if running.reasoning:
                self._to_window(session.id, self._delta(running, "reasoning", running.reasoning))
            if running.answer:
                self._to_window(session.id, self._delta(running, "answer", running.answer))

    def _start_turns(self) -> None:
        """Hand ready Prompts to executors while the server has free slots (ADR-0004)."""
        while not (self._paused or self._stopping) and len(self._running) < self._slots.count:
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
            request = TurnRequest(
                model=session.settings.model,
                input=conversation.request_input(prompt.text),
                params=session.settings.sampling.request_params(),
            )
            job = TurnJob(session.id, request)
            self._running[session.id] = _RunningTurn(prompt, session, job.cancellation)
            self._record(session.id, "turn_started", "turn started")
            self._submit_turn(job)

    def _pause(self, text: str = _PAUSED_TEXT) -> None:
        """Keep the queued Prompts until the server is back, instead of failing each in turn."""
        if self._paused:  # Turns running at once all fail when the server dies
            return
        self._paused = True
        self._paused_text = text
        self._report(None, "queue_paused", text)
        for session_id in self._prompts.session_ids():
            self._to_window(session_id, self._paused_entry())
        self._watch_health()

    def _paused_entry(self) -> ChatAppend:
        return ChatAppend(role="info", label="queue", text=self._paused_text)

    def _resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        starting = self._paused_text == _STARTING_TEXT
        self._report(None, "queue_resumed", _READY_TEXT if starting else _RESUMED_TEXT)
        self._start_turns()

    def _cancel(self, session_id: str, reason: str) -> None:
        """Drop a Session's queued Prompts and stop its running Turn.

        The Turn stays active until its executor reports it ended, as ``turn_cancelled`` unless
        it had already finished; either way the Conversation only ever gets whole Turns.
        """
        self._drop_prompts(session_id, reason)
        running = self._running.get(session_id)
        if running is not None:
            running.cancellation.cancel(reason)

    def _drop_prompts(self, session_id: str, reason: str) -> list[str]:
        """Take a Session's Prompts out of the queue and return their texts, in their order."""
        texts = [prompt.text for prompt in self._prompts.drop(session_id)]
        if texts:
            shown = dropped_entry(len(texts), reason).text
            self._record(session_id, "prompts_dropped", shown, reason=reason, texts=texts)
        return texts

    def _stop_turns(self) -> None:
        """On shutdown: cancel every running Turn.

        The Turns are ended here, as cancelled, since no event is handled after this. Their
        Prompts and the queue stay in the saved MainState, for the next start to give back.
        """
        for running in self._running.values():
            running.cancellation.cancel(SHUTTING_DOWN)
            self._fail_turn(running, TurnCancelled(reason=SHUTTING_DOWN))
        self._running.clear()

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
            running.reasoning += text
        else:
            running.answer += text
        self._to_window(session_id, self._delta(running, kind, text))

    @staticmethod
    def _delta(running: _RunningTurn, kind: OutputKind, text: str) -> ChatDelta:
        if kind == "reasoning":
            return ChatDelta(role="reasoning", label="reasoning", text=text)
        return ChatDelta(role="answer", label=running.session.settings.assistant_name, text=text)

    def _show_raw(self, session_id: str, raw: dict[str, Any]) -> None:
        self._log_main("raw", sid=session_id, data=raw)
        if session_id in self._raw:
            self._main_chat(f"[{session_id}] raw {json.dumps(raw, ensure_ascii=False)}")

    def _end_step(self, session_id: str, step: Step) -> None:
        running = self._running.get(session_id)
        if running is None:
            return
        running.steps.append(step)
        self._send_context(running.session, running.steps)

    def _send_context(self, session: Session, steps: list[Step]) -> None:
        """Show the Window the Context ``steps`` leave and how fast the last one generated."""
        usage = ContextUsage(
            context=context_tokens(steps),
            limit=session.settings.context_limit,
            tokens_per_second=tokens_per_second(steps),
        )
        self._to_window(session.id, usage)

    def _end_turn(
        self, session_id: str, steps: list[Step] | None, error: AllInError | None
    ) -> None:
        running = self._running.pop(session_id, None)
        if running is None:
            return
        try:
            if error is not None or steps is None:
                raise error or TurnCrashed(reason="no steps")
            turn = self._save_turn(running, steps)
        except AllInError as failure:
            self._fail_turn(running, failure)
            if isinstance(failure, LlmUnavailable):
                self._pause()
                if not self._prompts.has(session_id):  # else ``_pause`` or ``_send`` told it
                    self._to_window(session_id, self._paused_entry())
        else:
            self._record(session_id, "turn_finished", "turn finished", turn=turn)
            if (tokens := tokens_entry(steps)) is not None:
                self._to_window(session_id, tokens)
        self._send_turn_state(session_id)
        self._start_turns()

    def _fail_turn(self, running: _RunningTurn, error: AllInError) -> None:
        session_id = running.session.id
        # What it streamed is in no Conversation, so the Log keeps it for the rebuilt Chat.
        fields = {"error": str(error), "reasoning": running.reasoning, "answer": running.answer}
        cancelled = isinstance(error, TurnCancelled)
        ending = "cancelled" if cancelled else "failed"
        self._record(session_id, f"turn_{ending}", f"turn {ending}: {error}", **fields)
        self._to_window(session_id, error_entry(str(error)))
        if not cancelled:  # its Prompt is in no Conversation, so the user may send it again
            self._to_window(session_id, InputRestore(text=running.prompt.text))

    def _save_turn(self, running: _RunningTurn, steps: list[Step]) -> int:
        """Save the finished Turn and return its index in the Conversation."""
        # Saved only now, whole, so a Turn that fails half-way never reaches the disk.
        conversation = self._conversation(running.session)
        turn = Turn(prompt=running.prompt.text, steps=steps)
        self._sessions.save_conversation(running.session, conversation.with_turn(turn))
        return len(conversation.turns)

    def _send_turn_state(self, session_id: str) -> None:
        active = session_id in self._running or self._prompts.has(session_id)
        self._to_window(session_id, TurnState(active=active))


def _last_index(events: list[str], *names: str) -> int:
    """Where the last of ``names`` is in ``events``; -1 if none is."""
    return max((index for index, event in enumerate(events) if event in names), default=-1)


def _summary(text: str, width: int = 60) -> str:
    """A Command main as Main's Chat shows it: its first line, cut to ``width``."""
    lines = text.splitlines() or [""]
    first = lines[0]
    if len(lines) > 1 or len(first) > width:
        return first[:width] + "…"
    return first


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
