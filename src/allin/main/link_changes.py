"""A Session's Enlace changing while Main runs (ADR-0022): what to do with it at each look.

The change is atomic: the Enlace moves only once the new Server is ready and the Session has no
active Turn, so no Turn changes model half-way; if the new Supervisor cannot start, the Enlace
stays where it was and Main says why.
"""

from dataclasses import dataclass
from typing import Literal

from allin.core.ids import SessionId
from allin.main.events import (
    ServerAsleep,
    ServerFailed,
    ServerReady,
    ServerRestarting,
    ServerState,
)
from allin.main.links import Link

# How long what a just-woken Supervisor's ``.json`` said before it ran may still be stale: a
# failure or sleep seen in that time ends the change only once it has shown itself running.
STALE_FOR = 10.0

type Verdict = Literal["wait", "apply", "fail"]


@dataclass
class LinkChange:
    session_id: SessionId
    to: Link
    since: float  # Main's clock when it was asked
    live: bool = False  # whether its Server has shown itself running since


def judge(change: LinkChange, state: ServerState | None, busy: bool, now: float) -> Verdict:
    """What to do with a pending change, given what its Server says now and whether the
    Session has an active Turn."""
    match state:
        case ServerReady():
            change.live = True
            return "wait" if busy else "apply"
        case ServerAsleep(slept_by_human=True):  # never woken but by a human (ADR-0022)
            return "fail"
        case ServerFailed() | ServerAsleep() | ServerRestarting(sleep=True):
            return "fail" if change.live or now - change.since >= STALE_FOR else "wait"
        case None:
            return "wait"
        case _:  # loading, restarting, or not answering yet: it runs
            change.live = True
            return "wait"


def failure_text(change: LinkChange, state: ServerState | None, before: str) -> str:
    """Why the change failed and what to do, for Main's Chat and the Session's Window."""
    server = change.to.describe()
    stays = f"the Enlace stays on {before}"
    match state:
        case ServerFailed(failure="vram", gpu_holders=holders) if holders:
            names = ", ".join(holders)
            asleep = " or ".join(f"/server sleep {name}" for name in holders)
            why = (
                f"{server} does not fit in VRAM: the GPU is held by Supervisor {names};"
                f" free it with {asleep}, or lower its Settings with /server set"
            )
        case ServerFailed(failure="vram"):
            why = (
                f"{server} does not fit in VRAM and no Supervisor is active, so something"
                " outside AllIn holds the GPU; lower its Settings with /server set"
            )
        case ServerFailed(failure=failure, reason=reason):
            why = f"{server} failed ({failure}): {reason}"
        case ServerAsleep(slept_by_human=True):
            name = change.to.supervisor
            why = f"{server} was put to sleep by a human; wake it with /server wake {name}"
        case _:
            why = f"{server} fell asleep before it was ready"
    return f"Enlace not changed: {why}; {stays}"
