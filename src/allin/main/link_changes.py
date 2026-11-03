"""A Session's Enlace changing while Main runs (ADR-0022): what to do with it at each look.

The change is atomic: the Enlace moves only once the new Server is ready and the Session has no
active Turn, so no Turn changes model half-way; if the new Supervisor cannot start, the Enlace
stays where it was and Main says why.
"""

from dataclasses import dataclass, replace
from typing import Literal

from allin.core.ids import SessionId, SupervisorName
from allin.main.events import (
    ServerAsleep,
    ServerDown,
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


@dataclass(frozen=True)
class LinkChange:
    session_id: SessionId
    to: Link
    since: float  # Main's clock when it was asked
    live: bool = False  # whether its Server has shown itself running since


def judge(
    change: LinkChange, state: ServerState | None, busy: bool, now: float
) -> tuple[Verdict, LinkChange]:
    """What to do with a pending change, given what its Server says now and whether the
    Session has an active Turn; and the change as it stands after this look."""
    running = replace(change, live=True)
    match state:
        case ServerReady():
            return ("wait" if busy else "apply"), running
        case ServerAsleep(slept_by_human=True):  # never woken but by a human (ADR-0022)
            return "fail", change
        case ServerFailed() | ServerAsleep() | ServerRestarting(sleep=True):
            stale = not change.live and now - change.since < STALE_FOR
            return ("wait" if stale else "fail"), change
        case ServerDown() if change.to.supervisor is None:  # Main cannot start that one
            return "fail", change
        case None:
            return "wait", change
        case _:  # loading, restarting, or not answering yet: it runs
            return "wait", running


def failure_text(change: LinkChange, state: ServerState | None, before: Link | None) -> str:
    """Why the change failed and what to do, for Main's Chat and the Session's Window;
    ``before`` is ``None`` if the Session's Enlace cannot be resolved."""
    server = change.to.describe()
    stays = f"the Enlace stays on {before.describe() if before else 'one that cannot be resolved'}"
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
        case ServerFailed(failure="model_missing", reason=model):
            why = f"{server} has no model: {missing_model(change.to.supervisor, model)}"
        case ServerFailed(failure=failure, reason=reason):
            why = f"{server} failed ({failure}): {reason}"
        case ServerAsleep(slept_by_human=True):
            name = change.to.supervisor
            why = f"{server} was put to sleep by a human; wake it with /server wake {name}"
        case ServerDown():
            why = f"{server} cannot be reached"
        case _:
            why = f"{server} fell asleep before it was ready"
    return f"Enlace not changed: {why}; {stays}"


def missing_model(name: SupervisorName | None, model: str) -> str:
    """The model file a Supervisor did not find, and the command that gives it one (#158)."""
    return f"{model} is not there; give it one with /server set {name} model=<path>"
