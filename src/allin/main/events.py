"""Events consumed by Main's dispatcher, in arrival order (ADR-0001)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import JsonValue

from allin.core.errors import AllInError
from allin.core.ids import AttachedName, BoxId, ServerKey, SessionId, SupervisorName
from allin.core.origin import HUMAN, Origin
from allin.ipc.messages import OrganizerAction
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
    interrupt: bool = False  # Shift+Enter: cut the running Turn first (ADR-0024)


@dataclass(frozen=True)
class QueuedWithdrawn:
    """Up in a Window: its Session's last queued Prompt goes back to the input."""

    session_id: SessionId


@dataclass(frozen=True)
class DraftReceived:
    """A Window's Draft, shown block first, once the user stopped typing for a moment."""

    session_id: SessionId
    blocks: tuple[str, ...]


@dataclass(frozen=True)
class WindowUsed:
    """The human clicked in a Session's Window (#52)."""

    session_id: SessionId


@dataclass(frozen=True)
class OrganizerConnected:
    """The Organizer is up (#53)."""


@dataclass(frozen=True)
class OrganizerActed:
    """The human clicked or pressed a key in the Organizer for one of its actions (#53)."""

    action: OrganizerAction
    session_id: str


@dataclass(frozen=True)
class OrganizerClosed:
    """The Organizer's xterm, ``pid``, is gone (#53)."""

    pid: int


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
    # Whether its slots share one pool of ``-c`` tokens (``--kv-unified``), as its
    # Supervisor's running arguments say; assumed for a Server without one, which llama does
    # by itself when its slots are not given.
    shared_pool: bool = True


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
    sleep: bool = False  # it drains because a human put it to sleep


type ServerState = (
    ServerDown | ServerLoading | ServerReady | ServerAsleep | ServerFailed | ServerRestarting
)


@dataclass(frozen=True)
class InputMeasured:
    """How many tokens the Server counts in the input of a Session's next Step (ADR-0004);
    ``None`` if it could not say. ``ticket`` tells which measure it answers."""

    server: ServerKey
    session_id: SessionId
    ticket: int
    tokens: int | None


@dataclass(frozen=True)
class ServerProgress:
    """The Server read more of some Step's input (``/slots``): a Step that has sent nothing
    yet is not stalled, since llama sends nothing while it reads (build 10909, ADR-0023)."""

    server: ServerKey


@dataclass(frozen=True)
class Tick:
    """A second went by: Main looks for Steps that stopped making progress (ADR-0023)."""


@dataclass(frozen=True)
class ThemesChanged:
    """A Theme's file changed, appeared or went away (#47): every look is resolved again."""


@dataclass(frozen=True)
class ServerChanged:
    """What a Server says of itself changed (ADR-0004); the Server watch tells it."""

    server: ServerKey
    state: ServerState


@dataclass(frozen=True)
class SupervisorAdopted:
    """The ``llama serve`` that ``/server adopt`` ended freed its port: its new Supervisor
    launches it again."""

    supervisor: SupervisorName


@dataclass(frozen=True)
class AttachedProcessEnded:
    """The process of the Proceso adherido ``name`` whose warden was ``pid`` ended, detached or
    by itself."""

    name: AttachedName
    pid: int


@dataclass(frozen=True)
class MethodAnswered:
    """A ``/call`` or ``@call`` to a Proceso adherido's method, answered in its own thread;
    ``report`` tells how it went, in Main's thread (#114)."""

    report: Callable[[], None]


@dataclass(frozen=True)
class MethodProgressed:
    """A Proceso adherido's method told how far a call went: ``line`` shows in the Window of
    the Session that called it, or in Main's Chat for ``None`` (ADR-0012)."""

    session_id: SessionId | None
    line: str


@dataclass(frozen=True)
class MethodAnnounced:
    """A Proceso adherido's method announced something (``Fproc.announce``): it wakes the
    Sessions subscribed to the process, after ``origin``, the call's chain (#116)."""

    process: AttachedName
    text: str
    origin: Origin


@dataclass(frozen=True)
class SlotFreed:
    """A slot lock another Main held is free, while Prompts here waited for one."""

    server: ServerKey


@dataclass(frozen=True)
class ToolsStarted:
    """A running Turn's Step called tools: they run now, without its slot (ADR-0004)."""

    session_id: SessionId
    names: tuple[str, ...]
    step: Step  # the Step that called them, which an interrupted Turn keeps (ADR-0024)


@dataclass(frozen=True)
class Queried:
    """A Command main that only reads ran, and this is what it read: Consultar (#118)."""

    state: JsonValue


# How a Command main went: it ran, a human said no at its Confirmation, or why it failed; one
# that only reads, what it read.
type CommandOutcome = Literal["ok", "rejected"] | Queried | AllInError
# Who hears how a Command main went, maybe later: a Session's tool, through its Command session.
type OnOutcome = Callable[[CommandOutcome], None]


@dataclass(frozen=True)
class SessionCommandResult:
    """How a Command session went, for whoever asked for it (ADR-0025)."""

    error: str | None = None  # why it was refused or failed; ``None`` if it ran
    # How its Server came back, after an ``@server restart``.
    server: Literal["ready", "failed"] | None = None
    state: JsonValue = None  # what one that only reads read: ``@status`` (#118)

    def as_json(self) -> JsonValue:
        """As the tool that asked for it reads it, on its channel."""
        shown: dict[str, JsonValue] = (
            {"result": "ok"} if self.error is None else {"error": self.error}
        )
        if self.server is not None:
            shown["server"] = self.server
        if self.state is not None:
            shown["state"] = self.state
        return shown


@dataclass(frozen=True)
class SessionCommandAsked:
    """A tool of a running Turn asks for a Command session, ``@name …``, with the Turn's
    ``origin``; ``answer`` gets how it went, from Main's thread (ADR-0025)."""

    session_id: SessionId
    text: str
    origin: Origin
    answer: Callable[[SessionCommandResult], bool]  # ``False``: its tool no longer waits


@dataclass(frozen=True)
class ToolsEnded:
    """A running Turn's tools gave back what they said, in ``step``'s results: its next Step,
    a Continuation, waits for a slot. It reads ``grows`` more tokens at most than the Step
    before: what that one generated, and what the tools gave back, a character each."""

    session_id: SessionId
    step: Step
    grows: int


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
    ThemesChanged
    | WindowUsed
    | WindowConnected
    | InputReceived
    | QueuedWithdrawn
    | DraftReceived
    | WindowClosed
    | OrganizerConnected
    | OrganizerActed
    | OrganizerClosed
    | ErrorOccurred
    | MainCommandEntered
    | TurnOutput
    | TurnRaw
    | StepEnded
    | TurnEnded
    | ServerChanged
    | SlotFreed
    | SupervisorAdopted
    | AttachedProcessEnded
    | MethodAnswered
    | MethodProgressed
    | MethodAnnounced
    | ServerProgress
    | InputMeasured
    | Tick
    | ConfirmationRequested
    | ToolsStarted
    | ToolsEnded
    | SessionCommandAsked
    | BoxAnswered
    | Shutdown
)
