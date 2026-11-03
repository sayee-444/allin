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
# Why it failed: the Server did not fit in VRAM, its port was taken, it crashed too often, its
# Settings are invalid, or its model file is not there (then ``reason`` is the file's path).
type FailureKind = Literal["vram", "port_in_use", "crashes", "settings", "model_missing"]
# A restart asked for: once every Step on the Server ends, or cutting them short now.
type RestartKind = Literal["drain", "now"]


class SupervisorState(BaseModel, frozen=True):
    status: SupervisorStatus
    pid: int | None = None  # the Supervisor's own
    server_pid: int | None = None  # llama serve's
    arguments: list[str] = []  # the Server's, as it runs now
    cwd: str | None = None  # the folder it runs in, when its Settings name one
    port: int | None = None
    slept_by_human: bool = False
    last_exit_code: int | None = None
    failure: FailureKind | None = None
    reason: str | None = None  # the failure's details, as the Server or the system said them
    restart: RestartKind | None = None  # while draining for a restart
    going_to_sleep: bool = False  # while draining because a human put it to sleep
    # Why it runs on the last Settings that started instead of the pending ones (ADR-0008).
    fallback_reason: str | None = None


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
