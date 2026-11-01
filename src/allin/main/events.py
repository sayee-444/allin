"""Events consumed by Main's dispatcher, in arrival order (ADR-0001)."""

from dataclasses import dataclass
from typing import Any

from allin.core.errors import AllInError
from allin.core.ids import BoxId, ServerKey, SessionId, SupervisorName
from allin.core.origin import HUMAN, Origin
from allin.main.conversation import Step
from allin.main.slots import ServerProps
from allin.main.turn import OutputKind
from allin.supervisor.state import FailureKind


@dataclass(frozen=True)
class WindowConnected:
    session_id: SessionId


@dataclass(frozen=True)
class InputReceived:
    session_id: SessionId
    text: str


@dataclass(frozen=True)
class DraftReceived:
    """A Window's Draft, shown block first, once the user stopped typing for a moment."""

    session_id: SessionId
    blocks: tuple[str, ...]


@dataclass(frozen=True)
class WindowClosed:
    session_id: SessionId
    returncode: int


@dataclass(frozen=True)
class ErrorOccurred:
    error: AllInError
    session_id: SessionId | None = None


@dataclass(frozen=True)
class MainCommandEntered:
    """A line typed in Main's own input, parsed as a Command main; F2's Command sessions
    arrive the same way, with their Origin."""

    text: str
    origin: Origin = HUMAN


@dataclass(frozen=True)
class TurnOutput:
    """Text the LLM streamed during a Session's Turn."""

    session_id: SessionId
    kind: OutputKind
    text: str


@dataclass(frozen=True)
class TurnRaw:
    """One raw event of a Turn's stream, as JSON; it always goes to Main's Log."""

    session_id: SessionId
    event: dict[str, Any]


@dataclass(frozen=True)
class StepEnded:
    """One Step of a Session's running Turn ended; the Turn may go on with another."""

    session_id: SessionId
    step: Step


@dataclass(frozen=True)
class TurnEnded:
    """A Session's Turn finished with its Steps, or stopped with ``error``."""

    session_id: SessionId
    steps: list[Step] | None = None
    error: AllInError | None = None


@dataclass(frozen=True)
class ServerDown:
    """The Server does not answer, or does not look like llama-server."""


@dataclass(frozen=True)
class ServerLoading:
    """The Server answers 503: it is loading its model."""


@dataclass(frozen=True)
class ServerReady:
    """The Server is ready, with these props; said again when they change."""

    props: ServerProps
    # Why its Supervisor runs the last Settings that started instead of the pending ones.
    fallback: str | None = None


@dataclass(frozen=True)
class ServerAsleep:
    """Its Supervisor is not running: it wakes when work comes for it (ADR-0022)."""

    slept_by_human: bool = False  # then only a human wakes it


@dataclass(frozen=True)
class ServerFailed:
    """Its Supervisor gave up, and said why in its ``.json`` (ADR-0008)."""

    failure: FailureKind
    reason: str
    # The Supervisors active while it did not fit in VRAM: who holds the GPU.
    gpu_holders: tuple[SupervisorName, ...] = ()


@dataclass(frozen=True)
class ServerRestarting:
    """Its Supervisor drains it for a restart: no new Step starts; with ``now``, the Turns
    running on it are suspended and their Steps repeated after it (ADR-0008, ADR-0023)."""

    now: bool = False


type ServerState = (
    ServerDown | ServerLoading | ServerReady | ServerAsleep | ServerFailed | ServerRestarting
)


@dataclass(frozen=True)
class ServerProgress:
    """The Server read more of some Step's input (``/slots``): a Step that has sent nothing
    yet is not stalled, since llama sends nothing while it reads (build 10909, ADR-0023)."""

    server: ServerKey


@dataclass(frozen=True)
class Tick:
    """A second went by: Main looks for Steps that stopped making progress (ADR-0023)."""


@dataclass(frozen=True)
class ServerChanged:
    """What a Server says of itself changed (ADR-0004); the Server watch tells it."""

    server: ServerKey
    state: ServerState


@dataclass(frozen=True)
class SlotFreed:
    """A slot lock another Main held is free, while Prompts here waited for one."""

    server: ServerKey


@dataclass(frozen=True)
class ConfirmationRequested:
    """A running Turn asks the user yes or no; it waits, without its slot, for the answer."""

    session_id: SessionId
    title: str
    text: str


@dataclass(frozen=True)
class BoxAnswered:
    """A Notice or Confirmation was answered, in Main's Chat or in a Window."""

    box_id: BoxId
    answer: bool | None


@dataclass(frozen=True)
class Shutdown:
    pass


type MainEvent = (
    WindowConnected
    | InputReceived
    | DraftReceived
    | WindowClosed
    | ErrorOccurred
    | MainCommandEntered
    | TurnOutput
    | TurnRaw
    | StepEnded
    | TurnEnded
    | ServerChanged
    | SlotFreed
    | ServerProgress
    | Tick
    | ConfirmationRequested
    | BoxAnswered
    | Shutdown
)
