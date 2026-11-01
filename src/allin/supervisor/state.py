"""What a Supervisor tells about itself in ``servers/<name>.json`` (ADR-0008, ADR-0022).

Only the Supervisor writes it; Main reads it. There is no socket and no FIFO between them,
since either side may not exist.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from allin.core.files import write_atomically
from allin.core.ids import SupervisorName

type SupervisorStatus = Literal["starting", "ready", "crashed", "failed", "draining", "asleep"]
# Why it failed: the Server did not fit in VRAM, its port was taken, it crashed too often, or
# its Settings are invalid.
type FailureKind = Literal["vram", "port_in_use", "crashes", "settings"]


class SupervisorState(BaseModel, frozen=True):
    status: SupervisorStatus
    pid: int | None = None  # the Supervisor's own
    server_pid: int | None = None  # llama serve's
    arguments: list[str] = []  # the Server's, as it runs now
    port: int | None = None
    slept_by_human: bool = False
    last_exit_code: int | None = None
    failure: FailureKind | None = None
    reason: str | None = None  # the failure's details, as the Server or the system said them


def state_path(servers: Path, name: SupervisorName) -> Path:
    return servers / f"{name}.json"


def read_state(servers: Path, name: SupervisorName) -> SupervisorState | None:
    """``None`` for a Supervisor that never ran, or whose file cannot be read."""
    try:
        return SupervisorState.model_validate_json(state_path(servers, name).read_bytes())
    except (OSError, ValidationError):
        return None


def write_state(servers: Path, name: SupervisorName, state: SupervisorState) -> None:
    servers.mkdir(parents=True, exist_ok=True)
    write_atomically(state_path(servers, name), state.model_dump_json(indent=2))
