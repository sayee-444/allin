"""Main's queue of Prompts and the Turns it runs from them (ADR-0004).

It holds the queue, the running Turns, how many may run at once, the slot lock each one holds
(ADR-0008) and the pause while the Server is not ready. Each queue entry is a whole ``Prompt``,
from ``push`` to the saved MainState, and ``PromptQueue.pop_ready`` alone decides which one
starts next; so a Prompt that gains an Origin (ADR-0013), or a Continuation that must go
before the new Prompts, changes those two and nothing here.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from allin.core.errors import AllInError, LlmUnavailable, TurnCancelled, TurnCrashed
from allin.ipc.messages import ChatAppend, InputRestore, TurnState
from allin.main.chat_rebuild import dropped_entry, error_entry, tokens_entry
from allin.main.conversation import Conversation, Step, Turn
from allin.main.executors import SHUTTING_DOWN, TurnJob
from allin.main.open_windows import OpenWindows, Streamed
from allin.main.prompts import Prompt, PromptQueue
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.slot_locks import SlotLock, SlotLocks
from allin.main.slots import ServerProps, capacity
from allin.main.sysprompt import render_sysprompt
from allin.main.turn import Cancellation, OutputKind, TurnRequest

type SubmitTurn = Callable[[TurnJob], None]
# Waits for one of the server's first N slot locks to be free; then ``SlotFreed`` reaches the
# inbox.
type WaitForSlot = Callable[[int], None]
# Makes the Server watch report what it sees next, even if it looks unchanged.
type RecheckServer = Callable[[], None]
# Why the queue is paused: the Server is down, loading its model, or Main has just started.
type PauseReason = Literal["down", "loading", "starting"]

_PAUSED_TEXTS: dict[PauseReason, str] = {
    "down": "queue paused: the LLM server cannot be reached, waiting for its /health",
    "loading": "queue paused: the LLM server is loading its model",
    "starting": "queue paused until the LLM server's /health is OK",
}
_READY_TEXT = "queue resumed: the LLM server is ready"
_RESUMED_TEXT = "queue resumed: the LLM server is back"


@dataclass
class _RunningTurn:
    prompt: Prompt
    session: Session
    cancellation: Cancellation
    slot: SlotLock  # held while its Step runs; in F1 a Turn is one Step
    streamed: Streamed = field(default_factory=Streamed)


class TurnQueue:
    def __init__(
        self,
        reporter: Reporter,
        windows: OpenWindows,
        sessions: SessionStore,
        sysprompts: Path,
        parallel: int | None,
        submit_turn: SubmitTurn,
        slot_locks: SlotLocks,
        wait_for_slot: WaitForSlot,
        recheck_server: RecheckServer,
    ) -> None:
        self._reporter = reporter
        self._windows = windows
        self._sessions = sessions
        self._sysprompts = sysprompts
        self._parallel = parallel
        self._submit_turn = submit_turn
        self._slot_locks = slot_locks
        self._wait_for_slot = wait_for_slot
        self._recheck_server = recheck_server
        self._server: ServerProps | None = None  # as the Server watch last said, once ready
        self._paused = False  # no Turn starts while the server is not ready
        self._pause_reason: PauseReason = "down"
        self._stopping = False  # nor once Main is shutting down
        self._prompts = PromptQueue()
        self._running: dict[str, _RunningTurn] = {}

    def push(self, session: Session, prompt: Prompt) -> None:
        """Queue a Prompt the user sent, show it in its Window and start it if there is room."""
        self._prompts.push(prompt)
        self._reporter.log_session(session.id, "prompt", text=prompt.text)
        label = session.settings.username
        self._windows.send(session.id, ChatAppend(role="user", label=label, text=prompt.text))
        self.send_turn_state(session.id)
        if self._paused:
            self._windows.send(session.id, self._paused_entry())
        self._start_turns()

    def requeue(self, prompts: list[Prompt]) -> None:
        """Put back, as they were, the Prompts a stopped Main left waiting."""
        for prompt in prompts:
            self._prompts.push(prompt)

    def output(self, session_id: str, kind: OutputKind, text: str) -> None:
        running = self._running.get(session_id)
        if running is None:
            return
        if kind == "reasoning":
            running.streamed.reasoning += text
        else:
            running.streamed.answer += text
        self._windows.stream(running.session, kind, text)

    def step_ended(self, session_id: str, step: Step) -> None:
        running = self._running.get(session_id)
        if running is None:
            return
        running.streamed.steps.append(step)
        self._windows.show_context(running.session, running.streamed.steps)

    def turn_ended(
        self, session_id: str, steps: list[Step] | None, error: AllInError | None
    ) -> None:
        running = self._running.pop(session_id, None)
        if running is None:
            return
        running.slot.release()
        try:
            if error is not None or steps is None:
                raise error or TurnCrashed(reason="no steps")
            turn = self._save_turn(running, steps)
        except AllInError as failure:
            self._fail_turn(running, failure)
            if isinstance(failure, LlmUnavailable):
                self.pause()
                self._recheck_server()  # it may have looked ready to the Server watch all along
                if not self._prompts.has(session_id):  # else ``pause`` or ``push`` told it
                    self._windows.send(session_id, self._paused_entry())
        else:
            self._reporter.record(session_id, "turn_finished", "turn finished", turn=turn)
            if (tokens := tokens_entry(steps)) is not None:
                self._windows.send(session_id, tokens)
        self.send_turn_state(session_id)
        self._start_turns()

    def cancel(self, session_id: str, reason: str) -> None:
        """Drop a Session's queued Prompts and stop its running Turn.

        The Turn stays active until its executor reports it ended, as ``turn_cancelled`` unless
        it had already finished; either way the Conversation only ever gets whole Turns.
        """
        texts = [prompt.text for prompt in self._prompts.drop(session_id)]
        if texts:
            shown = dropped_entry(len(texts), reason).text
            self._reporter.record(session_id, "prompts_dropped", shown, reason=reason, texts=texts)
        running = self._running.get(session_id)
        if running is not None:
            running.cancellation.cancel(reason)

    def server_ready(self, props: ServerProps) -> None:
        """Adapt to what the Server says of itself (ADR-0004), then go on with the queue."""
        if props != self._server:
            context_changed = (
                self._server is None or props.slot_context != self._server.slot_context
            )
            self._server = props
            turns = capacity(props, self._parallel)
            shown = (
                f"LLM server ready: {turns} turn(s) at once, {props.slot_context} tokens of "
                f"context per slot ({props.model_path})"
            )
            fields = {"capacity": turns, "parallel": self._parallel}
            self._reporter.report(None, "server_ready", shown, **vars(props), **fields)
            if context_changed:
                streamed = {sid: running.streamed for sid, running in self._running.items()}
                self._windows.set_context_limit(props.slot_context, streamed)
        self._resume()
        self._start_turns()  # there may be room for more Turns than before

    def pause(self, reason: PauseReason = "down") -> None:
        """Keep the queued Prompts until the server is back, instead of failing each in turn."""
        if self._paused:  # Turns running at once all fail when the server dies
            return
        self._paused = True
        self._pause_reason = reason
        self._reporter.report(None, "queue_paused", _PAUSED_TEXTS[reason])
        for session_id in self._prompts.session_ids():
            self._windows.send(session_id, self._paused_entry())

    def slot_freed(self) -> None:
        """A slot lock another Main held may be free now."""
        self._start_turns()

    def stop(self) -> None:
        """Main is shutting down: no Turn starts any more."""
        self._stopping = True

    def stop_turns(self) -> None:
        """On shutdown: cancel every running Turn.

        The Turns are ended here, as cancelled, since no event is handled after this. Their
        Prompts and the queue stay in the saved MainState, for the next start to give back.
        """
        for running in self._running.values():
            running.cancellation.cancel(SHUTTING_DOWN)
            running.slot.release()
            self._fail_turn(running, TurnCancelled(reason=SHUTTING_DOWN))
        self._running.clear()

    def send_turn_state(self, session_id: str) -> None:
        """Tell the Session's Window whether it has a Turn running or waiting."""
        active = session_id in self._running or self._prompts.has(session_id)
        self._windows.send(session_id, TurnState(active=active))

    def streamed(self, session_id: str) -> Streamed | None:
        """What the Session's running Turn has given so far; ``None`` if it has none."""
        running = self._running.get(session_id)
        return None if running is None else running.streamed

    def queued(self) -> list[Prompt]:
        """Every waiting Prompt, in queue order."""
        return self._prompts.all()

    def running_prompts(self) -> list[Prompt]:
        """The Prompts of the running Turns that a restart should give back."""
        # A Turn cancelled because its Window closed must not come back as a draft.
        return [
            running.prompt
            for running in self._running.values()
            if running.cancellation.reason is None
        ]

    def _start_turns(self) -> None:
        """Hand ready Prompts to executors while there is room on the server (ADR-0004).

        Room is the capacity this Main allows itself and a free slot lock, which Mains sharing
        the Server compete for (ADR-0008); without one, Prompts wait here until one is free.
        """
        while not (self._paused or self._stopping) and self._server is not None:
            if len(self._running) >= capacity(self._server, self._parallel):
                return
            slot = self._slot_locks.take(self._server.total_slots)
            if slot is None:
                if self._prompts.ready(active=self._running):
                    self._wait_for_slot(self._server.total_slots)
                return
            prompt = self._prompts.pop_ready(active=self._running)
            if prompt is None:
                slot.release()
                return
            try:
                session = self._sessions.load(prompt.session_id)
                conversation = self._conversation(session)
            except AllInError as error:
                slot.release()
                self._windows.show_error(error, prompt.session_id)
                self.send_turn_state(prompt.session_id)
                continue
            request = TurnRequest(
                model=session.settings.model,
                input=conversation.request_input(prompt.text),
                params=session.settings.sampling.request_params(),
            )
            job = TurnJob(session.id, request)
            self._running[session.id] = _RunningTurn(prompt, session, job.cancellation, slot)
            self._reporter.record(session.id, "turn_started", "turn started")
            self._submit_turn(job)

    def _resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        starting = self._pause_reason == "starting"
        self._reporter.report(None, "queue_resumed", _READY_TEXT if starting else _RESUMED_TEXT)
        self._start_turns()

    def _paused_entry(self) -> ChatAppend:
        return ChatAppend(role="info", label="queue", text=_PAUSED_TEXTS[self._pause_reason])

    def _conversation(self, session: Session) -> Conversation:
        conversation = self._sessions.load_conversation(session)
        if conversation is None:  # a Session from before Conversations were stored
            # Rendered once and kept, like the Sysprompt of a Session made by /new.
            conversation = Conversation(
                sysprompt=render_sysprompt(self._sysprompts, session.settings)
            )
            self._sessions.save_conversation(session, conversation)
        return conversation

    def _fail_turn(self, running: _RunningTurn, error: AllInError) -> None:
        session_id = running.session.id
        # What it streamed is in no Conversation, so the Log keeps it for the rebuilt Chat.
        streamed = running.streamed
        fields = {"error": str(error), "reasoning": streamed.reasoning, "answer": streamed.answer}
        cancelled = isinstance(error, TurnCancelled)
        ending = "cancelled" if cancelled else "failed"
        self._reporter.record(session_id, f"turn_{ending}", f"turn {ending}: {error}", **fields)
        self._windows.send(session_id, error_entry(str(error)))
        if not cancelled:  # its Prompt is in no Conversation, so the user may send it again
            self._windows.send(session_id, InputRestore(text=running.prompt.text))

    def _save_turn(self, running: _RunningTurn, steps: list[Step]) -> int:
        """Save the finished Turn and return its index in the Conversation."""
        # Saved only now, whole, so a Turn that fails half-way never reaches the disk.
        conversation = self._conversation(running.session)
        turn = Turn(prompt=running.prompt.text, steps=steps)
        self._sessions.save_conversation(running.session, conversation.with_turn(turn))
        return len(conversation.turns)
