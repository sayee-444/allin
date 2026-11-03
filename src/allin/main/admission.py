"""The Admission of a Step to its Server: whether it starts now, by the Server's Wait list
(ADR-0004, ADR-0022), and what a waiting Session's Window says on its border.

The list is ordered by class (Continuations, then new Prompts, then those a Proceso adherido
brought on its own, by its Iniciativa, ADR-0026, then ``describir``) and, within each class, by
arrival. A free slot goes to the first; with ``--kv-unified`` a Step also needs
room in the pool: its measured input plus its Output cap must fit in ``-c`` minus what every
Step in course reserved, whatever Main runs it. One that does not fit may be overtaken by one
behind it that does, until it has waited its patience (``firm_at``): from then on nobody
overtakes it, in any Main.
"""

from dataclasses import dataclass
from typing import Literal

from allin.core.wait_list import WaitClass, Waiter, WaitListView
from allin.main.events import (
    ServerAsleep,
    ServerFailed,
    ServerLoading,
    ServerReady,
    ServerRestarting,
    ServerState,
)

# Whether the Step starts now, waits for those ahead of it, or waits for room in the pool.
type Verdict = Literal["go", "queued", "pool"]

_RANK: dict[WaitClass, int] = {"continuation": 0, "prompt": 1, "initiative": 2, "describe": 3}


@dataclass(frozen=True)
class Admission:
    verdict: Verdict
    position: int  # its place in the Server's Wait list, from 1


def admit(candidate: Waiter, listed: WaitListView, pool: int | None, now: float) -> Admission:
    """Whether ``candidate`` may take a free slot now. ``pool``: the Server's shared pool, in
    tokens; ``None`` without ``--kv-unified``, where only the order counts. ``now``: wall
    clock."""
    order = _ordered(candidate, listed.waiting)
    position = order.index(candidate) + 1
    free = None if pool is None else pool - sum(r.tokens for r in listed.reserved)
    for ahead in order[: position - 1]:
        if free is None or ahead.need <= free:  # it goes first
            return Admission("queued", position)
        if now >= ahead.firm_at:  # waited enough: it blocks everyone behind it
            return Admission("pool", position)
    if free is not None and candidate.need > free:
        return Admission("pool", position)
    return Admission("go", position)


def _ordered(candidate: Waiter, waiting: tuple[Waiter, ...]) -> list[Waiter]:
    """The waiters with ``candidate`` in its own place, first to go first."""
    mine = (candidate.main, candidate.pid, candidate.session)
    others = [w for w in waiting if (w.main, w.pid, w.session) != mine]
    return sorted(
        [*others, candidate], key=lambda w: (_RANK[w.kind], w.since, w.main, w.pid, w.session)
    )


# Why a Session's next Step waits, as its Window's border says it.
type WaitReason = Literal[
    "queued", "slot", "pool", "loading", "restarting", "down", "asleep", "failed", "link"
]

_TEXTS: dict[WaitReason, str] = {
    "slot": "waiting for a slot",
    "pool": "waiting for room in the pool",
    "loading": "Server loading",
    "restarting": "Server restarting",
    "down": "Server down",
    "asleep": "Supervisor asleep",
    "failed": "Supervisor failed",
    "link": "waiting for its Enlace to change",
}


@dataclass(frozen=True)
class Waiting:
    reason: WaitReason
    position: int | None = None  # its place in the Wait list, when queued

    def text(self) -> str:
        if self.reason == "queued":
            return "queued" if self.position is None else f"queued ({_ordinal(self.position)})"
        return _TEXTS[self.reason]


def server_waiting(state: ServerState | None) -> Waiting | None:
    """Why the Sessions of a Server in ``state`` wait; ``None`` once it is ready."""
    match state:
        case ServerReady():
            return None
        case ServerLoading():
            return Waiting("loading")
        case ServerRestarting():
            return Waiting("restarting")
        case ServerAsleep():
            return Waiting("asleep")
        case ServerFailed():
            return Waiting("failed")
        case _:
            return Waiting("down")


def _ordinal(n: int) -> str:
    if n % 100 in {11, 12, 13}:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"
