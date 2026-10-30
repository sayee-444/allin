"""Events consumed by Main's dispatcher, in arrival order (ADR-0001)."""

from dataclasses import dataclass
from typing import Any

from allin.core.errors import AllInError
from allin.main.conversation import Step
from allin.main.slots import ServerProps
from allin.main.turn import OutputKind


@dataclass(frozen=True)
class WindowConnected:
    session_id: str


@dataclass(frozen=True)
class InputReceived:
    session_id: str
    text: str


@dataclass(frozen=True)
class DraftReceived:
    """A Window's Draft, shown block first, once the user stopped typing for a moment."""

    session_id: str
    blocks: tuple[str, ...]


@dataclass(frozen=True)
class WindowClosed:
    session_id: str
    returncode: int


@dataclass(frozen=True)
class ErrorOccurred:
    error: AllInError
    session_id: str | None = None


@dataclass(frozen=True)
class MainCommandEntered:
    """A line the user typed in Main's own input, parsed as a Command main."""

    text: str


@dataclass(frozen=True)
class TurnOutput:
    """Text the LLM streamed during a Session's Turn."""

    session_id: str
    kind: OutputKind
    text: str


@dataclass(frozen=True)
class TurnRaw:
    """One raw event of a Turn's stream, as JSON; it always goes to Main's Log."""

    session_id: str
    event: dict[str, Any]


@dataclass(frozen=True)
class StepEnded:
    """One Step of a Session's running Turn ended; the Turn may go on with another."""

    session_id: str
    step: Step


@dataclass(frozen=True)
class TurnEnded:
    """A Session's Turn finished with its Steps, or stopped with ``error``."""

    session_id: str
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


type ServerState = ServerDown | ServerLoading | ServerReady


@dataclass(frozen=True)
class SlotFreed:
    """A slot lock another Main held is free, while Prompts here waited for one."""


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
    | ServerDown
    | ServerLoading
    | ServerReady
    | SlotFreed
    | Shutdown
)
