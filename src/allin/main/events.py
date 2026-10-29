"""Events consumed by Main's dispatcher, in arrival order (ADR-0001)."""

from dataclasses import dataclass

from allin.core.errors import AllInError


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
class Shutdown:
    pass


type MainEvent = (
    WindowConnected | InputReceived | WindowClosed | ErrorOccurred | MainCommandEntered | Shutdown
)
