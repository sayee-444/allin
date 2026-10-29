"""Events consumed by Main's dispatcher, in arrival order (ADR-0001)."""

from dataclasses import dataclass
from typing import Any

from allin.core.errors import AllInError
from allin.main.conversation import Step
from allin.main.turn import OutputKind


@dataclass(frozen=True)
class WindowConnected:
    session_id: str


@dataclass(frozen=True)
class InputReceived:
    session_id: str
    text: str


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
class LlmAvailable:
    """The LLM's server answers ``/health`` with OK again, after Main's queue paused for it."""


@dataclass(frozen=True)
class Shutdown:
    pass


type MainEvent = (
    WindowConnected
    | InputReceived
    | WindowClosed
    | ErrorOccurred
    | MainCommandEntered
    | TurnOutput
    | TurnRaw
    | StepEnded
    | TurnEnded
    | LlmAvailable
    | Shutdown
)
