"""Main's queue of Prompts and the Turns it runs from them (ADR-0004).

It holds the queue, the running Turns, how many may run at once, the slot lock each one holds
(ADR-0008) and what each Server last said of itself. Each Session's Steps go to the Server of
its Enlace (ADR-0022): while that one is not ready, only its Sessions wait, and new work for an
asleep Supervisor wakes it.

Each queue entry is a whole ``Prompt``, carried as it is from ``push`` to its running Turn and
the saved MainState, so an Origin (ADR-0013) added to ``Prompt`` reaches every place that needs
it. A Turn that waits for a Confirmation's answer holds no slot; once answered it is a
Continuation and takes the next free slot before any new Prompt (``_next_continuation``);
letting a new Prompt through after several Continuations in a row comes with F2's. Among the
new Prompts, the first in the queue whose Session is idle and whose Server has room starts. What depends on the Origin is
in ``push`` (the ``prompt`` event and the user's line in the Window) and ``_fail_turn`` (the
Prompt back to the input, only for a human one).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import JsonValue

from allin.core.errors import (
    AllInError,
    LlmUnavailable,
    StepStalled,
    TurnCancelled,
    TurnCrashed,
)
from allin.core.ids import BoxId, MainId, ServerKey, SessionId, SupervisorName
from allin.ipc.messages import ChatAppend, InputRestore, TurnState
from allin.main.boxes import MainBoxes
from allin.main.chat_rebuild import dropped_entry, error_entry, tokens_entry
from allin.main.conversation import Conversation, Step, Turn
from allin.main.devastation import Devastation
from allin.main.events import (
    ServerAsleep,
    ServerDown,
    ServerFailed,
    ServerLoading,
    ServerReady,
    ServerRestarting,
    ServerState,
)
from allin.main.executors import SHUTTING_DOWN, TurnJob
from allin.main.links import Link, LinkResolver
from allin.main.notices import NoticeTypes
from allin.main.open_windows import OpenWindows, Streamed
from allin.main.prompts import Prompt, PromptQueue
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.slot_locks import SlotLock
from allin.main.slots import ServerProps, capacity
from allin.main.sysprompt import render_sysprompt
from allin.main.turn import Cancellation, OutputKind, TurnRequest
from allin.main.usage import context_tokens
from allin.notices import NoticeEvent

type SubmitTurn = Callable[[TurnJob], None]


class Servers(Protocol):
    """Main's side of the Servers its Sessions use (``ServerLinks``)."""

    def watch(self, link: Link) -> None:
        """Start watching the link's Server; it reports through ``ServerChanged``."""
        ...

    def take_slot(self, server: ServerKey, slots: int) -> SlotLock | None:
        """A free lock among the Server's first ``slots``; ``None`` if every one is held."""
        ...

    def wait_for_slot(self, server: ServerKey, slots: int) -> None:
        """Wait for one of those locks to be free; then ``SlotFreed`` reaches the inbox."""
        ...

    def recheck(self, server: ServerKey) -> None:
        """Make its watch report what it sees next, even if it looks unchanged."""
        ...

    def wake(self, name: SupervisorName) -> bool:
        """Start an asleep Supervisor; whether it did."""
        ...

    def restart(self, name: SupervisorName, now: bool) -> None:
        """Ask a running Supervisor to restart its Server (``SupervisorNotRunning`` if not)."""
        ...


_RESUMED_TEXT = "queue resumed: {server} is ready"
# Why Main cancels a Turn's Step itself: a restart with --now (repeated after it, ADR-0023) or
# no progress for ``step.stall`` seconds (it fails).
RESTARTING = "the Server restarts now"
STALLED = "the Step made no progress"
_CUT_SHORT = (
    "step cut short: {why}; what it gave is dropped and it runs again once the Server is back"
)
_MAX_CUTS = 2  # the same Step cut short by this many falls in a row: its Turn fails


def _waiting_text(link: Link, state: ServerState | None) -> str:
    """Why the Prompts of the link's Sessions wait, for Main's Chat and their Windows."""
    server = link.describe()
    match state:
        case None:
            return f"queue paused until {server}'s /health is OK"
        case ServerLoading():
            return f"queue paused: {server} is loading its model"
        case ServerRestarting():
            return f"queue paused: {server} is restarting"
        case ServerAsleep(slept_by_human=True):
            return f"queue paused: {server} was put to sleep by a human; only a human wakes it"
        case ServerAsleep():
            return f"queue paused: {server} is asleep; work for it wakes it"
        case ServerFailed(failure="vram", gpu_holders=holders):
            return f"queue paused: {server} does not fit in VRAM; {_gpu_holders(holders)}"
        case ServerFailed(failure=failure, reason=reason):
            return f"queue paused: {server} failed ({failure}): {reason}"
        case _:
            return f"queue paused: {server} cannot be reached, waiting for its /health"


def _gpu_holders(holders: tuple[SupervisorName, ...]) -> str:
    if not holders:
        return "no Supervisor is active, so something outside AllIn holds the GPU"
    names = ", ".join(holders)
    return f"the GPU is held by Supervisor {names}; it is tried again when one falls asleep"


@dataclass
class _RunningTurn:
    prompt: Prompt
    session: Session
    link: Link  # where its Steps go, from when it started
    cancellation: Cancellation
    job: TurnJob
    # Held while its Steps run; ``None`` while it waits for a Confirmation's answer.
    slot: SlotLock | None
    # Whether Main answers its Confirmations itself: ``--devastation`` and the Session's
    # Exemption as they were when it started, so changing them only reaches the next Turn.
    auto_confirm: bool
    progress_at: float  # when its Step last showed progress: started, or a stream event
    streamed: Streamed = field(default_factory=Streamed)
    asking: BoxId | None = None  # the Confirmation it waits on, if any
    answer: bool | None = None  # its answer, while it waits for a slot to go on

    def release_slot(self) -> None:
        if self.slot is not None:
            self.slot.release()
            self.slot = None


class TurnQueue:
    def __init__(
        self,
        reporter: Reporter,
        windows: OpenWindows,
        sessions: SessionStore,
        sysprompts: Path,
        parallel: int | None,
        submit_turn: SubmitTurn,
        links: LinkResolver,
        servers: Servers,
        boxes: MainBoxes,
        devastation: Devastation,
        notices: NoticeTypes,
        main_id: MainId,
        stall: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._main_id = main_id
        self._stall = stall
        self._clock = clock
        # How many falls in a row cut each Session's current Step short (ADR-0023).
        self._cuts: dict[SessionId, int] = {}
        self._contexts: dict[ServerKey, int] = {}  # each Server's slot context, last seen
        self._notices = notices
        self._boxes = boxes
        self._devastation = devastation
        self._reporter = reporter
        self._windows = windows
        self._sessions = sessions
        self._sysprompts = sysprompts
        self._parallel = parallel
        self._submit_turn = submit_turn
        self._links = links
        self._servers = servers
        # What each watched Server last said of itself; a Turn starts only on a ready one.
        self._states: dict[ServerKey, ServerState | None] = {}
        self._watched: dict[ServerKey, Link] = {}
        self._told: dict[ServerKey, str] = {}  # why it waits, as Main's Chat was last told
        self._stopping = False  # nor once Main is shutting down
        self._prompts = PromptQueue()
        self._running: dict[SessionId, _RunningTurn] = {}

    def push(self, session: Session, prompt: Prompt) -> None:
        """Queue a Prompt the user sent, show it in its Window and start it if there is room."""
        self._prompts.push(prompt)
        self._reporter.log_session(session.id, "prompt", text=prompt.text)
        label = session.settings.username
        self._windows.send(session.id, ChatAppend(role="user", label=label, text=prompt.text))
        self.send_turn_state(session.id)
        link = self._watched_link(session)
        if link is not None and not self._ready(link.key):
            self._windows.send(session.id, self._waiting_entry(link))
            # New work wakes it (ADR-0022), but not one that did not fit in VRAM: that one is
            # tried again when another falls asleep.
            state = self._states.get(link.key)
            vram = isinstance(state, ServerFailed) and state.failure == "vram"
            if link.supervisor is not None and not vram:
                self._wake(link.supervisor)
        self._start_turns()

    def watch(self, link: Link) -> None:
        """Watch a Server even before any Session needs it: Main's default Enlace."""
        self._watch(link)

    def window_connected(self, session_id: SessionId) -> None:
        """Tell a Window that just connected its Turn state; its Server is watched from now on,
        so the Window gets its context limit."""
        self._watched_link_of(session_id)
        self.send_turn_state(session_id)

    def requeue(self, prompts: list[Prompt]) -> None:
        """Put back, as they were, the Prompts a stopped Main left waiting."""
        for prompt in prompts:
            self._prompts.push(prompt)

    def progress(self, session_id: SessionId) -> None:
        """The Session's running Step sent something: it is not stalled."""
        if (running := self._running.get(session_id)) is not None:
            running.progress_at = self._clock()

    def tick(self) -> None:
        """Cancel each Step that made no progress for ``step.stall`` seconds (ADR-0023)."""
        now = self._clock()
        for running in self._running.values():
            in_step = running.slot is not None and running.cancellation.reason is None
            if in_step and now - running.progress_at >= self._stall:
                running.cancellation.cancel(STALLED)

    def set_stall(self, stall: float) -> None:
        self._stall = stall

    def output(self, session_id: SessionId, kind: OutputKind, text: str) -> None:
        running = self._running.get(session_id)
        if running is None:
            return
        running.progress_at = self._clock()
        if kind == "reasoning":
            running.streamed.reasoning += text
        else:
            running.streamed.answer += text
        self._windows.stream(running.session, kind, text)

    def step_ended(self, session_id: SessionId, step: Step) -> None:
        running = self._running.get(session_id)
        if running is None:
            return
        running.streamed.steps.append(step)
        self._windows.show_context(running.session, running.streamed.steps)
        self._tell_notices(running)

    def turn_ended(
        self, session_id: SessionId, steps: list[Step] | None, error: AllInError | None
    ) -> None:
        running = self._running.pop(session_id, None)
        if running is None:
            return
        running.release_slot()
        self._withdraw(running)
        try:
            if error is not None or steps is None:
                raise error or TurnCrashed(reason="no steps")
            turn = self._save_turn(running, steps)
        except AllInError as failure:
            if self._cut_short(running, failure):
                pass
            elif _reason(failure) == STALLED:
                self._stalled(running)
            else:
                self._cuts.pop(session_id, None)
                self._fail_turn(running, failure)
            if isinstance(failure, LlmUnavailable):
                key = running.link.key
                if self._ready(key):  # Turns running at once all fail when the server dies
                    self._server_changed(key, ServerDown())
                self._servers.recheck(key)  # it may have looked ready to the watch all along
                if not self._prompts.has(session_id):  # else the change or ``push`` told it
                    self._windows.send(session_id, self._waiting_entry(running.link))
        else:
            self._cuts.pop(session_id, None)
            self._reporter.record(session_id, "turn_finished", "turn finished", turn=turn)
            if (tokens := tokens_entry(steps)) is not None:
                self._windows.send(session_id, tokens)
        self.send_turn_state(session_id)
        self._start_turns()

    def cancel(self, session_id: SessionId, reason: str) -> None:
        """Drop a Session's queued Prompts and stop its running Turn.

        The Turn stays active until its executor reports it ended, as ``turn_cancelled`` unless
        it had already finished; either way the Conversation only ever gets whole Turns.
        """
        self._cuts.pop(session_id, None)
        texts: list[JsonValue] = [prompt.text for prompt in self._prompts.drop(session_id)]
        if texts:
            shown = dropped_entry(len(texts), reason).text
            self._reporter.record(session_id, "prompts_dropped", shown, reason=reason, texts=texts)
        running = self._running.get(session_id)
        if running is not None:
            running.cancellation.cancel(reason)
            self._withdraw(running)

    def confirmation_requested(self, session_id: SessionId, title: str, text: str) -> None:
        """A running Turn asks yes or no: it gives its slot back while a human answers
        (ADR-0009), so Prompts that wait can use it."""
        running = self._running.get(session_id)
        if running is None or running.cancellation.reason is not None:
            return
        if running.auto_confirm:  # answered at once: the Turn keeps its slot and goes on
            fields = {"title": title, "text": text, "answer": True}
            shown = f"confirmation answered by --devastation: {title}"
            self._reporter.record(session_id, "confirmation_auto_answered", shown, **fields)
            running.job.confirmation.resume(True)
            return
        running.release_slot()
        self._reporter.record(session_id, "confirmation_asked", f"confirmation: {title}")

        def on_answer(answer: bool) -> None:
            self._reporter.record(
                session_id, "confirmation_answered", f"confirmation: {answer}", answer=answer
            )
            self._answered(running, answer)

        running.asking = self._boxes.confirm(session_id, title, text, on_answer)
        self._start_turns()

    def server_changed(self, server: ServerKey, state: ServerState) -> None:
        """What a watched Server says of itself changed (ADR-0004, ADR-0022)."""
        if server in self._watched:
            self._server_changed(server, state)
        self._start_turns()

    def tell_waiting(self) -> None:
        """Tell each Session with queued Prompts why they wait, if their Server is not ready:
        on start, once the Windows are open."""
        for session_id in self._prompts.session_ids():
            link = self._watched_link_of(session_id)
            if link is not None and not self._ready(link.key):
                self._windows.send(session_id, self._waiting_entry(link))

    def slot_freed(self, server: ServerKey) -> None:
        """A slot lock of ``server`` another Main held may be free now."""
        del server  # any free slot may let the first waiting Turn start
        self._start_turns()

    def set_parallel(self, parallel: int | None) -> None:
        """How many Turns this Main may run at once from now on; running ones go on."""
        self._parallel = parallel
        self._start_turns()

    def stop(self) -> None:
        """Main is shutting down: no Turn starts any more."""
        self._stopping = True

    def cancel_running(self) -> None:
        """On shutdown: cancel every running Turn.

        The Turns are ended here, as cancelled, since no event is handled after this. Their
        Prompts and the queue stay in the saved MainState, for the next start to give back.
        """
        for running in self._running.values():
            running.cancellation.cancel(SHUTTING_DOWN)
            running.release_slot()
            self._withdraw(running)
            self._fail_turn(running, TurnCancelled(reason=SHUTTING_DOWN))
        self._running.clear()

    def send_turn_state(self, session_id: SessionId) -> None:
        """Tell the Session's Window whether it has a Turn running or waiting."""
        active = session_id in self._running or self._prompts.has(session_id)
        self._windows.send(session_id, TurnState(active=active))

    def streamed(self, session_id: SessionId) -> Streamed | None:
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
        """Hand Turns to executors while there is room (ADR-0004, ADR-0022).

        Room is this Main's ``parallel``, summing every Server, and on each Server a free slot
        lock, which Mains sharing it compete for (ADR-0008). A Server that is not ready, or full,
        holds back only its own Sessions.
        """
        while not self._stopping and self._room_in_main() and self._start_one():
            pass

    def _room_in_main(self) -> bool:
        holding = sum(running.slot is not None for running in self._running.values())
        return self._parallel is None or holding < self._parallel

    def _start_one(self) -> bool:
        """Start one answered Turn or new Prompt; ``False`` if none can start now."""
        full: set[ServerKey] = set()  # no slot there now: what waits for it waits on
        for running in self._continuations():
            slot = self._slot_on(running.link.key, full)
            if slot is not None:  # an answered Turn goes on before any new Prompt
                self._resume_turn(running, slot)
                return True
        for prompt in self._prompts.waiting(active=self._running):
            try:
                session = self._sessions.load(prompt.session_id)
                link = self._links.resolve(session.settings)
            except AllInError as error:
                self._prompts.remove(prompt)
                self._windows.show_error(error, prompt.session_id)
                self.send_turn_state(prompt.session_id)
                return True
            self._watch(link)  # its Enlace may be new since it was queued
            slot = self._slot_on(link.key, full)
            if slot is None:
                continue
            self._prompts.remove(prompt)
            self._start_prompt(prompt, session, link, slot)
            return True
        return False

    def _slot_on(self, server: ServerKey, full: set[ServerKey]) -> SlotLock | None:
        """A slot of ``server`` if it is ready and has one free; else ``server`` joins
        ``full`` until the next look."""
        state = self._states.get(server)
        if server in full or not isinstance(state, ServerReady):
            full.add(server)
            return None
        slots = state.props.total_slots
        holding = sum(
            running.slot is not None and running.link.key == server
            for running in self._running.values()
        )
        slot = self._servers.take_slot(server, slots) if holding < slots else None
        if slot is None:
            full.add(server)
            if holding < slots:  # another Main holds it
                self._servers.wait_for_slot(server, slots)
        return slot

    def _start_prompt(self, prompt: Prompt, session: Session, link: Link, slot: SlotLock) -> None:
        try:
            conversation = self._conversation(session)
        except AllInError as error:
            slot.release()
            self._windows.show_error(error, prompt.session_id)
            self.send_turn_state(prompt.session_id)
            return
        request = TurnRequest(
            model=session.settings.model,
            input=conversation.request_input(prompt.text),
            params=session.settings.sampling.request_params(),
        )
        job = TurnJob(session.id, request, link.base_url)
        auto_confirm = self._devastation.covers(session.settings)
        self._running[session.id] = _RunningTurn(
            prompt, session, link, job.cancellation, job, slot, auto_confirm, self._clock()
        )
        self._reporter.record(session.id, "turn_started", "turn started")
        self._submit_turn(job)

    def _server_changed(self, server: ServerKey, state: ServerState) -> None:
        link = self._watched[server]
        before = self._states.get(server)
        self._states[server] = state
        if isinstance(state, ServerReady):
            self._server_ready(link, state.props, state.fallback, before)
            return
        waiting = _waiting_text(link, state)
        if self._told.get(server) != waiting:
            self._told[server] = waiting
            fields: dict[str, JsonValue] = {"server": server, "state": type(state).__name__}
            self._reporter.report(None, "queue_paused", waiting, **fields)
            for session_id in self._sessions_on(server):
                self._windows.send(session_id, self._waiting_entry(link))
        if isinstance(state, ServerRestarting) and state.now:
            for running in self._running.values():  # repeated once it is back (ADR-0023)
                if running.link.key == server and running.slot is not None:
                    running.cancellation.cancel(RESTARTING)
        if isinstance(state, ServerFailed) and state.failure == "vram":
            self._vram_failed(link, state, before)
        elif isinstance(state, ServerFailed) and state != before:
            title = f"Supervisor {link.supervisor} failed ({state.failure})"
            self._boxes.notify(None, title, state.reason, "supervisor_failed")
        elif (
            isinstance(state, ServerAsleep)
            and not state.slept_by_human
            and link.supervisor is not None
            and self._has_work(server)
        ):
            self._wake(link.supervisor)

    def _server_ready(
        self,
        link: Link,
        props: ServerProps,
        fallback: str | None,
        before: ServerState | None,
    ) -> None:
        """Adapt to what the Server says of itself (ADR-0004)."""
        server = link.key
        old = before.props if isinstance(before, ServerReady) else None
        old_fallback = before.fallback if isinstance(before, ServerReady) else None
        if fallback is not None and fallback != old_fallback:
            shown = f"{link.describe()} runs its last Settings that started: {fallback}"
            self._reporter.report(None, "server_fallback", shown, server=server, reason=fallback)
        last_context = self._contexts.get(server)
        self._contexts[server] = props.slot_context
        if last_context is not None and props.slot_context < last_context:
            self._tell_over_limit(link, props.slot_context)
        if props != old:
            turns = capacity(props, self._parallel)
            shown = (
                f"{link.describe()} ready: {turns} turn(s) at once, {props.slot_context} tokens"
                f" of context per slot ({props.model_path})"
            )
            fields = {"server": server, "capacity": turns, "parallel": self._parallel}
            self._reporter.report(None, "server_ready", shown, **vars(props), **fields)
            if old is None or props.slot_context != old.slot_context:
                streamed = {sid: running.streamed for sid, running in self._running.items()}
                self._windows.set_context_limit(server, props.slot_context, streamed)
        if self._told.pop(server, None) is not None:
            shown = _RESUMED_TEXT.format(server=link.describe())
            self._reporter.report(None, "queue_resumed", shown, server=server)

    def _tell_over_limit(self, link: Link, limit: int) -> None:
        """A Notice naming this Main's Sessions on ``link`` whose Context no longer fits its
        lowered slot limit (ADR-0022); compacting them comes with #46."""
        over: list[str] = []
        for session_id in self._sessions.ids():
            try:
                session = self._sessions.load(session_id)
                if session.owner != self._main_id or self._links.resolve(session.settings) != link:
                    continue
                conversation = self._sessions.load_conversation(session)
            except AllInError:
                continue
            steps = conversation.turns[-1].steps if conversation and conversation.turns else []
            context = context_tokens(steps)
            if context is not None and context > limit:
                over.append(f"{session_id} ({context})")
        if over:
            title = f"{link.describe()}: context limit lowered to {limit} tokens"
            text = f"over the limit: {', '.join(over)}"
            self._boxes.notify(None, title, text, "context_limit")

    def _cut_short(self, running: _RunningTurn, failure: AllInError) -> bool:
        """A Step a fall or a restart of its Server left half-done goes back first in the
        queue, to run again once the Server is back (ADR-0023); ``False`` if it must fail:
        the same Step cut short by two falls in a row."""
        restart = _reason(failure) == RESTARTING
        if not (restart or isinstance(failure, LlmUnavailable)):
            return False
        session_id = running.session.id
        streamed = running.streamed
        half_done = bool(streamed.reasoning or streamed.answer)
        cuts = self._cuts.get(session_id, 0) + (1 if half_done and not restart else 0)
        if cuts >= _MAX_CUTS:
            self._cuts.pop(session_id, None)
            return False
        self._cuts[session_id] = cuts
        why = "the Server restarted" if restart else "the Server fell"
        shown = _CUT_SHORT.format(why=why)
        fields = {"error": str(failure), "reasoning": streamed.reasoning, "answer": streamed.answer}
        self._reporter.record(session_id, "step_cut_short", shown, **fields)
        self._windows.send(session_id, ChatAppend(role="info", label="server", text=shown))
        self._prompts.push_front(running.prompt)
        return True

    def _stalled(self, running: _RunningTurn) -> None:
        """No progress for ``step.stall`` seconds: the Step fails, without repeating, since it
        would stall again on the same Server (ADR-0023)."""
        session_id = running.session.id
        self._cuts.pop(session_id, None)
        self._fail_turn(running, StepStalled(seconds=self._stall))
        title = f"[{session_id}] Step stalled"
        text = f"no progress for {self._stall:g} s on {running.link.describe()}: cancelled"
        self._boxes.notify(session_id, title, text, "step_stalled")

    def _vram_failed(self, link: Link, state: ServerFailed, before: ServerState | None) -> None:
        """Main never puts a Supervisor to sleep to make room; it tries again when one falls
        asleep (ADR-0022)."""
        assert link.supervisor is not None
        was = (
            before.gpu_holders
            if isinstance(before, ServerFailed) and before.failure == "vram"
            else None
        )
        if was is None or set(state.gpu_holders) - set(was):
            title = f"Supervisor {link.supervisor} does not fit in VRAM"
            self._boxes.notify(None, title, _gpu_holders(state.gpu_holders), "vram")
        elif set(was) - set(state.gpu_holders) and self._has_work(link.key):
            self._wake(link.supervisor)

    def _wake(self, name: SupervisorName) -> None:
        if self._servers.wake(name):
            self._reporter.report(
                None, "supervisor_woken", f"waking Supervisor {name}", server=name
            )

    def _watch(self, link: Link) -> None:
        if link.key not in self._watched:
            self._watched[link.key] = link
            self._states[link.key] = None
            self._servers.watch(link)
            waiting = _waiting_text(link, None)
            self._told[link.key] = waiting
            self._reporter.report(None, "queue_paused", waiting, server=link.key, state="None")

    def _ready(self, server: ServerKey) -> bool:
        return isinstance(self._states.get(server), ServerReady)

    def _watched_link(self, session: Session) -> Link | None:
        """The Session's Enlace, watched from now on; ``None`` if it cannot be resolved, which
        its Prompt tells when it would start."""
        try:
            link = self._links.resolve(session.settings)
        except AllInError:
            return None
        self._watch(link)
        return link

    def _watched_link_of(self, session_id: SessionId) -> Link | None:
        try:
            return self._watched_link(self._sessions.load(session_id))
        except AllInError:
            return None

    def _sessions_on(self, server: ServerKey) -> list[SessionId]:
        """The Sessions with queued Prompts whose Enlace is ``server``."""
        return [
            session_id
            for session_id in self._prompts.session_ids()
            if (link := self._watched_link_of(session_id)) is not None and link.key == server
        ]

    def _has_work(self, server: ServerKey) -> bool:
        continuing = any(running.link.key == server for running in self._continuations())
        return continuing or bool(self._sessions_on(server))

    def _continuations(self) -> list[_RunningTurn]:
        """The answered Turns still waiting for a slot, in the order they started."""
        return [
            running
            for running in self._running.values()
            if running.slot is None
            and running.answer is not None
            and running.cancellation.reason is None
        ]

    def _tell_notices(self, running: _RunningTurn) -> None:
        """Show the Notices a Step that just ended sets off for its Session."""
        session_id = running.session.id
        state = self._states.get(running.link.key)
        limit = state.props.slot_context if isinstance(state, ServerReady) else None
        event = NoticeEvent(session_id, context_tokens(running.streamed.steps), limit)

        def notify(kind: str, title: str, text: str) -> None:
            fields = {"kind": kind, "title": title, "text": text}
            self._reporter.record(session_id, "notice", f"notice: {title}", **fields)
            self._boxes.notify(session_id, title, text, kind)

        for failure in self._notices.happened(event, running.session.settings, notify):
            self._reporter.error(failure, session_id)

    def _answered(self, running: _RunningTurn, answer: bool) -> None:
        running.asking = None
        running.answer = answer
        self._start_turns()

    def _resume_turn(self, running: _RunningTurn, slot: SlotLock) -> None:
        answer, running.answer, running.slot = running.answer, None, slot
        running.progress_at = self._clock()
        assert answer is not None
        running.job.confirmation.resume(answer)

    def _withdraw(self, running: _RunningTurn) -> None:
        """Its Confirmation is no longer valid: nobody answers it."""
        if running.asking is not None:
            self._boxes.withdraw(running.asking)
            running.asking = None

    def _waiting_entry(self, link: Link) -> ChatAppend:
        text = _waiting_text(link, self._states.get(link.key))
        return ChatAppend(role="info", label="queue", text=text)

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


def _reason(error: AllInError) -> JsonValue:
    """Why a cancelled Turn was cancelled; ``None`` for any other error."""
    return error.fields.get("reason") if isinstance(error, TurnCancelled) else None
