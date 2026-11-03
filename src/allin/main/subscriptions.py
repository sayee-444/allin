"""Suscripciones (#116, ADR-0011, ADR-0013): an event wakes a Session's own LLM with a Prompt.

A Session opens one with ``@subscribe`` (a human, for any Session, with ``/subscribe``), or with
``--reply-to`` on what it sends. While it waits it has no Turn and holds no slot. The events
that come gather, and once its Session has no Turn, at the next tick, up to
``subscription.events`` of them go in one Prompt, a Continuation, headed by who sends it.

- **A reply** (``--reply-to``) waits for one Prompt's answer: that of the last Turn of its
  chain, so a Turn that leaves a reply of its own open does not answer yet, and the Turn that
  closes it does. It wakes once, also when the Prompt fails or leaves the queue.
- **process:<name>** wakes on each thing a method of that Proceso adherido announces.
- **timer:<seconds>** wakes once, after that time went by with its Profile active.
- **rewind:<sid>** and **compact:<sid>** wake on each Rewind or Compactación of that Session,
  with the Prompts it undid or summarized.

Each Prompt it wakes carries in its Origin the chain that led to it, and the Suscripción's
link. A Suscripción already that many times in the chain as ``subscription.repeats`` is cut
with a Notificación, so A waking B waking A ends by itself. A reply or a timer is new each time
but counts as the same Suscripción: the same Session waiting for the same one, or for time.

A Rewind of its Session to before it opened one cancels it. They live in the MainState of the
Profile they were opened in: while it is inactive they are frozen, and what happens meanwhile
to a Session it shares waits there, to come grouped when it is back.

An Encargo or a Lote (#117, ``allin.main.errands``) is one more: it waits for the answers of
the Prompts it hands out, and wakes its Session once, with all of them.
"""

import math
from collections.abc import Callable, Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue

from allin.core.errors import (
    AllInError,
    ErrandCycle,
    SubscriptionInvalid,
    SubscriptionNotFound,
    SubscriptionNotOwn,
)
from allin.core.ids import (
    NO_PROMPT_ID,
    AttachedName,
    PromptId,
    SessionId,
    SubscriptionId,
    new_prompt_id,
    new_subscription_id,
    parse_attached_name,
    parse_session_id,
)
from allin.core.origin import Link, Origin, from_session
from allin.core.settings import SubscriptionSettings
from allin.main.conversation import Turn
from allin.main.errands import (
    OnErrand,
    Outcome,
    OutcomeKind,
    Part,
    parse_part_id,
    part_id,
    report,
    task_text,
    with_outcome,
)
from allin.main.notifications import ERRAND, SUBSCRIPTION, Notifications
from allin.main.prompts import Prompt, origin_header
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore
from allin.main.turn_queue import TurnQueue

SUBSCRIBE_USAGE = "/subscribe <sid> [process:<name>|timer:<seconds>|rewind:<sid>|compact:<sid>]"
UNSUBSCRIBE_USAGE = "/unsubscribe <sid> <subscription>"


def parse_watch(event: str) -> "Watch":
    """What ``event``, as ``/subscribe`` takes it, waits for; raises ``SubscriptionInvalid``."""
    kind, _, target = event.partition(":")
    match kind:
        case "process" if (name := parse_attached_name(target)) is not None:
            return OnProcess(name=name)
        case "timer":
            try:
                seconds = float(target)
            except ValueError:
                seconds = 0.0
            if 0 < seconds < float("inf"):
                return OnTime(left=seconds)
        case "rewind" | "compact" if (sid := parse_session_id(target)) is not None:
            return OnSession(kind=kind, session_id=sid)
        case _:
            pass
    raise SubscriptionInvalid(event=event, usage=SUBSCRIBE_USAGE)


class OnReply(BaseModel, frozen=True):
    kind: Literal["reply"] = "reply"
    session_id: SessionId  # the Session that answers
    prompt_id: PromptId


class OnProcess(BaseModel, frozen=True):
    kind: Literal["process"] = "process"
    name: AttachedName


class OnTime(BaseModel, frozen=True):
    kind: Literal["timer"] = "timer"
    left: float  # seconds still to go with its Profile active


class OnSession(BaseModel, frozen=True):
    kind: Literal["rewind", "compact"]
    session_id: SessionId


type Watch = Annotated[
    OnReply | OnProcess | OnTime | OnSession | OnErrand, Field(discriminator="kind")
]


class Subscription(BaseModel, frozen=True):
    id: SubscriptionId
    session_id: SessionId  # the one it wakes: always its own (ADR-0011)
    watch: Watch
    # The Turn of its Session it was opened in, or the next one: a Rewind there cancels it.
    opened_turn: int
    in_turn: bool = False  # opened by that Turn, whose answer a reply holds back
    origin: Origin  # who opened it

    def opened_after(self, turn: int) -> bool:
        """Whether it was opened by its Session's Turn ``turn`` or later, or after it ended."""
        return self.opened_turn > turn or (self.opened_turn == turn and self.in_turn)

    @property
    def event(self) -> str:
        """What it waits for, as written: ``process:board``, ``reply:ab12``…"""
        match self.watch:
            case OnReply(session_id=sid) | OnSession(session_id=sid):
                return f"{self.watch.kind}:{sid}"
            case OnProcess(name=name):
                return f"process:{name}"
            case OnTime(left=left):
                return f"timer:{math.ceil(left)}"
            case OnErrand():
                return self.watch.event

    @property
    def link(self) -> Link:
        """Its link in the Origin of what it wakes: a reply or a timer, one each time, counts
        as the same Suscripción when it waits for the same."""
        match self.watch:
            case OnReply(session_id=sid):
                key = f"{self.session_id}.reply.{sid}"
            case OnTime():
                key = f"{self.session_id}.timer"
            case _:
                key = self.id
        return Link("subscription", key)


class Happened(BaseModel, frozen=True):
    """An event a Suscripción waits for, until its Session's Prompt carries it."""

    subscription: Subscription
    text: str
    origin: Origin  # the chain that led to it, before the Suscripción's link


class Debt(BaseModel, frozen=True):
    """A Prompt's answer held back: its Session's Turn left replies of its own open."""

    session_id: SessionId  # the one that answers
    owed: tuple[SubscriptionId, ...]  # the replies the answer goes to
    waiting: tuple[SubscriptionId, ...]  # its own replies still open
    answer: str  # what its last Turn wrote: the answer if those are cancelled
    origin: Origin  # that Turn's Prompt's


class SubscriptionState(BaseModel, frozen=True):
    """What a Profile's MainState keeps of its Suscripciones."""

    open: list[Subscription] = Field(default_factory=list[Subscription])
    pending: list[Happened] = Field(default_factory=list[Happened])
    debts: list[Debt] = Field(default_factory=list[Debt])

    def renamed(self, old: SessionId, new: SessionId) -> "SubscriptionState":
        """The same, with a Session renamed (#59) wherever one is named, but in Origins."""

        def key(session_id: SessionId) -> SessionId:
            return new if session_id == old else session_id

        def subscription(s: Subscription) -> Subscription:
            watch = s.watch
            if isinstance(watch, OnReply | OnSession):
                watch = watch.model_copy(update={"session_id": key(watch.session_id)})
            elif isinstance(watch, OnErrand):
                watch = watch.renamed(old, new)
            return s.model_copy(update={"session_id": key(s.session_id), "watch": watch})

        return SubscriptionState(
            open=[subscription(s) for s in self.open],
            pending=[
                h.model_copy(update={"subscription": subscription(h.subscription)})
                for h in self.pending
            ],
            debts=[d.model_copy(update={"session_id": key(d.session_id)}) for d in self.debts],
        )


# Applies a change to the Suscripciones of every Profile but the active one, frozen.
type Frozen = Callable[[Callable[[SubscriptionState], SubscriptionState]], None]


def _none_frozen(change: Callable[[SubscriptionState], SubscriptionState]) -> None:
    pass


class Subscriptions:
    """The active Profile's Suscripciones. Only the dispatcher touches it."""

    def __init__(
        self,
        sessions: SessionStore,
        turns: TurnQueue,
        reporter: Reporter,
        notifications: Notifications,
        settings: Callable[[], SubscriptionSettings],
        clock: Callable[[], float],
        frozen: Frozen = _none_frozen,
    ) -> None:
        self._sessions = sessions
        self._turns = turns
        self._reporter = reporter
        self._notifications = notifications
        self._settings = settings
        self._clock = clock
        self._frozen = frozen
        self._last_tick = clock()
        self._open: dict[SubscriptionId, Subscription] = {}
        self._pending: list[Happened] = []
        self._debts: list[Debt] = []
        # Encargos settled or cancelled since Main started: what comes for them is dropped.
        self._closed: set[SubscriptionId] = set()

    def state(self) -> SubscriptionState:
        return SubscriptionState(
            open=list(self._open.values()), pending=list(self._pending), debts=list(self._debts)
        )

    def restore(self, state: SubscriptionState) -> None:
        """Take a Profile's, as Main enters it; what came meanwhile goes at the next tick."""
        self._open = {subscription.id: subscription for subscription in state.open}
        self._pending = list(state.pending)
        self._debts = list(state.debts)
        self._last_tick = self._clock()

    def renamed(self, old: SessionId, new: SessionId) -> None:
        self.restore(self.state().renamed(old, new))

    def listed(self, session_id: SessionId) -> list[Subscription]:
        return [s for s in self._open.values() if s.session_id == session_id]

    def subscribe(self, session_id: SessionId, watch: Watch, origin: Origin) -> Subscription:
        """Open one for ``session_id``, which must be ``origin``'s own if a Session asks."""
        _check_own(session_id, origin)
        if isinstance(watch, OnSession):
            self._sessions.load(watch.session_id)  # raises if there is none
        return self._opened(session_id, watch, origin)

    def reply_to(
        self, subscriber: SessionId, target: SessionId, prompt_id: PromptId, origin: Origin
    ) -> SubscriptionId:
        """Open the reply ``--reply-to`` asks for: ``target``'s answer to ``prompt_id``."""
        _check_own(subscriber, origin)
        watch = OnReply(session_id=target, prompt_id=prompt_id)
        return self._opened(subscriber, watch, origin).id

    def unsubscribe(
        self, session_id: SessionId, subscription_id: SubscriptionId, origin: Origin
    ) -> None:
        _check_own(session_id, origin)
        subscription = self._open.get(subscription_id)
        if subscription is None or subscription.session_id != session_id:
            raise SubscriptionNotFound(session_id=session_id, subscription=subscription_id)
        self._cancel(subscription, "unsubscribed")

    def open_errand(self, session_id: SessionId, errand: OnErrand, origin: Origin) -> Subscription:
        """Open an Encargo or a Lote for ``session_id``, which must be ``origin``'s own if a
        Session asks, and hand out what it can now (#117)."""
        _check_own(session_id, origin)
        workers = errand.workers or tuple(p.worker for p in errand.parts if p.worker is not None)
        self._check_tree(session_id, workers)
        subscription = self._opened(session_id, errand, origin, prefix="e")
        self._advance(subscription.id)
        return subscription

    def errands(self) -> list[Subscription]:
        """The open Encargos and Lotes."""
        return [s for s in self._open.values() if isinstance(s.watch, OnErrand)]

    # What happens -------------------------------------------------------------------------

    def answered(self, prompt: Prompt, turn: int, answer: str) -> None:
        """A Turn of ``prompt`` was saved as ``turn``: its answer goes to the replies waiting
        for it, unless that Turn left replies of its own open."""
        # Its replies stay open until a Turn of its own took what they brought.
        left_open = tuple(
            dict.fromkeys(
                s.id
                for s in self._waiting()
                if s.session_id == prompt.session_id
                and s.in_turn
                and s.opened_turn == turn
                and isinstance(s.watch, OnReply | OnErrand)
            )
        )
        origin = from_session(prompt.origin, prompt.session_id)
        for debt in self._take_debts(prompt):
            waiting = (*debt.waiting, *left_open)
            if waiting:
                held = Debt(
                    session_id=prompt.session_id,
                    owed=debt.owed,
                    waiting=waiting,
                    answer=answer,
                    origin=prompt.origin,
                )
                self._debts.append(held)
            else:
                outcome = Outcome(
                    session_id=prompt.session_id, kind="answered", text=answer, origin=origin
                )
                self._reply(debt.owed, outcome)

    def failed(self, prompt: Prompt, why: str) -> None:
        """``prompt``'s Turn ran and is in no Conversation: what it opened is cancelled, as a
        Rewind of it would, and who waits hears why it has no answer."""
        self._cancel_unsaved(prompt)
        self._unanswered(prompt, why, "failed")

    def lost(self, prompt: Prompt, why: str) -> None:
        """``prompt``'s Turn died with Main: as ``failed``, but an Encargo counts it as no
        answer, and does not try it again (ADR-0011)."""
        self._cancel_unsaved(prompt)
        self._unanswered(prompt, why, "lost")

    def dropped(self, prompt: Prompt, why: str) -> None:
        """``prompt`` left the queue before its Turn: who waits hears why it has no answer."""
        self._unanswered(prompt, why, "failed")

    def announced(self, process: AttachedName, text: str, origin: Origin) -> None:
        for subscription in list(self._open.values()):
            if isinstance(subscription.watch, OnProcess) and subscription.watch.name == process:
                self._happened(subscription, f"{process} announced: {text}", origin)

    def rewound(
        self, session_id: SessionId, turn: int, undone: Sequence[Turn], origin: Origin
    ) -> None:
        """The Session went back to before its Turn ``turn``: what it opened since is
        cancelled, and who waits for its Rewinds hears which Prompts it undid."""
        for subscription in self._waiting():
            if subscription.session_id == session_id and subscription.opened_after(turn):
                self._cancel(subscription, "rewound")

        def frozen_cancel(state: SubscriptionState) -> SubscriptionState:
            def undone_by_it(s: Subscription) -> bool:
                return s.session_id == session_id and s.opened_after(turn)

            return state.model_copy(
                update={
                    "open": [s for s in state.open if not undone_by_it(s)],
                    "pending": [h for h in state.pending if not undone_by_it(h.subscription)],
                }
            )

        self._frozen(frozen_cancel)  # a Session both Profiles share
        if not undone:
            return
        ids = ", ".join(t.prompt_id for t in undone if t.prompt_id)
        numbers = _numbers(turn + 1, turn + len(undone))
        text = f"Session {session_id} rewound Prompt{numbers}" + (f" (ids {ids})" if ids else "")
        self._session_changed("rewind", session_id, text, origin)

    def compacted(
        self,
        session_id: SessionId,
        summarized: int,
        ids: Sequence[PromptId],
        origin: Origin,
    ) -> None:
        """``ids``: those of the Prompts it summarized now; earlier ones already were."""
        shown = ", ".join(i for i in ids if i)
        text = f"Session {session_id} compacted: Prompt{_numbers(1, summarized)} summarized"
        text += f" (ids {shown})" if shown else ""
        self._session_changed("compact", session_id, text, origin)

    def tick(self) -> None:
        """A second or so went by: timers run, and what came wakes Sessions with no Turn."""
        now = self._clock()
        elapsed, self._last_tick = now - self._last_tick, now
        for subscription in list(self._open.values()):
            if not isinstance(subscription.watch, OnTime):
                continue
            left = subscription.watch.left - elapsed
            if left > 0:
                watch = OnTime(left=left)
                self._open[subscription.id] = subscription.model_copy(update={"watch": watch})
                continue
            del self._open[subscription.id]
            self._happened(subscription, "its time came", subscription.origin)
        for subscription in self.errands():
            self._spend(subscription.id, elapsed)
            self._advance(subscription.id)
        self._wake()

    # Inside -------------------------------------------------------------------------------

    def _opened(
        self, session_id: SessionId, watch: Watch, origin: Origin, prefix: str = "s"
    ) -> Subscription:
        subscription = Subscription(
            id=new_subscription_id(prefix),
            session_id=session_id,
            watch=watch,
            opened_turn=self._next_turn(session_id),
            in_turn=self._turns.has_turn(session_id),
            origin=origin,
        )
        self._open[subscription.id] = subscription
        shown = f"subscribed {subscription.id}: {subscription.event}"
        self._reporter.record(
            session_id,
            "subscribed",
            shown,
            subscription=subscription.id,
            watching=subscription.event,
            origin=list[JsonValue](origin.labels),
        )
        return subscription

    def _cancel(self, subscription: Subscription, why: str) -> None:
        """It no longer waits, and what came for it is dropped; an answer held back for it
        goes once nothing else holds it."""
        self._open.pop(subscription.id, None)
        self._pending = [h for h in self._pending if h.subscription.id != subscription.id]
        shown = f"subscription {subscription.id} ({subscription.event}) cancelled: {why}"
        self._reporter.record(
            subscription.session_id,
            "subscription_cancelled",
            shown,
            subscription=subscription.id,
            reason=why,
        )
        if isinstance(subscription.watch, OnErrand):
            self._closed.add(subscription.id)
            for index, part in enumerate(subscription.watch.parts):
                why_here = f"its errand {subscription.id} was cancelled: {why}"
                self._stop_part(subscription.id, index, part, why_here)
        for debt in self._release({subscription.id}):
            if debt.waiting:
                self._debts.append(debt)
                continue
            origin = from_session(debt.origin, debt.session_id)
            outcome = Outcome(
                session_id=debt.session_id, kind="answered", text=debt.answer, origin=origin
            )
            self._reply(debt.owed, outcome)

    def _waiting(self) -> list[Subscription]:
        """Those open, and those that fired and wait for their Session's Prompt."""
        waiting = [*self._open.values(), *(h.subscription for h in self._pending)]
        return list({s.id: s for s in waiting}.values())

    def _next_turn(self, session_id: SessionId) -> int:
        conversation = self._sessions.load_conversation(self._sessions.load(session_id))
        return 0 if conversation is None else conversation.next_turn

    def _release(self, done: set[SubscriptionId]) -> list[Debt]:
        """Take out the held answers that waited for any of ``done``, which no longer do."""
        released = [d for d in self._debts if done & set(d.waiting)]
        for debt in released:
            self._debts.remove(debt)
        return [
            d.model_copy(update={"waiting": tuple(w for w in d.waiting if w not in done)})
            for d in released
        ]

    def _take_debts(self, prompt: Prompt) -> list[Debt]:
        """What ``prompt``'s Turn answers: its own Prompt's replies, and the answers held back
        for the replies that woke it, which no longer wait for those."""
        debts: list[Debt] = []
        if prompt.owed:
            debts.append(
                Debt(
                    session_id=prompt.session_id,
                    owed=prompt.owed,
                    waiting=(),
                    answer="",
                    origin=prompt.origin,
                )
            )
        return [*debts, *self._release(set(prompt.woke_by))]

    def _cancel_unsaved(self, prompt: Prompt) -> None:
        """What ``prompt``'s Turn opened, which is in no Conversation, is cancelled."""
        turn = self._next_turn(prompt.session_id)
        for subscription in self._waiting():
            if (
                subscription.session_id == prompt.session_id
                and subscription.in_turn
                and subscription.opened_after(turn)
            ):
                self._cancel(subscription, "its Turn was not saved")

    def _unanswered(self, prompt: Prompt, why: str, kind: OutcomeKind) -> None:
        origin = from_session(prompt.origin, prompt.session_id)
        outcome = Outcome(session_id=prompt.session_id, kind=kind, text=why, origin=origin)
        for debt in self._take_debts(prompt):
            self._reply(debt.owed, outcome)

    def _reply(self, owed: Sequence[SubscriptionId], outcome: Outcome) -> None:
        text, origin = outcome.told, outcome.origin
        for subscription_id in owed:
            part = parse_part_id(subscription_id)
            if part is not None:
                self._part_ended(*part, outcome)
                continue
            subscription = self._open.pop(subscription_id, None)
            if subscription is not None:
                self._happened(subscription, text, origin)
                continue

            def frozen_reply(
                state: SubscriptionState, sid: SubscriptionId = subscription_id
            ) -> SubscriptionState:
                waiting = next((s for s in state.open if s.id == sid), None)
                if waiting is None:
                    return state
                kept = [s for s in state.open if s.id != sid]
                came = [Happened(subscription=waiting, text=text, origin=origin)]
                pending = [*state.pending, *self._uncut(came)]
                return state.model_copy(update={"open": kept, "pending": pending})

            self._frozen(frozen_reply)

    # Encargos (#117) ----------------------------------------------------------------------

    def _check_tree(self, session_id: SessionId, workers: Sequence[SessionId]) -> None:
        """No Encargo waits for the Session that opens it nor for one waiting for it."""
        above = {session_id}
        while True:
            more = {
                s.session_id
                for s in self.errands()
                if isinstance(s.watch, OnErrand) and s.watch.waits_for & above
            }
            if more <= above:
                break
            above |= more
        for worker in workers:
            if worker in above:
                raise ErrandCycle(session_id=session_id, worker=worker)

    def _errand(self, errand_id: SubscriptionId) -> tuple[Subscription, OnErrand] | None:
        subscription = self._open.get(errand_id)
        if subscription is None or not isinstance(subscription.watch, OnErrand):
            return None
        return subscription, subscription.watch

    def _put(self, subscription: Subscription, errand: OnErrand) -> None:
        self._open[subscription.id] = subscription.model_copy(update={"watch": errand})

    def _part_ended(self, errand_id: SubscriptionId, index: int, outcome: Outcome) -> None:
        """A part's Prompt ended: its result counts, or it goes out again, and its Encargo
        moves on; one that is no longer open drops it, and it stays in its worker's Log."""
        found = self._errand(errand_id)
        if found is None and errand_id in self._closed:
            self._reporter.log_session(
                outcome.session_id, "errand_result_dropped", errand=errand_id, text=outcome.text
            )
            return
        if found is None:

            def frozen_part(state: SubscriptionState) -> SubscriptionState:
                kept = [
                    s.model_copy(update={"watch": with_outcome(s.watch, index, outcome, "")})
                    if s.id == errand_id and isinstance(s.watch, OnErrand)
                    else s
                    for s in state.open
                ]
                return state.model_copy(update={"open": kept})

            self._frozen(frozen_part)
            return
        subscription, before = found
        served = self._turns.serving(outcome.session_id) if before.batch else ""
        after = with_outcome(before, index, outcome, served)
        if after == before:
            self._reporter.log_session(
                outcome.session_id, "errand_result_dropped", errand=errand_id, text=outcome.text
            )
            return
        self._put(subscription, after)
        coordinator = subscription.session_id
        if after.parts[index].status == "pending":
            # It goes out at the next tick: the queue is still busy with the Turn that failed.
            shown = f"errand {errand_id}: task {index + 1} goes out again: {outcome.text}"
            self._reporter.record(coordinator, "errand_retry", shown, errand=errand_id, task=index)
            return
        told = f"errand {errand_id} ({after.event}): {after.progress}"
        self._notifications.leave(coordinator, ERRAND, told)
        self._advance(errand_id)

    def _advance(self, errand_id: SubscriptionId) -> None:
        """Hand out what can go now, or wake its Session if it is settled."""
        while (found := self._errand(errand_id)) is not None:
            subscription, errand = found
            if errand.settled:
                self._settle(subscription, errand)
                return
            if not self._send_next(subscription, errand):
                return

    def _send_next(self, subscription: Subscription, errand: OnErrand) -> bool:
        """Send its next pending part that has a worker to go to; ``False`` if none."""
        busy = {p.worker for p in errand.parts if p.status == "out"}
        for index, part in enumerate(errand.parts):
            if part.status != "pending":
                continue
            worker = part.worker or next(
                (
                    w
                    for w in errand.workers
                    if w not in busy and not self._turns.busy(w) and self._turns.room_for(w)
                ),
                None,
            )
            if worker is None:
                return False  # a Lote's task waits for a free worker
            prompt_id = new_prompt_id()
            out = part.model_copy(
                update={
                    "status": "out",
                    "worker": worker,
                    "prompt_id": prompt_id,
                    "tries": part.tries + 1,
                }
            )
            self._put(subscription, errand.with_part(index, out))
            origin = subscription.origin
            if origin.chain[-1] != Link("session", subscription.session_id):
                origin = from_session(origin, subscription.session_id)
            try:
                session = self._sessions.load(worker)
            except AllInError as error:  # gone meanwhile
                why = Outcome(session_id=worker, kind="failed", text=str(error), origin=origin)
                self._part_ended(subscription.id, index, why)
                return True
            text = task_text(origin_header(origin), subscription.id, part.task)
            owed = (part_id(subscription.id, index),)
            prompt = Prompt(worker, text, origin=origin, prompt_id=prompt_id, owed=owed)
            self._turns.push(session, prompt)
            return True
        return False

    def _settle(self, subscription: Subscription, errand: OnErrand) -> None:
        """Wake its Session with the results, once; what is still out is cancelled."""
        del self._open[subscription.id]
        self._closed.add(subscription.id)
        for index, part in enumerate(errand.parts):
            if not part.ended:
                why = f"its errand {subscription.id} has what it waited for"
                self._stop_part(subscription.id, index, part, why)
                errand = errand.with_part(index, part.model_copy(update={"status": "cancelled"}))
        origins = [p.origin for p in errand.parts if p.origin is not None]
        longest = max(origins, key=lambda o: len(o.chain), default=subscription.origin)
        settled = subscription.model_copy(update={"watch": errand})
        self._reporter.log_session(
            subscription.session_id, "errand_settled", errand=subscription.id, text=report(errand)
        )
        self._happened(settled, report(errand), longest)

    def _stop_part(self, errand_id: SubscriptionId, index: int, part: Part, why: str) -> None:
        """Cancel what a part's worker does for it: its Prompt, queued or running, and what
        its answer, held back, still waits for."""
        owed = part_id(errand_id, index)
        for debt in [d for d in self._debts if owed in d.owed]:
            self._debts.remove(debt)
            for waiting in debt.waiting:
                inner = next((s for s in self._waiting() if s.id == waiting), None)
                if inner is not None:
                    self._cancel(inner, why)
        if part.status == "out" and part.worker is not None:
            self._turns.cancel_prompt(part.worker, part.prompt_id, why)

    def _spend(self, errand_id: SubscriptionId, elapsed: float) -> None:
        """Its parts out spend their deadline while their worker's Server is ready; one past
        it is cancelled, as no answer."""
        found = self._errand(errand_id)
        if found is None or found[1].deadline is None:
            return
        subscription, errand = found
        deadline = found[1].deadline
        late: list[tuple[int, Part]] = []
        for index, part in enumerate(errand.parts):
            if part.status != "out" or part.worker is None or not self._turns.ready(part.worker):
                continue
            spent = part.spent + elapsed
            if spent < deadline:
                errand = errand.with_part(index, part.model_copy(update={"spent": spent}))
                continue
            late.append((index, part))
            gone = {"status": "late", "spent": spent, "prompt_id": NO_PROMPT_ID}
            errand = errand.with_part(index, part.model_copy(update=gone))
        self._put(subscription, errand)
        for index, part in late:
            self._stop_part(errand_id, index, part, f"its errand {errand_id}'s deadline passed")
        if late:
            told = f"errand {errand_id} ({errand.event}): {errand.progress}"
            self._notifications.leave(subscription.session_id, ERRAND, told)

    def _session_changed(
        self, kind: Literal["rewind", "compact"], session_id: SessionId, text: str, origin: Origin
    ) -> None:
        watch = OnSession(kind=kind, session_id=session_id)
        for subscription in list(self._open.values()):
            if subscription.watch == watch:
                self._happened(subscription, text, origin)

        def frozen_change(state: SubscriptionState) -> SubscriptionState:
            came = self._uncut(
                [
                    Happened(subscription=s, text=text, origin=origin)
                    for s in state.open
                    if s.watch == watch
                ]
            )
            return state.model_copy(update={"pending": [*state.pending, *came]}) if came else state

        self._frozen(frozen_change)

    def _uncut(self, came: list[Happened]) -> list[Happened]:
        """What a frozen Profile keeps of ``came``: a chain past its cap is cut there too,
        without a Notificación, its Session being out of view."""
        repeats = self._settings().repeats
        return [h for h in came if h.origin.chain.count(h.subscription.link) < repeats]

    def _happened(self, subscription: Subscription, text: str, origin: Origin) -> None:
        """It waits for its Session's next Prompt, unless its chain already went through it as
        many times as ``subscription.repeats``: then the chain is cut, and its Session told."""
        repeats = self._settings().repeats
        times = origin.chain.count(subscription.link)
        session_id = subscription.session_id
        if times >= repeats:
            told = (
                f"{subscription.event} ({subscription.id}) did not wake you: it already did"
                f" {times} times in one chain (subscription.repeats is {repeats})"
            )
            self._notifications.leave(session_id, SUBSCRIPTION, told)
            self._reporter.record(
                session_id,
                "subscription_cut",
                f"chain cut: {told}",
                subscription=subscription.id,
                origin=list[JsonValue](origin.labels),
            )
            return
        self._pending.append(Happened(subscription=subscription, text=text, origin=origin))
        self._reporter.log_session(
            session_id, "subscription_event", subscription=subscription.id, text=text
        )

    def _wake(self) -> None:
        """Each Session with events and no Turn gets them in one Prompt, a Continuation."""
        most = self._settings().events
        for session_id in dict.fromkeys(h.subscription.session_id for h in self._pending):
            if self._turns.has_turn(session_id) or self._turns.waking(session_id):
                continue
            came = [h for h in self._pending if h.subscription.session_id == session_id][:most]
            for happened in came:
                self._pending.remove(happened)
            try:
                session = self._sessions.load(session_id)
            except AllInError as error:  # gone meanwhile: nobody to wake
                self._reporter.error(error, session_id)
                continue
            longest = max(came, key=lambda h: len(h.origin.chain))
            origin = Origin(chain=(*longest.origin.chain, longest.subscription.link))
            events = "\n\n".join(
                f"{h.subscription.event} ({h.subscription.id}): {h.text}" for h in came
            )
            woke_by = tuple(dict.fromkeys(h.subscription.id for h in came))
            prompt = Prompt(
                session_id, f"{origin_header(origin)}\n{events}", origin=origin, woke_by=woke_by
            )
            self._turns.push(session, prompt)


def _check_own(session_id: SessionId, origin: Origin) -> None:
    """A Session only subscribes itself; a human or Main, any (ADR-0011)."""
    sender = origin.sender
    if sender is not None and sender != session_id:
        raise SubscriptionNotOwn(session_id=session_id, sender=sender)


def _numbers(first: int, last: int) -> str:
    return f" {first}" if first == last else f"s {first}\N{EN DASH}{last}"
