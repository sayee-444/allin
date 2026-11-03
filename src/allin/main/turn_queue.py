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

import functools
import itertools
import json
import os
import time
from collections.abc import Callable, Container
from dataclasses import dataclass, field, replace
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
    TurnYielded,
)
from allin.core.ids import (
    BoxId,
    MainId,
    PromptId,
    ServerKey,
    SessionId,
    SubscriptionId,
    SupervisorName,
    new_prompt_id,
)
from allin.core.origin import HUMAN, Origin, from_session
from allin.core.settings import Sampling
from allin.core.slot_locks import SlotLock
from allin.core.wait_list import Reservation, WaitClass, Waiter, WaitList, WaitListView
from allin.ipc.messages import ChatAppend, InputRestore, TurnState
from allin.main.admission import Waiting, admit, server_waiting
from allin.main.attachments import AttachmentReader
from allin.main.boxes import MainBoxes
from allin.main.chat_rebuild import (
    FAILED_BEFORE,
    cut_entry,
    dropped_entry,
    error_entry,
    tokens_entry,
    tool_failed_entry,
    tools_entry,
)
from allin.main.compaction import summary_text, wants_compaction
from allin.main.compactions import Compacted, CompactionDone, Compactions
from allin.main.context_notice import CONTEXT as CONTEXT_NOTICE
from allin.main.context_notice import context_left
from allin.main.conversation import (
    TOOL_ERROR,
    Conversation,
    Item,
    Step,
    Turn,
    interjected_in,
)
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
from allin.main.notifications import CONTEXT_LEFT, NOTHING_TOLD, Notifications, Told
from allin.main.open_windows import OpenWindows, Streamed
from allin.main.prompts import AutoConfirm, Prompt, PromptQueue
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.slots import ServerProps, capacity
from allin.main.status import InActive, QueuedSession, QueueView, ServerStateName, WatchedServer
from allin.main.sysprompt import render_sysprompt
from allin.main.tool_steps import SessionTools, ToolGate, call_output
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
INTERRUPTED = "interrupted with Shift+Enter"
# What the LLM reads as the output of a call whose Turn a human interrupted (ADR-0024).
CUT_CALL = (
    f"{TOOL_ERROR}the human interrupted the Turn (Shift+Enter) before this call's output was "
    "read: it was stopped, and may have done part or all of its work"
)
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
    # What its tools' schemas take of each Step's input at most, a character each: ``/tokenize``
    # does not read them (#106).
    schemas: int = 0


@dataclass
class _RunningTurn:
    prompt: Prompt
    session: Session
    link: Link  # where its Steps go, from when it started
    cancellation: Cancellation
    job: TurnJob
    # Held while its Steps run; ``None`` while it waits for a Confirmation's answer.
    slot: SlotLock | None
    # Who answers its Confirmations instead of a human, if anyone: ``-y`` on its Prompt, or
    # ``--devastation`` covering its chain as it was when it started, so changing it, or an
    # Exemption, only reaches the next Turn.
    auto_confirm: AutoConfirm | None
    progress_at: float  # when its Step last showed progress: started, or a stream event
    slot_context: int  # its Server's slot limit when it started
    output_cap: int  # the most each of its Steps may generate, sent with each
    need: int  # what its Step reserves of the pool: measured input plus Output cap
    context_before: int | None  # its Session's Context when it started
    grown: int = 0  # what its tools added to ``need`` since it started
    # Whether the Context Notice showed after one of its Steps, and the last Step's figures.
    context_noticed: bool = False
    notice_event: NoticeEvent | None = None
    cut: CutReason | None = None  # why its last Step was cut, if it was (ADR-0004)
    # Shift+Enter cut it: once its executor stops, it is saved with what it streamed.
    interrupted: bool = False
    # The Step whose tools run, then with what they gave back and the Textos intercalados
    # that entered after them, until its executor says that Step ended (ADR-0024).
    tools_step: Step | None = None
    notifications: list[str] = field(default_factory=list)  # told the LLM ahead of its Prompt
    told: Told = NOTHING_TOLD  # the pending Notificaciones among them (#109)
    # Where its current Step's text starts in ``streamed``: (reasoning, answer).
    step_from: tuple[int, int] = (0, 0)
    sent: bool = False  # whether its Step sent any stream event: until then it reads input
    streamed: Streamed = field(default_factory=Streamed)
    asking: BoxId | None = None  # the Confirmation it waits on, if any
    # How it goes on once it holds a slot again, while it waits for one: a Confirmation was
    # answered, or the tools its Step called gave back what they said (a Continuation).
    resume: Callable[[], None] | None = None
    ready_at: float = 0.0  # wall clock: since when it waits so, its place in the Wait list

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


class Answers(Protocol):
    """Who hears that a Prompt's Turn answered it, or that it will not (#116)."""

    def answered(self, prompt: Prompt, turn: int, answer: str) -> None:
        """Its Turn was saved as ``turn``; ``answer``: what its last Step wrote."""
        ...

    def failed(self, prompt: Prompt, why: str) -> None:
        """Its Turn failed or was cancelled, and is in no Conversation."""
        ...

    def dropped(self, prompt: Prompt, why: str) -> None:
        """It left the queue before a Turn of it started."""
        ...


class _NoAnswers:
    def answered(self, prompt: Prompt, turn: int, answer: str) -> None:
        pass

    def failed(self, prompt: Prompt, why: str) -> None:
        pass

    def dropped(self, prompt: Prompt, why: str) -> None:
        pass


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
        tools_for: Callable[[Session], SessionTools | None] = lambda _: None,
        *,
        notifications: Notifications,
    ) -> None:
        """``patience``: seconds a waiting Prompt may be overtaken (``admission.patience``);
        ``wall_clock`` places each waiter in a Wait list other Mains read too."""
        self._main_id = main_id
        self._tools_for = tools_for  # each Turn's tools, as its Session has them when it starts
        self._notifications = notifications
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
        # The Prompt whose Turn each Session stopped mid-Turn to compact, until that served.
        self._yielded: dict[SessionId, PromptId] = {}
        self._paused = False  # nor while Main leaves its Profile (``/profile switch``)
        self._prompts = PromptQueue()
        self._running: dict[SessionId, _RunningTurn] = {}
        self._answers: Answers = _NoAnswers()
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

    def listen(self, answers: Answers, compacted: Compacted) -> None:
        """Who hears each Prompt's answer, and each Compactación done, from now on: the
        Suscripciones (#116)."""
        self._answers = answers

        def heard(done: CompactionDone) -> None:
            # The Context dropped: how much was left no longer holds (#120).
            self._notifications.withdraw(done.session_id, CONTEXT_LEFT)
            if self._yielded.pop(done.session_id, None) is not None:
                # Its Turn goes on reading the new summary, so it reads what is pending now
                # too, the live state among it: its next Step's input changed anyway.
                self._notifications.not_saved(done.session_id)
            compacted(done)

        self._compactions.on_compacted = heard

    def push(self, session: Session, prompt: Prompt) -> None:
        """Queue a Prompt the user sent, show it in its Window and start it if there is room.

        One sent while its Session is busy waits as queued: its Chat shows it only once its
        Turn starts (ADR-0024). A Suscripción's Prompt is a Continuation, so it goes first
        (ADR-0004). Each Prompt gets its id here, unless it came with one."""
        if not prompt.prompt_id:
            prompt = replace(prompt, prompt_id=new_prompt_id())
        busy = self._busy(session.id)
        if busy:
            prompt = replace(prompt, queued=True)
        if prompt.woke_by:
            self._prompts.push_front(prompt)
        else:
            self._prompts.push(prompt)
        fields = _commands(prompt)
        fields["prompt_id"] = prompt.prompt_id
        if busy:
            fields["queued"] = True
        fields["origin"] = list[JsonValue](prompt.origin.labels)
        self._reporter.log_session(session.id, "prompt", text=prompt.text, **fields)
        if not busy:
            self._show_prompt(session, prompt)
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

    def compact(self, session: Session, instructions: str, origin: Origin = HUMAN) -> None:
        """Queue a Compaction of the Session's Conversation (``/session compact``, or its own
        ``@compact``, with ``origin``): it waits its turn after the Session's queued Prompts,
        and none of them starts while it lasts."""
        self._prompts.push(Prompt(session.id, instructions, "compact", origin=origin))
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
        running.tools_step = None
        self._falls.pop(session_id, None)  # a fall from now on leaves another Step half-done
        running.step_from = (len(running.streamed.reasoning), len(running.streamed.answer))
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
        if isinstance(error, TurnYielded) and running.cancellation.reason is not None:
            error = TurnCancelled(reason=running.cancellation.reason)  # it lands first
        if not isinstance(error, TurnYielded) and self._yielded.get(session_id) == (
            running.prompt.prompt_id
        ):
            del self._yielded[session_id]  # its Turn is over
        self._release(running)
        self._measures.pop(session_id, None)  # its Conversation changed
        self._withdraw(running)
        turn: int | None = None  # where it was saved, unless it was a summary Step
        if running.interrupted and isinstance(error, TurnCancelled):
            steps, error, running.cut = _interrupted_steps(running), None, "interrupted"
            if running.tools_step is not None and not running.tools_step.results:
                # Its tools were cut: shown as its rebuilt Chat shows them.
                self._show_failed_tools(session_id, tuple(steps[-1].failures()))
        try:
            if error is not None or steps is None:
                raise error or TurnCrashed(reason="no steps")
            if _compacting(running):  # its next part, or its check, goes first again
                self._compactions.step_done(session_id, steps, running.cut)
                self._prompts.push_front(running.prompt)
            else:
                turn = self._save_turn(running, steps)
                self._notifications.saved(session_id, running.told)
        except AllInError as failure:
            # A suspended Turn keeps what it told: its Steps already read it (ADR-0023).
            if _reason(failure) == STALLED:
                self._untold(running)
                self._stalled(running)
            elif isinstance(failure, TurnYielded):
                self._yield(running)
            elif not self._suspend(running, failure):
                self._untold(running)
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
                self._tell_context_left(running)
                self._turn_finished(running, turn, steps)
                self._answers.answered(running.prompt, turn, summary_text(steps))
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
        """Drop a Session's queued Prompts, which go back to its Draft as blocks in order, and
        stop its running Turn.

        The Turn stays active until its executor reports it ended, as ``turn_cancelled`` unless
        it had already finished; either way the Conversation only ever gets whole Turns.
        """
        self._cancel(session_id, reason, by_human=False)

    def window_closed(self, session_id: SessionId) -> None:
        """Closing a Window cancels what a human asked there: its queued Prompts of a human
        Origin, and its running Turn only if its Prompt has one. For any other Origin the Window
        was a peephole, and the work goes on (ADR-0013)."""
        self._cancel(session_id, "window closed", by_human=True)

    def _cancel(self, session_id: SessionId, reason: str, by_human: bool) -> None:
        self._falls.pop(session_id, None)
        dropped = self._drop_queued(session_id, reason, by_human)
        typed = [prompt.as_typed for prompt in dropped if prompt.kind == "prompt"]
        for text in reversed(typed):  # each one put back goes ahead of the one after it
            self._windows.put_back(session_id, text)
        running = self._running.get(session_id)
        if running is not None and (not by_human or running.prompt.origin.is_human):
            running.cancellation.cancel(reason)
            self._withdraw(running)

    def interrupt(self, session_id: SessionId) -> None:
        """Shift+Enter: cut the Session's running Turn now. Once its executor stops it is saved
        with what it streamed, as a Turn cut by ``interrupted`` (ADR-0024)."""
        running = self._running.get(session_id)
        if running is None or _compacting(running):
            return
        running.interrupted = True
        running.cancellation.cancel(INTERRUPTED)
        self._withdraw(running)

    def queued_count(self, session_id: SessionId) -> int:
        return len(self._prompts.of(session_id))

    def mark_typed_in_window(self, session_id: SessionId) -> None:
        """The Session's last queued Prompt was typed in its Window: if it may, it enters the
        Session's Turn under way before its next Step (ADR-0024)."""
        self._prompts.mark_typed_in_window(session_id)

    def jump(self, session_id: SessionId) -> None:
        """The Session's last queued Prompt runs before the others it has waiting."""
        self._prompts.jump_last(session_id)
        self._measures.pop(session_id, None)
        self.send_turn_state(session_id)
        self._start_turns()

    def withdraw(self, session_id: SessionId) -> None:
        """Up: the Session's last queued Prompt leaves the queue and goes back to its input,
        as it was typed; nothing if none waits as queued."""
        prompt = self._prompts.take_last(session_id)
        if prompt is None:
            return
        self._measures.pop(session_id, None)
        if not self._prompts.has(session_id):
            self._since.pop(session_id, None)
        self._reporter.log_session(session_id, "prompt_withdrawn", text=prompt.text)
        self._windows.put_back(session_id, prompt.as_typed)
        self.send_turn_state(session_id)
        self._start_turns()

    def confirmation_requested(
        self, session_id: SessionId, title: str, text: str, inside: bool = False
    ) -> None:
        """A running Turn asks yes or no: it gives its slot back while a human answers
        (ADR-0009), so Prompts that wait can use it. ``inside``: what it asks about stays inside
        its Session, which is all ``--devastation inside`` answers (ADR-0026)."""
        running = self._running.get(session_id)
        if running is None or running.cancellation.reason is not None:
            return
        answers = running.auto_confirm is not None and (
            running.auto_confirm != "--devastation inside" or inside
        )
        if answers:  # answered at once: it keeps its slot, goes on
            assert running.auto_confirm is not None
            by = running.auto_confirm
            fields = {"title": title, "text": text, "answer": True, "by": by}
            shown = f"confirmation answered by {by}: {title}"
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

    def _auto_confirm(self, prompt: Prompt, session: Session) -> AutoConfirm | None:
        """Who answers the Confirmations of the Prompt's Turn: ``-y`` on it, or who answered
        them before a fall, since a Turn keeps what it had when it started; else
        ``--devastation`` if it covers the Prompt's chain and the Session its tools act in
        (ADR-0013), or only what stays inside if a Subperfil's Exención covers it (ADR-0026);
        else a human."""
        if prompt.answered_by is not None or prompt.suspended:
            return prompt.answered_by
        chain = from_session(prompt.origin, session.id)
        if self._devastation.answers(chain):
            return "--devastation"
        if self._devastation.answers_inside(chain):
            return "--devastation inside"
        return None

    def tools_started(self, session_id: SessionId, names: tuple[str, ...], step: Step) -> None:
        """A running Turn's ``step`` called tools: they run without its slot, so Prompts that
        wait can use it (ADR-0004)."""
        running = self._running.get(session_id)
        if running is None or running.cancellation.reason is not None:
            return
        running.tools_step = step
        self._release(running)
        entry = tools_entry(names)
        self._reporter.record(session_id, "tools_called", entry.text, tools=list[JsonValue](names))
        self._windows.send(session_id, entry)
        self._start_turns()

    def tools_ended(self, session_id: SessionId, step: Step, grows: int) -> None:
        """Its tools gave back what they said, in ``step``: its next Step needs ``grows`` more
        of the pool at most, and waits for a slot as a Continuation. A tool that failed is
        shown, and its LLM reads why."""
        running = self._running.get(session_id)
        if running is None or running.cancellation.reason is not None:
            return
        running.tools_step = step
        running.need += grows
        running.grown += grows
        running.resume = functools.partial(self._next_step, running)
        running.ready_at = self._wall()
        self._reporter.log_session(session_id, "tools_returned", grows=grows)
        self._show_failed_tools(session_id, tuple(step.failures()))
        if self._compacts_between_steps(running):
            self._yielded[session_id] = running.prompt.prompt_id
            running.resume = None  # it ends instead; its Steps go on once it compacted
            running.job.tools.yield_turn()
        self._start_turns()

    def _show_failed_tools(
        self, session_id: SessionId, failed: tuple[tuple[str, str], ...]
    ) -> None:
        for name, error in failed:
            entry = tool_failed_entry(name, error)
            self._reporter.record(session_id, "tool_failed", entry.text, tool=name, error=error)
            self._windows.send(session_id, entry)

    def _compacts_between_steps(self, running: _RunningTurn) -> bool:
        """Whether the Turn's next Step may pass ``compact.threshold`` of its slot, and its
        Session's saved Turns may take more than ``compact.keep``, so some could be compacted:
        it stops after its tools and compacts first. Never twice in a Turn without a
        Compaction that served in between (ADR-0017)."""
        session = running.session
        compact = session.settings.compact
        if self._yielded.get(session.id) == running.prompt.prompt_id:
            return False
        if not wants_compaction(compact, running.need, running.slot_context):
            return False
        # What it read when it started holds its saved Turns, and more: at most this much.
        schemas = len(json.dumps(running.job.tools.schemas))  # counted a character each
        saved = running.need - running.grown - running.output_cap - schemas
        return saved > compact.keep * running.slot_context and bool(
            self._conversation(session).turns
        )

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
        queued = [p.text for p in self._prompts.of(session_id) if p.queued]
        self._windows.send(session_id, TurnState(active=active, waiting=waiting, queued=queued))

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
            since = running.ready_at
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
            # It makes room for that Prompt, so it goes, or stays, with it.
            self._compactions.wanted(session_id, need)
            entry = Prompt(
                session_id,
                "",
                "auto_compact",
                origin=prompt.origin,
                initiative=prompt.initiative,
            )
            self._prompts.insert_before(prompt, entry)
            return self._prompt_candidate(entry, waiting)
        if need > slot_context:
            raise PromptTooBig(need=need, limit=slot_context)
        repeats = (
            session_id in self._falls
            or prompt.suspended  # a Turn under way: a fall, or one that compacts mid-Turn
            or session_id in self._yielded  # its Compaction, for that Turn
            or self._compactions.continuing(session_id)
            or bool(prompt.woke_by)  # a Suscripción's: its Session goes on (ADR-0004)
        )
        kind: WaitClass = "continuation" if repeats else "prompt"
        if kind == "prompt" and prompt.initiative:
            kind = "initiative"  # behind the humans' and the Sessions' (ADR-0026)
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
            items = self._input(prompt, session, link)
            ticket = next(self._tickets)
            tools = self._tools_for(session)
            schemas = len(json.dumps(tools.schemas)) if tools is not None else 0
            self._measures[session.id] = _Measure(prompt, link.key, ticket, schemas=schemas)
            self._servers.measure(link.key, session.id, ticket, items)
            return None
        if not measure.done:
            return None
        tokens = (measure.tokens or 0) + measure.schemas
        return tokens + output_cap(_sampling(prompt, session), slot_context)

    def _input(self, prompt: Prompt, session: Session, link: Link) -> list[Item]:
        """The first Step's ``input`` for the Prompt: the Conversation and the Prompt, with
        their attachments as this Server can take them (ADR-0022)."""
        state = self._states.get(link.key)
        vision = isinstance(state, ServerReady) and state.props.vision
        reader = AttachmentReader(self._sessions.workspace(session.id), vision)
        conversation = self._conversation(session)
        pending = self._notifications.pending(session.id)
        lines = self._notifications.opening(session, conversation, pending)
        items = conversation.request_input(prompt.text, prompt.attachments, reader, lines)
        return [*items, *(item for step in prompt.steps for item in step.items)]

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
        self._answers.dropped(prompt, f"it could not start: {error}")
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
        sampling = _sampling(prompt, session)
        cap = output_cap(sampling, slot_context)
        compacting = prompt.kind != "prompt"
        if prompt.queued:  # it waited behind a Turn: its Chat shows it now (ADR-0024)
            prompt = replace(prompt, queued=False)  # a Step run again never shows it twice
            self._reporter.log_session(session.id, "prompt_started", text=prompt.text)
            self._show_prompt(session, prompt)
            self.send_turn_state(session.id)  # it no longer waits below the Chat
        if not prompt.steps:  # a new Turn: what waits was typed before it
            self._prompts.turn_started(session.id)
        joined: list[str] = []
        if not compacting and prompt.steps and prompt.steps[-1].calls:
            joined = self._join(session, slot_context - need)
            prompt = _with_interjected(prompt, joined)
            need += _chars(joined)
        request = TurnRequest(
            model=session.settings.model,
            input=self._compactions.request(session.id)
            if compacting
            else self._input(prompt, session, link),
            params={
                **sampling.request_params(),
                "max_output_tokens": cap,
                **prompt.overrides.extra_params(),
            },
        )
        tools = None if compacting else self._tools_for(session)
        told = NOTHING_TOLD if compacting else self._notifications.tell(session.id)
        request = replace(request, done=list(prompt.steps))
        gate = ToolGate(tools, from_session(prompt.origin, session.id), prompt.prompt_id)
        job = TurnJob(session.id, request, link.base_url, tools=gate)
        auto_confirm = self._auto_confirm(prompt, session)
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
            grown=_chars(joined),
            context_before=context_tokens(conversation.turns[-1].steps)
            if conversation.turns
            else None,
            notifications=[]
            if compacting
            else self._notifications.opening(session, conversation, told),
            told=told,
        )
        self._running[session.id].streamed.steps.extend(prompt.steps)
        self._reserve(self._running[session.id])
        if compacting:
            shown = "summary step started"
            self._reporter.record(session.id, "compaction_step_started", shown, need=need)
            self.send_turn_state(session.id)  # its border says so while the Step runs
        else:
            self._reporter.record(
                session.id, "turn_started", "turn started", need=need, **_brought(prompt)
            )
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

    def _yield(self, running: _RunningTurn) -> None:
        """The Turn stopped after its tools for its Session to compact: it goes back first in
        the queue with the Steps it ended, whose tools do not run again, and its next Step
        measures the compacted Conversation first (ADR-0017, ADR-0023)."""
        session_id = running.session.id
        shown = "its next Step may not fit: measured again first, it compacts if it must"
        self._reporter.record(session_id, "turn_yielded", shown, steps=len(running.streamed.steps))
        self._windows.send(session_id, ChatAppend(role="info", label="context", text=shown))
        steps = tuple(running.streamed.steps)
        self._prompts.push_front(
            replace(running.prompt, suspended=True, steps=steps, answered_by=running.auto_confirm)
        )

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
        # It goes on from the Steps it ended: their tools are not run again (ADR-0023).
        steps = tuple(streamed.steps)
        answered_by = running.auto_confirm  # it keeps who answers for it
        self._prompts.push_front(
            replace(running.prompt, suspended=True, steps=steps, answered_by=answered_by)
        )
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
            and running.resume is not None
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
            if kind == CONTEXT_NOTICE.name:  # once it ends, its LLM is told what is left
                running.context_noticed = True

        running.notice_event = event

        for failure in self._notices.happened(event, running.session.settings, notify):
            self._reporter.error(failure, session_id)

    def _tell_context_left(self, running: _RunningTurn) -> None:
        """The Context Notice showed in the saved Turn: its LLM, which cannot see its tokens,
        is told how much is left as the Turn ended, instead of what an older line said
        (#120)."""
        left = context_left(running.notice_event) if running.notice_event else None
        if not running.context_noticed or left is None:
            return
        session_id = running.session.id
        self._notifications.withdraw(session_id, CONTEXT_LEFT)
        self._notifications.leave(session_id, CONTEXT_LEFT, left)

    def _others_reserved(self, running: _RunningTurn) -> int:
        """What the Steps in course of the other Sessions on its Server reserve."""
        mine = (self._main_id, self._pid, running.session.id)
        reserved = self._servers.wait_list(running.link.key).look().reserved
        return sum(r.tokens for r in reserved if (r.main, r.pid, r.session) != mine)

    def _answered(self, running: _RunningTurn, answer: bool) -> None:
        running.asking = None
        running.resume = functools.partial(running.job.confirmation.resume, answer)
        running.ready_at = self._wall()
        self._start_turns()

    def _resume_turn(self, running: _RunningTurn, slot: SlotLock) -> None:
        resume, running.resume, running.slot = running.resume, None, slot
        running.progress_at = self._clock()
        running.sent = False  # a new Step reads its input first
        assert resume is not None
        resume()  # what the human sent may enter first, and count in what it reserves
        self._reserve(running)

    def _next_step(self, running: _RunningTurn) -> None:
        """Its tools answered and it holds a slot: its next Step starts, reading first what the
        human sent from its Window meanwhile (ADR-0024)."""
        joined = self._join(running.session, running.slot_context - running.need)
        if running.tools_step is not None:  # kept here too, should a cut land before it ends
            running.tools_step = running.tools_step.interjecting(joined)
        running.need += _chars(joined)
        running.grown += _chars(joined)
        running.job.tools.resume(joined)

    def _join(self, session: Session, room: int) -> list[str]:
        """Take the Session's queued Prompts that may enter its running Turn before its next
        Step (``Prompt.may_join``), as many as fit in ``room``; its Chat shows them now."""
        joined = self._prompts.take_joining(session.id, room)
        if not joined:
            return []
        self._measures.pop(session.id, None)  # its next Prompt is another one, if any
        if not self._prompts.has(session.id):
            self._since.pop(session.id, None)
        for prompt in joined:
            self._reporter.log_session(
                session.id, "prompt_joined", text=prompt.text, prompt_id=prompt.prompt_id
            )
            self._show_prompt(session, prompt)
        self.send_turn_state(session.id)  # they no longer wait below the Chat
        return [prompt.text for prompt in joined]

    def _withdraw(self, running: _RunningTurn) -> None:
        """Its Confirmation is no longer valid: nobody answers it."""
        if running.asking is not None:
            self._boxes.withdraw(running.asking)
            running.asking = None

    def has_turn(self, session_id: SessionId) -> bool:
        """Whether the Session has a Turn under way: running, suspended until its Server is
        back (ADR-0023), or a Compactación's (ADR-0017)."""
        return (
            session_id in self._running
            or any(prompt.suspended for prompt in self._prompts.of(session_id))
            or self._compactions.busy(session_id)
        )

    def waking(self, session_id: SessionId) -> bool:
        """Whether a Suscripción's Prompt for the Session waits in the queue."""
        return any(prompt.woke_by for prompt in self._prompts.of(session_id))

    def busy(self, session_id: SessionId) -> bool:
        """Whether the Session has a Turn, a queued Prompt or a Compaction under way."""
        return self._busy(session_id)

    def cancel_prompt(self, session_id: SessionId, prompt_id: PromptId, reason: str) -> None:
        """Cancel the Session's Prompt ``prompt_id``, queued or running, and nothing else: an
        Encargo that no longer waits for it (#117). Its Turn, once its executor stops, is
        saved in no Conversation, as any cancelled Turn."""
        running = self._running.get(session_id)
        if running is not None and running.prompt.prompt_id == prompt_id:
            running.cancellation.cancel(reason)
            self._withdraw(running)
            return
        for prompt in self._prompts.of(session_id):
            if prompt.prompt_id != prompt_id or prompt.kind != "prompt":
                continue
            self._prompts.remove(prompt)
            self._measures.pop(session_id, None)
            if not self._prompts.has(session_id):
                self._since.pop(session_id, None)
            texts: list[JsonValue] = [prompt.text]
            shown = dropped_entry(1, reason).text
            self._reporter.record(
                session_id, "prompts_dropped", shown, reason=reason, texts=texts, kinds=["prompt"]
            )
            self._answers.dropped(prompt, f"it left the queue: {reason}")
            self.send_turn_state(session_id)
            self._start_turns()
            return

    def cancel_woken(
        self, session_id: SessionId, subscription_id: SubscriptionId, reason: str
    ) -> None:
        """Cancel the Session's Prompt that ``subscription_id`` woke, queued or running."""
        running = self._running.get(session_id)
        prompts = [*([running.prompt] if running else []), *self._prompts.of(session_id)]
        for prompt in prompts:
            if subscription_id in prompt.woke_by:
                self.cancel_prompt(session_id, prompt.prompt_id, reason)
                return

    def ready(self, session_id: SessionId) -> bool:
        """Whether the Session's Server is ready: an Encargo's deadline runs only then."""
        link = self._watched_link_of(session_id)
        return link is not None and self._ready(link.key)

    def snapshot(self, in_active: InActive) -> QueueView:
        """Consultar's view (#118): who of the active Profile runs or waits, and the Servers
        the Enlaces watch, with the slots its Turns take; another Profile's do not show."""
        sessions = [
            QueuedSession(
                session=session_id,
                running=session_id in self._running,
                queued=len(self._prompts.of(session_id)),
            )
            for session_id in dict.fromkeys([*self._running, *self._prompts.session_ids()])
            if in_active(session_id)
        ]
        servers: list[WatchedServer] = []
        for key, link in self._watched.items():
            state = self._states.get(key)
            ready = state if isinstance(state, ServerReady) else None
            busy = [s for s, turn in self._running.items() if turn.link.key == key]
            servers.append(
                WatchedServer(
                    key=key,
                    server=link.describe(),
                    state=_state_name(state),
                    slots=ready.props.total_slots if ready else None,
                    busy=sum(1 for session_id in busy if in_active(session_id)),
                    model=Path(ready.props.model_path).name if ready else None,
                )
            )
        return QueueView(sessions=tuple(sessions), servers=tuple(servers))

    def room_for(self, session_id: SessionId) -> bool:
        """Whether the Session's Server is ready with a slot that none of this Main's
        Sessions takes, running or waiting for one: a Lote gives it a task only then (#117)."""
        link = self._watched_link_of(session_id)
        state = None if link is None else self._states.get(link.key)
        if link is None or not isinstance(state, ServerReady):
            return False
        running = {s for s, turn in self._running.items() if turn.link.key == link.key}
        return len(running | set(self._sessions_on(link.key))) < state.props.total_slots

    def serving(self, session_id: SessionId) -> str:
        """Who answers the Session's Prompts now: its Server and the model there."""
        link = self._watched_link_of(session_id)
        if link is None:
            return ""
        state = self._states.get(link.key)
        model = Path(state.props.model_path).name if isinstance(state, ServerReady) else ""
        return " · ".join(filter(None, (f"{link.describe()} ({link.key})", model)))

    def _busy(self, session_id: SessionId) -> bool:
        """Whether a Prompt sent now would wait behind a Turn, a Prompt or a Compaction."""
        return (
            session_id in self._running
            or self._prompts.has(session_id)
            or self._compactions.busy(session_id)
        )

    def _show_prompt(self, session: Session, prompt: Prompt) -> None:
        label = session.settings.username
        self._windows.send(session.id, ChatAppend(role="user", label=label, text=prompt.text))

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
        # Its Textos intercalados, in no Conversation either (ADR-0024).
        steps = [*streamed.steps, *filter(None, [running.tools_step])]
        joined = interjected_in(steps)
        fields: dict[str, JsonValue] = {
            "error": str(error),
            "reasoning": streamed.reasoning,
            "answer": streamed.answer,
            "joined": list[JsonValue](joined),
        }
        cancelled = isinstance(error, TurnCancelled)
        ending = "cancelled" if cancelled else "failed"
        prompt = running.prompt.text
        self._reporter.record(
            session_id, f"turn_{ending}", f"turn {ending}: {error}", **fields, prompt=prompt
        )
        self._windows.send(session_id, error_entry(str(error)))
        if not self._stopping:  # else MainState keeps it running, for the next start to tell
            self._answers.failed(running.prompt, f"its Turn {ending}: {error}")
        if cancelled:  # back to its Draft, as what still waited in the queue (ADR-0024)
            for text in reversed(joined):
                self._windows.put_back(session_id, text)
        else:  # its Prompt is in no Conversation, so the user may send it again
            # The Session's queued Prompts leave with it: without it in the Conversation they
            # would run out of the order they were written in (#16).
            behind = self._drop_queued(session_id, FAILED_BEFORE)
            if behind:
                self._windows.send(session_id, dropped_entry(len(behind), FAILED_BEFORE))
            typed = [queued.text for queued in behind if queued.kind == "prompt"]
            self._give_back(session_id, [prompt, *joined, *typed])

    def _drop_queued(
        self, session_id: SessionId, reason: str, by_human: bool = False
    ) -> list[Prompt]:
        """Take a Session's queued Prompts out of the queue, as ``prompts_dropped`` in its Log;
        ``by_human``, only those of a human Origin, and the others keep their place."""
        dropped = self._prompts.drop(session_id, _not_human if by_human else None)
        left = by_human and bool(self._prompts.of(session_id))
        if dropped or not left:
            self._measures.pop(session_id, None)  # its next Prompt is another one, if any
        if not left:
            self._since.pop(session_id, None)
        if not left or any(prompt.kind != "prompt" for prompt in dropped):
            self._compactions.drop(session_id)  # one it was doing, or would have done
        if any(prompt.suspended for prompt in dropped):  # what its Turn told waits again
            self._yielded.pop(session_id, None)
            self._notifications.not_saved(session_id)
        for prompt in dropped:
            if prompt.kind == "prompt":
                self._answers.dropped(prompt, f"it left the queue: {reason}")
        if dropped:
            shown = dropped_entry(len(dropped), reason).text
            texts: list[JsonValue] = [prompt.text for prompt in dropped]
            kinds: list[JsonValue] = [prompt.kind for prompt in dropped]
            self._reporter.record(
                session_id, "prompts_dropped", shown, reason=reason, texts=texts, kinds=kinds
            )
        return dropped

    def _give_back(self, session_id: SessionId, texts: list[str]) -> None:
        """Texts back to the Window's input, the first one shown first; with its Window closed
        they come back when it opens (``returned_prompts``)."""
        if not self._windows.is_open(session_id):
            return
        for text in reversed(texts):  # each one shown pushes the one before it back
            self._windows.send(session_id, InputRestore(text=text))
        self._reporter.log_session(session_id, "prompt_returned", texts=list[JsonValue](texts))

    def _untold(self, running: _RunningTurn) -> None:
        """A Turn that ends unsaved leaves its Notificaciones for the next Prompt; a summary
        Step told none, so it leaves alone what a suspended Turn told."""
        if not _compacting(running):
            self._notifications.not_saved(running.session.id)

    def _save_turn(self, running: _RunningTurn, steps: list[Step]) -> int:
        """Save the finished Turn and return its index in the Conversation."""
        # Saved only now, whole, so a Turn that fails half-way never reaches the disk.
        conversation = self._conversation(running.session)
        attachments = list(running.prompt.attachments)
        turn = Turn(
            prompt=running.prompt.text,
            steps=steps,
            cut=running.cut,
            attachments=attachments,
            notifications=running.notifications,
            typed=running.prompt.typed,
            origin=running.prompt.origin,
            outsider=running.prompt.outsider,
            prompt_id=running.prompt.prompt_id,
        )
        self._sessions.save_conversation(running.session, conversation.with_turn(turn))
        return conversation.next_turn


def _not_human(prompt: Prompt) -> bool:
    return not prompt.origin.is_human


def _interrupted_steps(running: _RunningTurn) -> list[Step]:
    """An interrupted Turn's Steps: those that ended, then what its last one streamed, as the
    items a Step gives, its message marked ``incomplete``. A Step whose tools were cut is kept
    whole, with what its tools gave back if they ended, else each call with an error as its
    output, so its LLM knows what it had set going."""
    step = running.tools_step
    if step is not None:
        if not step.results:
            step = step.model_copy(
                update={"results": [call_output(c, CUT_CALL) for c in step.calls]}
            )
        return [*running.streamed.steps, step]
    reasoning_from, answer_from = running.step_from
    reasoning = running.streamed.reasoning[reasoning_from:]
    answer = running.streamed.answer[answer_from:]
    output: list[Item] = []
    if reasoning:
        part: Item = {"type": "reasoning_text", "text": reasoning}
        output.append(
            {"id": "rs_interrupted", "type": "reasoning", "summary": [], "content": [part]}
        )
    if answer:
        text: Item = {"type": "output_text", "text": answer, "annotations": []}
        message: Item = {"type": "message", "role": "assistant", "status": "incomplete"}
        output.append({"id": "msg_interrupted", **message, "content": [text]})
    return [*running.streamed.steps, Step(output=output)]


def _chars(texts: list[str]) -> int:
    """What ``texts`` add to a Step's input, a character each, as tools' outputs count."""
    return sum(len(text) for text in texts)


def _with_interjected(prompt: Prompt, texts: list[str]) -> Prompt:
    """A suspended Prompt whose Turn goes on after its tools, with ``texts`` entered after
    them."""
    *before, last = prompt.steps
    return replace(prompt, steps=(*before, last.interjecting(texts)))


def _sampling(prompt: Prompt, session: Session) -> Sampling:
    """The Session's Sampling with the Prompt's overrides for its Turn (#14)."""
    return prompt.overrides.sampling(session.settings.sampling)


def _commands(prompt: Prompt) -> dict[str, JsonValue]:
    """What the Log keeps of a Prompt's Command prompts, besides its text."""
    fields: dict[str, JsonValue] = {}
    overrides = prompt.overrides.model_dump(exclude_none=True)
    if overrides:
        fields["overrides"] = overrides
    if prompt.attachments:
        fields["attachments"] = [attachment.path for attachment in prompt.attachments]
    if prompt.typed:
        fields["typed"] = prompt.typed
    if prompt.owed:
        fields["owed"] = list[JsonValue](prompt.owed)
    if prompt.woke_by:
        fields["woke_by"] = list[JsonValue](prompt.woke_by)
    return fields


def _brought(prompt: Prompt) -> dict[str, JsonValue]:
    """What the Log keeps, as its Turn starts, of what a Proceso adherido brought: an
    Externo's identity, never read by its LLM, and whether it came by the Iniciativa
    (ADR-0026)."""
    fields: dict[str, JsonValue] = {}
    if prompt.outsider is not None:
        fields["outsider"] = prompt.outsider
    if prompt.initiative:
        fields["initiative"] = True
    return fields


def _compacting(running: _RunningTurn) -> bool:
    """Whether its Step is a Compaction's summary Step."""
    return running.prompt.kind != "prompt"


def _reason(error: AllInError) -> JsonValue:
    """Why a cancelled Turn was cancelled; ``None`` for any other error."""
    return error.fields.get("reason") if isinstance(error, TurnCancelled) else None


def _state_name(state: ServerState | None) -> ServerStateName:
    """As Consultar names it; one not heard from yet is down."""
    match state:
        case ServerReady():
            return "ready"
        case ServerLoading():
            return "loading"
        case ServerAsleep():
            return "asleep"
        case ServerFailed():
            return "failed"
        case ServerRestarting():
            return "restarting"
        case ServerDown() | None:
            return "down"
