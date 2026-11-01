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

import itertools
import os
import time
from collections.abc import Callable, Container
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import JsonValue

from allin.core.cut import CutReason
from allin.core.errors import (
    AllInError,
    LlmUnavailable,
    PromptTooBig,
    StepStalled,
    TurnCancelled,
    TurnCrashed,
)
from allin.core.ids import BoxId, MainId, ServerKey, SessionId, SupervisorName
from allin.core.slot_locks import SlotLock
from allin.core.wait_list import Reservation, WaitClass, Waiter, WaitList, WaitListView
from allin.ipc.messages import ChatAppend, InputRestore, TurnState
from allin.main.admission import Waiting, admit, server_waiting
from allin.main.boxes import MainBoxes
from allin.main.chat_rebuild import cut_entry, dropped_entry, error_entry, tokens_entry
from allin.main.compaction import wants_compaction
from allin.main.compactions import Compactions
from allin.main.conversation import Conversation, Item, Step, Turn
from allin.main.cut_turns import cut_mark, cut_reason, output_cap
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
from allin.main.link_changes import LinkChange, failure_text, judge
from allin.main.links import Link, LinkResolver
from allin.main.notices import NoticeTypes
from allin.main.open_windows import OpenWindows, Streamed
from allin.main.prompts import Prompt, PromptQueue
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
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

    def wait_for_slot(self, server: ServerKey, slots: int, version: int) -> None:
        """Wait for one of those locks to be freed or the Server's Wait list to move past
        ``version``; then ``SlotFreed`` reaches the inbox."""
        ...

    def wait_list(self, server: ServerKey) -> WaitList:
        """The Server's Wait list, shared by every Main that uses it (ADR-0022)."""
        ...

    def measure(
        self, server: ServerKey, session_id: SessionId, ticket: int, items: list[Item]
    ) -> None:
        """Measure a Step's input on the Server; then ``InputMeasured`` reaches the inbox."""
        ...

    def recheck(self, server: ServerKey) -> None:
        """Make its watch report what it sees next, even if it looks unchanged."""
        ...

    def wake(self, name: SupervisorName, human: bool = False) -> bool:
        """Start an asleep Supervisor; whether it did. Only a ``human`` wakes one a human
        put to sleep."""
        ...

    def sleep(self, name: SupervisorName) -> None:
        """Put a Supervisor to sleep for a human: drained first if it runs (ADR-0022)."""
        ...

    def restart(self, name: SupervisorName, now: bool) -> None:
        """Ask a running Supervisor to restart its Server (``SupervisorNotRunning`` if not)."""
        ...


_RESUMED_TEXT = "queue resumed: {server} is ready"
# Why Main cancels a Turn's Step itself: a restart with --now (repeated after it, ADR-0023) or
# no progress for ``step.stall`` seconds (it fails).
RESTARTING = "the Server restarts now"
STALLED = "the Step made no progress"
_SUSPENDED_TEXT = (
    "turn suspended: {why}; what its Step gave is dropped and the Step runs again once the"
    " Server is back"
)
_MAX_FALLS = 2  # the same Step left half-done by this many falls in a row: its Turn fails


def _waiting_text(link: Link, state: ServerState | None) -> str:
    """Why the Prompts of the link's Sessions wait, for Main's Chat and their Windows."""
    server = link.describe()
    match state:
        case None:
            return f"queue paused until {server}'s /health is OK"
        case ServerLoading():
            return f"queue paused: {server} is loading its model"
        case ServerRestarting(sleep=True):
            return f"queue paused: {server} is going to sleep, as a human asked"
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
class _Measure:
    """The measure of a Session's next Prompt on a Server, asked or done (ADR-0004)."""

    prompt: Prompt
    server: ServerKey
    ticket: int
    done: bool = False
    tokens: int | None = None  # ``None`` once done: the Server could not measure it


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
    slot_context: int  # its Server's slot limit when it started
    output_cap: int  # the most each of its Steps may generate, sent with each
    need: int  # what its Step reserves of the pool: measured input plus Output cap
    context_before: int | None  # its Session's Context when it started
    cut: CutReason | None = None  # why its last Step was cut, if it was (ADR-0004)
    sent: bool = False  # whether its Step sent any stream event: until then it reads input
    streamed: Streamed = field(default_factory=Streamed)
    asking: BoxId | None = None  # the Confirmation it waits on, if any
    answer: bool | None = None  # its answer, while it waits for a slot to go on
    answered_at: float = 0.0  # wall clock: when it was answered, its place in the Wait list

    def release_slot(self) -> None:
        if self.slot is not None:
            self.slot.release()
            self.slot = None


@dataclass(frozen=True)
class _Candidate:
    """A Step ready to start but for its Server: a Continuation or a Session's next Prompt."""

    waiter: Waiter
    link: Link
    running: _RunningTurn | None = None
    prompt: Prompt | None = None
    session: Session | None = None


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
        patience: float,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """``patience``: seconds a waiting Prompt may be overtaken (``admission.patience``);
        ``wall_clock`` places each waiter in a Wait list other Mains read too."""
        self._main_id = main_id
        self._pid = os.getpid()
        self._stall = stall
        self._patience = patience
        self._clock = clock
        self._wall = wall_clock
        # Each Session's next Prompt as measured, or being measured (ADR-0004).
        self._measures: dict[SessionId, _Measure] = {}
        self._tickets = itertools.count(1)
        # When each Session's next Step started waiting for its Server, by the wall clock.
        self._since: dict[SessionId, float] = {}
        self._published: set[ServerKey] = set()  # Servers whose Wait list has waiters of ours
        self._shown_waiting: dict[SessionId, str] = {}  # why it waits, as its border says
        # What each Session's last Step left counting against its limit, for the Context Notice.
        self._used: dict[SessionId, int | None] = {}
        # How many falls in a row left each Session's current Step half-done (ADR-0023).
        self._falls: dict[SessionId, int] = {}
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
        self._paused = False  # nor while Main leaves its Profile (``/profile switch``)
        self._prompts = PromptQueue()
        self._running: dict[SessionId, _RunningTurn] = {}
        # Each Session's Enlace asked to change, until it does or fails (ADR-0022): its new
        # Prompts wait for it, so none starts on the old Server meanwhile.
        self._changes: dict[SessionId, LinkChange] = {}
        # Each Session's Compaction under way, a queue entry of its own (ADR-0017).
        self._compactions = Compactions(
            reporter,
            windows,
            boxes,
            sessions,
            sysprompts,
            servers.measure,
            self._tickets,
            self._conversation,
        )

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

    def compact(self, session: Session, instructions: str) -> None:
        """Queue a Compaction of the Session's Conversation (``/session compact``): it waits
        its turn after the Session's queued Prompts, and none of them starts while it lasts."""
        self._prompts.push(Prompt(session.id, instructions, "compact"))
        shown = "compaction queued"
        self._reporter.record(session.id, "compaction_asked", shown, instructions=instructions)
        self.send_turn_state(session.id)
        self._start_turns()

    def watch(self, link: Link) -> None:
        """Watch a Server even before any Session needs it: Main's default Enlace."""
        self._watch(link)

    def change_link(self, session: Session, link: Link) -> None:
        """Move the Session's Enlace to ``link`` once its Server is ready and the Session has no
        active Turn; if that Server cannot start, it stays where it is (ADR-0022). Asking again
        replaces the change, and asking for the current Enlace drops it."""
        session_id = session.id
        dropped = self._changes.pop(session_id, None)
        if link == self._link_or_none(session):
            if dropped is not None:
                shown = f"Enlace change to {dropped.to.describe()} dropped"
                self._tell_session(session_id, "link_change_dropped", shown)
            else:
                self._reporter.show(session_id, f"already linked to {link.describe()}")
            return
        self._changes[session_id] = LinkChange(session_id, link, self._clock())
        self._watch(link)
        shown = f"Enlace changes to {link.describe()} once it is ready and no Turn is active"
        self._tell_session(session_id, "link_change_asked", shown, server=link.key)
        if link.supervisor is not None and not self._ready(link.key):
            self._wake(link.supervisor)
        self._start_turns()

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
            running.sent = True

    def server_progress(self, server: ServerKey) -> None:
        """``server`` read more input: its Steps still reading theirs are not stalled. Which
        slot read it, llama does not say, so it counts for each of them."""
        for running in self._running.values():
            if running.link.key == server and not running.sent:
                running.progress_at = self._clock()

    def tick(self) -> None:
        """Cancel each Step that made no progress for ``step.stall`` seconds (ADR-0023)."""
        now = self._clock()
        for running in self._running.values():
            in_step = running.slot is not None and running.cancellation.reason is None
            if in_step and now - running.progress_at >= self._stall:
                running.cancellation.cancel(STALLED)
        if self._changes:  # a change waiting on what a Supervisor said before it ran
            self._start_turns()

    def set_stall(self, stall: float) -> None:
        self._stall = stall

    def set_patience(self, patience: float) -> None:
        """For the Prompts that start waiting from now on."""
        self._patience = patience

    def measured(
        self, server: ServerKey, session_id: SessionId, ticket: int, tokens: int | None
    ) -> None:
        """The Server measured the input of a Session's next Prompt, or something its
        Compaction asked."""
        if self._compactions.owns(session_id, ticket):
            self._compaction_measured(session_id, ticket, tokens)
            return
        measure = self._measures.get(session_id)
        if measure is None or measure.ticket != ticket or measure.server != server:
            return  # a stale measure: the Prompt or its Server changed since
        measure.done, measure.tokens = True, tokens
        if tokens is None:
            shown = "the Server could not measure the input: admitted by its slots only"
            self._reporter.log_session(session_id, "input_unmeasured", text=shown)
        self._start_turns()

    def output(self, session_id: SessionId, kind: OutputKind, text: str) -> None:
        running = self._running.get(session_id)
        if running is None:
            return
        running.progress_at = self._clock()
        if _compacting(running):  # its summary is shown once it is saved
            return
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
        running.cut = cut_reason(step, running.output_cap, running.slot_context)
        if _compacting(running):  # the Session's Context is not what its summary Step read
            return
        self._windows.show_context(running.session, running.streamed.steps)
        self._tell_notices(running)

    def turn_ended(
        self, session_id: SessionId, steps: list[Step] | None, error: AllInError | None
    ) -> None:
        running = self._running.pop(session_id, None)
        if running is None:
            return
        self._release(running)
        self._measures.pop(session_id, None)  # its Conversation changed
        self._withdraw(running)
        turn: int | None = None  # where it was saved, unless it was a summary Step
        try:
            if error is not None or steps is None:
                raise error or TurnCrashed(reason="no steps")
            if _compacting(running):  # its next part, or its check, goes first again
                self._compactions.step_done(session_id, steps, running.cut)
                self._prompts.push_front(running.prompt)
            else:
                turn = self._save_turn(running, steps)
        except AllInError as failure:
            if _reason(failure) == STALLED:
                self._stalled(running)
            elif not self._suspend(running, failure):
                self._falls.pop(session_id, None)
                self._fail_turn(running, failure)
            if isinstance(failure, LlmUnavailable):
                key = running.link.key
                if self._ready(key):  # Turns running at once all fail when the server dies
                    self._server_changed(key, ServerDown())
                self._servers.recheck(key)  # it may have looked ready to the watch all along
                if not self._prompts.has(session_id):  # else the change or ``push`` told it
                    self._windows.send(session_id, self._waiting_entry(running.link))
        else:
            self._falls.pop(session_id, None)
            if turn is not None:
                self._turn_finished(running, turn, steps)
        self.send_turn_state(session_id)
        self._start_turns()

    def _turn_finished(self, running: _RunningTurn, turn: int, steps: list[Step]) -> None:
        """Tell the Turn ended and what it cost; a cut one also with its mark (ADR-0004)."""
        session_id, cut = running.session.id, running.cut
        if (tokens := tokens_entry(steps)) is not None:
            self._windows.send(session_id, tokens)
        if cut is None:
            self._reporter.record(session_id, "turn_finished", "turn finished", turn=turn)
            return
        shown = f"turn finished, {cut_mark(cut)}"
        self._reporter.record(session_id, "turn_finished", shown, turn=turn, cut=cut)
        self._windows.send(session_id, cut_entry(cut))

    def cancel(self, session_id: SessionId, reason: str) -> None:
        """Drop a Session's queued Prompts and stop its running Turn.

        The Turn stays active until its executor reports it ended, as ``turn_cancelled`` unless
        it had already finished; either way the Conversation only ever gets whole Turns.
        """
        self._falls.pop(session_id, None)
        self._measures.pop(session_id, None)
        self._since.pop(session_id, None)
        self._compactions.drop(session_id)
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
        self._release(running)
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

    def pause(self) -> None:
        """No Turn starts until ``resume``; running ones go on (``/profile switch``)."""
        self._paused = True
        self._publish([])  # nothing of ours waits in any Wait list meanwhile

    def resume(self) -> None:
        """Turns start again, and each Session with queued Prompts is told it has work."""
        self._paused = False
        for session_id in self._prompts.session_ids():
            self.send_turn_state(session_id)
        self._start_turns()

    def has_running(self) -> bool:
        """Whether any Turn is active, one waiting for its Confirmation included."""
        return bool(self._running)

    def running_count(self) -> int:
        return len(self._running)

    def cancel_turns(self, reason: str) -> list[Prompt]:
        """Stop every running Turn and keep the queue (``/profile switch --now``); the
        Prompts of those that were a Prompt's Turn, which go back to their input.

        Each Turn stays active until its executor reports it ended, as with ``cancel``.
        """
        stopped: list[Prompt] = []
        for running in self._running.values():
            if running.cancellation.reason is not None:
                continue
            running.cancellation.cancel(reason)
            self._withdraw(running)
            if not _compacting(running):
                stopped.append(running.prompt)
        return stopped

    def take_queue(self, keep: Container[SessionId]) -> list[Prompt]:
        """Take out the waiting Prompts of every Session but those in ``keep``, as they are:
        the Profile Main leaves keeps them, and a Session both Profiles share goes on."""
        taken = [prompt for prompt in self._prompts.all() if prompt.session_id not in keep]
        for session_id in dict.fromkeys(prompt.session_id for prompt in taken):
            self._prompts.drop(session_id)
            self._measures.pop(session_id, None)
            self._since.pop(session_id, None)
            self._shown_waiting.pop(session_id, None)
            self._compactions.drop(session_id)
            self.send_turn_state(session_id)
        return taken

    def use_sysprompts(self, sysprompts: Path) -> None:
        """The Sysprompts of the Profile Main entered, for what it renders from now on."""
        self._sysprompts = sysprompts
        self._compactions.use_sysprompts(sysprompts)

    def cancel_running(self) -> None:
        """On shutdown: cancel every running Turn.

        The Turns are ended here, as cancelled, since no event is handled after this. Their
        Prompts and the queue stay in the saved MainState, for the next start to give back.
        """
        for running in self._running.values():
            running.cancellation.cancel(SHUTTING_DOWN)
            self._release(running)
            self._withdraw(running)
            self._fail_turn(running, TurnCancelled(reason=SHUTTING_DOWN))
        self._running.clear()

    def send_turn_state(self, session_id: SessionId) -> None:
        """Tell the Session's Window whether it has a Turn running or waiting, and why its next
        Step waits, if it does."""
        active = session_id in self._running or self._prompts.has(session_id)
        waiting = self._shown_waiting.get(session_id) if active else None
        if waiting is None and self._compactions.busy(session_id):
            waiting = "compacting…"
        self._windows.send(session_id, TurnState(active=active, waiting=waiting))

    def streamed(self, session_id: SessionId) -> Streamed | None:
        """What the Session's running Turn has given so far; ``None`` if it has none."""
        running = self._running.get(session_id)
        return None if running is None else running.streamed

    def queued(self) -> list[Prompt]:
        """Every waiting Prompt, in queue order; a Compaction whose summary Step runs is
        first, so a restart does it again."""
        running = [r.prompt for r in self._running.values() if _compacting(r)]
        return [*running, *self._prompts.all()]

    def running_prompts(self) -> list[Prompt]:
        """The Prompts of the running Turns that a restart should give back."""
        # A Turn cancelled because its Window closed must not come back as a draft.
        return [
            running.prompt
            for running in self._running.values()
            if running.cancellation.reason is None and not _compacting(running)
        ]

    def _start_turns(self) -> None:
        """Hand Turns to executors while there is room (ADR-0004, ADR-0022).

        Room is this Main's ``parallel``, summing every Server; on each Server, a free slot
        lock, which Mains sharing it compete for (ADR-0008), and the Admission by its Wait list
        (``allin.main.admission``). A Server that is not ready, or full, holds back only its own
        Sessions.
        """
        self._judge_changes()
        waiting: dict[SessionId, Waiting] = {}
        while not self._stopping and not self._paused and self._start_one(waiting):
            waiting = {}
        self._show_waiting(waiting)
        self._show_pools()

    def _room_in_main(self) -> bool:
        holding = sum(running.slot is not None for running in self._running.values())
        return self._parallel is None or holding < self._parallel

    def _start_one(self, waiting: dict[SessionId, Waiting]) -> bool:
        """Start one answered Turn or new Prompt; ``False`` if none can start now. ``waiting``
        gets why each of the others waits."""
        candidates = self._candidates(waiting)
        if candidates is None:  # one failed before it could wait: look again
            return True
        room = self._room_in_main()
        views = self._publish(candidates if room else [])
        full: set[ServerKey] = set()  # no slot there now: what waits for it waits on
        for candidate in candidates:
            session_id, key = candidate.waiter.session, candidate.link.key
            if not room or key in full:
                waiting[session_id] = Waiting("slot")
                continue
            view = views[key]
            admission = admit(candidate.waiter, view, self._pool(key), self._wall())
            if admission.verdict != "go":
                reason = "queued" if admission.verdict == "queued" else "pool"
                waiting[session_id] = Waiting(reason, admission.position)
                self._wait_on(key, view.version)
                continue
            slot = self._slot_on(key, full, view.version)
            if slot is None:
                waiting[session_id] = Waiting("slot")
                continue
            self._start_candidate(candidate, slot)
            return True
        return False

    def _candidates(self, waiting: dict[SessionId, Waiting]) -> list[_Candidate] | None:
        """The Steps ready to start but for their Server, Continuations first, then each
        Session's next Prompt in queue order; ``None`` if one failed on the way. ``waiting``
        gets why the others cannot even wait for a slot yet."""
        candidates: list[_Candidate] = []
        for running in self._continuations():
            if not self._ready(running.link.key):
                waiting[running.session.id] = self._server_waiting(running.link.key)
                continue
            since = running.answered_at
            waiter = self._waiter(running.session.id, "continuation", since, running.need)
            candidates.append(_Candidate(waiter, running.link, running=running))
        for session_id in self._changes.keys() - self._falls.keys():
            if self._prompts.has(session_id) and session_id not in self._running:
                waiting[session_id] = Waiting("link")
        # A Session whose Enlace changes waits for it, but a suspended Turn repeats its Step.
        held = self._changes.keys() - self._falls.keys()
        seen: set[SessionId] = set()
        for prompt in self._prompts.waiting(active=self._running.keys() | held):
            if prompt.session_id in seen:  # only a Session's next Prompt may start
                continue
            seen.add(prompt.session_id)
            try:
                candidate = self._prompt_candidate(prompt, waiting)
            except AllInError as error:
                self._drop_prompt(prompt, error)
                return None
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _prompt_candidate(
        self, prompt: Prompt, waiting: dict[SessionId, Waiting]
    ) -> _Candidate | None:
        """The Prompt as a candidate; ``None`` while its Server is not ready or its input is
        measured. Raises ``PromptTooBig`` if it would not fit even in an empty slot."""
        session_id = prompt.session_id
        session = self._sessions.load(session_id)
        link = self._links.resolve(session.settings)
        self._watch(link)  # its Enlace may be new since it was queued
        state = self._states.get(link.key)
        if not isinstance(state, ServerReady):
            waiting[session_id] = self._server_waiting(link.key)
            return None
        slot_context = state.props.slot_context
        need = self._need(prompt, session, link, slot_context)
        if need is None:  # measured in a moment: nothing to tell meanwhile
            return None
        if self._compacts_first(prompt, session, need, slot_context):
            entry = Prompt(session_id, "", "auto_compact")
            self._prompts.insert_before(prompt, entry)
            return self._prompt_candidate(entry, waiting)
        if need > slot_context:
            raise PromptTooBig(need=need, limit=slot_context)
        repeats = session_id in self._falls or self._compactions.continuing(session_id)
        kind: WaitClass = "continuation" if repeats else "prompt"
        since = self._since.setdefault(session_id, self._wall())
        waiter = self._waiter(session_id, kind, since, need)
        return _Candidate(waiter, link, prompt=prompt, session=session)

    def _need(self, prompt: Prompt, session: Session, link: Link, slot_context: int) -> int | None:
        """What the Prompt's first Step needs: its input, as the Server measures it, plus its
        Output cap; ``None`` until measured, which this asks for. For a Compaction, what its
        next summary Step needs."""
        if prompt.kind != "prompt":
            cap = output_cap(session.settings.sampling, slot_context)
            return self._compactions.need(prompt, session, link.key, slot_context, cap)
        measure = self._measures.get(session.id)
        if measure is None or measure.prompt != prompt or measure.server != link.key:
            items = self._conversation(session).request_input(prompt.text)
            ticket = next(self._tickets)
            self._measures[session.id] = _Measure(prompt, link.key, ticket)
            self._servers.measure(link.key, session.id, ticket, items)
            return None
        if not measure.done:
            return None
        return (measure.tokens or 0) + output_cap(session.settings.sampling, slot_context)

    def _compacts_first(
        self, prompt: Prompt, session: Session, need: int, slot_context: int
    ) -> bool:
        """Whether the Prompt's Turn compacts first, as its first Step: it needs more than
        ``compact.threshold`` of the slot, never counting what other Sessions take of a pool,
        and it was not tried for it yet (ADR-0017)."""
        if prompt.kind != "prompt" or self._compactions.tried(session.id):
            return False
        compact = session.settings.compact
        return wants_compaction(compact, need, slot_context) and self._conversation(session).started

    def _compacts_alone(self, session_id: SessionId) -> bool:
        """Whether the Session has ``compact.auto`` on; else ``/session compact`` is up to a
        human, which a Prompt too big is told."""
        try:
            return self._sessions.load(session_id).settings.compact.auto
        except AllInError:
            return True

    def _waiter(self, session_id: SessionId, kind: WaitClass, since: float, need: int) -> Waiter:
        return Waiter(
            main=self._main_id,
            pid=self._pid,
            session=session_id,
            kind=kind,
            since=since,
            need=need,
            firm_at=since + self._patience,
        )

    def _drop_prompt(self, prompt: Prompt, error: AllInError) -> None:
        """A Prompt that cannot start: it leaves the queue, and back to its input if it never
        waited for its Server, since it is in no Conversation."""
        session_id = prompt.session_id
        self._prompts.remove(prompt)
        self._measures.pop(session_id, None)
        if prompt.kind != "prompt":  # its Prompt, if it compacted for one, goes on
            self._compactions.failed(prompt, error)
            self.send_turn_state(session_id)
            return
        self._since.pop(session_id, None)
        self._compactions.forget(session_id)
        self._windows.show_error(error, session_id)
        if isinstance(error, PromptTooBig):
            self._windows.send(session_id, InputRestore(text=prompt.text))
            if not self._compacts_alone(session_id):
                hint = f"/session compact {session_id} may make room for it"
                self._windows.send(session_id, ChatAppend(role="info", label="context", text=hint))
        self.send_turn_state(session_id)

    def _publish(self, candidates: list[_Candidate]) -> dict[ServerKey, WaitListView]:
        """Put this Main's waiters in their Servers' Wait lists, taking out the ones that no
        longer wait; each list as it is after."""
        by_server: dict[ServerKey, list[Waiter]] = {key: [] for key in self._published}
        for candidate in candidates:
            by_server.setdefault(candidate.link.key, []).append(candidate.waiter)
        views = {
            key: self._servers.wait_list(key).publish(self._main_id, self._pid, waiters)
            for key, waiters in by_server.items()
        }
        self._published = {key for key, waiters in by_server.items() if waiters}
        return views

    def _pool(self, server: ServerKey) -> int | None:
        """The Server's shared pool, in tokens; ``None`` if its slots do not share one."""
        state = self._states.get(server)
        if not isinstance(state, ServerReady) or not state.shared_pool:
            return None
        return state.props.slot_context if state.props.total_slots > 1 else None

    def _wait_on(self, server: ServerKey, version: int) -> None:
        state = self._states.get(server)
        if isinstance(state, ServerReady):
            self._servers.wait_for_slot(server, state.props.total_slots, version)

    def _slot_on(self, server: ServerKey, full: set[ServerKey], version: int) -> SlotLock | None:
        """A slot of ``server``, which is ready, if it has one free; else ``server`` joins
        ``full`` until the next look."""
        state = self._states.get(server)
        assert isinstance(state, ServerReady)  # only a ready Server's Steps are candidates
        slots = state.props.total_slots
        holding = sum(
            running.slot is not None and running.link.key == server
            for running in self._running.values()
        )
        slot = self._servers.take_slot(server, slots) if holding < slots else None
        if slot is None:
            full.add(server)
            if holding < slots:  # another Main holds it
                self._servers.wait_for_slot(server, slots, version)
        return slot

    def _start_candidate(self, candidate: _Candidate, slot: SlotLock) -> None:
        if candidate.running is not None:
            self._resume_turn(candidate.running, slot)
            return
        assert candidate.prompt is not None and candidate.session is not None
        self._prompts.remove(candidate.prompt)
        self._measures.pop(candidate.waiter.session, None)
        if candidate.prompt.kind == "prompt":  # a Compaction keeps its Turn's place
            self._since.pop(candidate.waiter.session, None)
            self._compactions.forget(candidate.waiter.session)
        self._start_prompt(
            candidate.prompt, candidate.session, candidate.link, slot, candidate.waiter.need
        )

    def _reserve(self, running: _RunningTurn) -> None:
        reservation = Reservation(
            main=self._main_id, pid=self._pid, session=running.session.id, tokens=running.need
        )
        self._servers.wait_list(running.link.key).reserve(reservation)

    def _release(self, running: _RunningTurn) -> None:
        """Its Step ended or paused: its slot and what it reserved of the pool are free."""
        if running.slot is not None:
            self._servers.wait_list(running.link.key).release(
                self._main_id, self._pid, running.session.id
            )
        running.release_slot()

    def _server_waiting(self, server: ServerKey) -> Waiting:
        return server_waiting(self._states.get(server)) or Waiting("slot")

    def _show_waiting(self, waiting: dict[SessionId, Waiting]) -> None:
        """Tell each Window whose reason to wait changed."""
        texts = {session_id: why.text() for session_id, why in waiting.items()}
        for session_id in self._shown_waiting.keys() | texts.keys():
            text = texts.get(session_id)
            if self._shown_waiting.get(session_id) == text:
                continue
            if text is None:
                del self._shown_waiting[session_id]
            else:
                self._shown_waiting[session_id] = text
            self.send_turn_state(session_id)

    def _show_pools(self) -> None:
        """Tell the Windows on a shared pool whether another Session's Step uses it now."""
        for server in self._watched:
            if self._pool(server) is None:
                continue
            in_course = frozenset(
                r.session for r in self._servers.wait_list(server).look().reserved
            )
            self._windows.set_in_course(server, in_course)

    def _start_prompt(
        self, prompt: Prompt, session: Session, link: Link, slot: SlotLock, need: int
    ) -> None:
        try:
            conversation = self._conversation(session)
        except AllInError as error:
            slot.release()
            self._windows.show_error(error, prompt.session_id)
            self.send_turn_state(prompt.session_id)
            return
        state = self._states.get(link.key)
        assert isinstance(state, ServerReady)  # a slot is only taken on a ready Server
        slot_context = state.props.slot_context
        cap = output_cap(session.settings.sampling, slot_context)
        compacting = prompt.kind != "prompt"
        request = TurnRequest(
            model=session.settings.model,
            input=self._compactions.request(session.id)
            if compacting
            else conversation.request_input(prompt.text),
            params={**session.settings.sampling.request_params(), "max_output_tokens": cap},
        )
        job = TurnJob(session.id, request, link.base_url)
        auto_confirm = self._devastation.covers(session.settings)
        self._running[session.id] = _RunningTurn(
            prompt,
            session,
            link,
            job.cancellation,
            job,
            slot,
            auto_confirm,
            self._clock(),
            slot_context=slot_context,
            output_cap=cap,
            need=need,
            context_before=context_tokens(conversation.turns[-1].steps)
            if conversation.turns
            else None,
        )
        self._reserve(self._running[session.id])
        if compacting:
            shown = "summary step started"
            self._reporter.record(session.id, "compaction_step_started", shown, need=need)
            self.send_turn_state(session.id)  # its border says so while the Step runs
        else:
            self._reporter.record(session.id, "turn_started", "turn started", need=need)
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
        lowered slot limit (ADR-0022); each compacts in parts before its next Turn (ADR-0017)."""
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

    def _suspend(self, running: _RunningTurn, failure: AllInError) -> bool:
        """Suspend the Turn whose Step a fall or a restart of its Server left half-done: it goes
        back first in the queue, to run that Step again once the Server is back (ADR-0023).
        ``False`` if it must fail instead: not a fall or a restart, or the same Step left
        half-done by two falls in a row."""
        restart = _reason(failure) == RESTARTING
        if not (restart or isinstance(failure, LlmUnavailable)):
            return False
        session_id = running.session.id
        streamed = running.streamed
        fell_in_it = not restart and failure.fields.get("reached") is True
        falls = self._falls.get(session_id, 0) + (1 if fell_in_it else 0)
        if falls >= _MAX_FALLS:
            self._falls.pop(session_id, None)
            return False
        self._falls[session_id] = falls
        why = "the Server restarted" if restart else "the Server fell"
        shown = _SUSPENDED_TEXT.format(why=why)
        fields = {"error": str(failure), "reasoning": streamed.reasoning, "answer": streamed.answer}
        self._reporter.record(session_id, "turn_suspended", shown, **fields)
        self._windows.send(session_id, ChatAppend(role="info", label="server", text=shown))
        self._prompts.push_front(running.prompt)
        return True

    def _stalled(self, running: _RunningTurn) -> None:
        """No progress for ``step.stall`` seconds: the Step fails, without repeating, since it
        would stall again on the same Server (ADR-0023)."""
        session_id = running.session.id
        self._falls.pop(session_id, None)
        self._fail_turn(running, StepStalled(seconds=self._stall))
        title = "Step stalled"  # Main names the Session in front, the Window is its own
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
        link = self._link_or_none(session)
        if link is not None:
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
        changing = any(change.to.key == server for change in self._changes.values())
        return continuing or changing or bool(self._sessions_on(server))

    def _judge_changes(self) -> None:
        """Apply or drop each pending Enlace change that can be decided now."""
        now = self._clock()
        for session_id, change in list(self._changes.items()):
            state = self._states.get(change.to.key)
            # A Turn suspended to repeat its Step is still active: it repeats on the old model.
            busy = session_id in self._running or session_id in self._falls
            verdict, self._changes[session_id] = judge(change, state, busy, now)
            if verdict == "wait":
                continue
            del self._changes[session_id]
            try:
                session = self._sessions.load(session_id)
                match verdict:
                    case "apply":
                        self._apply_change(session, change.to)
                    case "fail":
                        shown = failure_text(change, state, self._link_or_none(session))
                        self._tell_session(session_id, "link_change_failed", shown)
            except AllInError as error:
                self._windows.show_error(error, session_id)

    def _apply_change(self, session: Session, link: Link) -> None:
        """Save the Session's new Enlace, show its Window the new limit, and give the Notice
        if its Context no longer fits (ADR-0022)."""
        settings = session.settings.model_copy(update={"server": link.supervisor})
        saved = self._sessions.save_settings(session, settings)
        shown = f"Enlace changed to {link.describe()}"
        self._tell_session(saved.id, "link_changed", shown, server=link.key)
        conversation = self._sessions.load_conversation(saved)
        steps = conversation.turns[-1].steps if conversation and conversation.turns else []
        self._windows.show_context(saved, steps)
        state = self._states.get(link.key)
        context = context_tokens(steps)
        if isinstance(state, ServerReady) and context is not None:
            limit = state.props.slot_context
            if context > limit:
                title = f"{link.describe()}: context limit is {limit} tokens"
                text = f"the Conversation already holds {context}: over the limit"
                self._boxes.notify(saved.id, title, text, "context_limit")

    def _link_or_none(self, session: Session) -> Link | None:
        try:
            return self._links.resolve(session.settings)
        except AllInError:
            return None

    def _tell_session(
        self, session_id: SessionId, event: str, shown: str, **fields: JsonValue
    ) -> None:
        """Tell Main's Chat, the Logs and the Session's Window."""
        self._reporter.record(session_id, event, shown, **fields)
        self._windows.send(session_id, ChatAppend(role="info", label="server", text=shown))

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
        context = context_tokens(running.streamed.steps)
        shared = self._pool(running.link.key) is not None
        # With a shared pool, what the Steps of other Sessions reserve counts too.
        others = self._others_reserved(running) if shared else 0
        used = None if context is None else context + others
        before = self._used.get(session_id, running.context_before)
        self._used[session_id] = used
        # The limit its Steps ran with, which a Server restarted meanwhile may have changed.
        event = NoticeEvent(
            session_id,
            context,
            running.slot_context,
            running.cut,
            running.output_cap,
            used=used,
            used_before=before,
            shared=shared,
            compacts=running.session.settings.compact.auto,
        )

        def notify(kind: str, title: str, text: str) -> None:
            fields = {"kind": kind, "title": title, "text": text}
            self._reporter.record(session_id, "notice", f"notice: {title}", **fields)
            self._boxes.notify(session_id, title, text, kind)

        for failure in self._notices.happened(event, running.session.settings, notify):
            self._reporter.error(failure, session_id)

    def _others_reserved(self, running: _RunningTurn) -> int:
        """What the Steps in course of the other Sessions on its Server reserve."""
        mine = (self._main_id, self._pid, running.session.id)
        reserved = self._servers.wait_list(running.link.key).look().reserved
        return sum(r.tokens for r in reserved if (r.main, r.pid, r.session) != mine)

    def _answered(self, running: _RunningTurn, answer: bool) -> None:
        running.asking = None
        running.answer = answer
        running.answered_at = self._wall()
        self._start_turns()

    def _resume_turn(self, running: _RunningTurn, slot: SlotLock) -> None:
        answer, running.answer, running.slot = running.answer, None, slot
        self._reserve(running)
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

    def _compaction_measured(self, session_id: SessionId, ticket: int, tokens: int | None) -> None:
        """The Server measured something a Compaction asked; once it ends, saved or not, its
        entry leaves the queue."""
        entry = self._compactions.entry(session_id)
        try:
            over = self._compactions.measured(session_id, ticket, tokens)
        except AllInError as error:
            self._prompts.remove(entry)
            self._compactions.failed(entry, error)
            over = True
        else:
            if over:
                self._prompts.remove(entry)
                self._used.pop(session_id, None)  # the Context Notice may show again
        if over:
            self._measures.pop(session_id, None)
            self.send_turn_state(session_id)
        self._start_turns()

    def _fail_turn(self, running: _RunningTurn, error: AllInError) -> None:
        session_id = running.session.id
        if _compacting(running):  # nothing changed: its Prompt, if any, waits in the queue
            self._compactions.failed(running.prompt, error)
            return
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
        turn = Turn(prompt=running.prompt.text, steps=steps, cut=running.cut)
        self._sessions.save_conversation(running.session, conversation.with_turn(turn))
        return conversation.next_turn


def _compacting(running: _RunningTurn) -> bool:
    """Whether its Step is a Compaction's summary Step."""
    return running.prompt.kind != "prompt"


def _reason(error: AllInError) -> JsonValue:
    """Why a cancelled Turn was cancelled; ``None`` for any other error."""
    return error.fields.get("reason") if isinstance(error, TurnCancelled) else None
