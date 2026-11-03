"""Consultar (ADR-0011, #118): the state a Session has within reach, read only, of the active
Profile alone: the queue, the Subscriptions and Encargos, the Procesos adheridos, the Servers
its Enlaces watch and the Supervisors with their capacity. The tree of Mains comes with F3."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel, JsonValue

from allin.core.errors import InvalidArguments
from allin.core.ids import AttachedName, ServerKey, SessionId, SubscriptionId, SupervisorName

type StatusPart = Literal["queue", "subscriptions", "attached", "servers", "supervisors"]
type ServerStateName = Literal["ready", "down", "loading", "asleep", "failed", "restarting"]
PARTS: tuple[StatusPart, ...] = get_args(StatusPart.__value__)
PARTS_SHOWN = "|".join(PARTS)
USAGE = f"/status <sid> [{PARTS_SHOWN}]"

# Whether a Session is one of the active Profile's: Consultar shows those alone.
type InActive = Callable[[SessionId], bool]


class QueuedSession(BaseModel, frozen=True):
    """A Session of the queue: with a Turn running, Prompts waiting for one, or both."""

    session: SessionId
    running: bool
    queued: int


class WatchedServer(BaseModel, frozen=True):
    """A Server an Enlace of this Main watches: how it is and how much of it the active
    Profile's Turns take."""

    key: ServerKey
    server: str  # as its Enlace describes it
    state: ServerStateName
    slots: int | None  # its Turns at once, once ready
    busy: int  # the active Profile's Turns running on it
    model: str | None


class QueueView(BaseModel, frozen=True):
    """The queue as the TurnQueue sees it, of the active Profile's Sessions."""

    sessions: tuple[QueuedSession, ...]
    servers: tuple[WatchedServer, ...]


class SubscriptionView(BaseModel, frozen=True):
    """An open Suscripción or Encargo, with an Encargo's progress."""

    id: SubscriptionId
    session: SessionId
    event: str  # as ``@subscribe`` lists it
    progress: str | None  # an Encargo's, as "1 of 3 answered"


class AttachedView(BaseModel, frozen=True):
    """A Proceso adherido and the methods it offers."""

    name: AttachedName
    methods: tuple[str, ...]


class SupervisorView(BaseModel, frozen=True):
    """A Supervisor, how it is, and its Server's capacity while an Enlace watches it."""

    name: SupervisorName
    status: str  # as ``/server`` lists it
    default: bool  # the default Enlace's
    server: WatchedServer | None = None


@dataclass(frozen=True)
class Sources:
    """Where Consultar reads from; none of it changes anything."""

    queue: Callable[[InActive], QueueView]
    subscriptions: Callable[[], list[SubscriptionView]]
    attached: Callable[[], list[AttachedView]]
    supervisors: Callable[[], list[SupervisorView]]


def parse_part(text: str | None) -> StatusPart | None:
    """``None`` is every part; one not known is an error with the usage."""
    if text is None:
        return None
    for part in PARTS:
        if part == text:
            return part
    raise InvalidArguments(usage=USAGE)


class StatusReport(BaseModel, frozen=True):
    """What Consultar read, as the tool gets it and as Main's Chat shows it; a part not asked
    for is ``None``."""

    queue: tuple[QueuedSession, ...] | None = None
    subscriptions: tuple[SubscriptionView, ...] | None = None
    attached: tuple[AttachedView, ...] | None = None
    servers: tuple[WatchedServer, ...] | None = None
    supervisors: tuple[SupervisorView, ...] | None = None

    def as_json(self) -> JsonValue:
        """The parts it read, as the tool gets them."""
        dumped: dict[str, JsonValue] = self.model_dump(mode="json")
        return {part: items for part, items in dumped.items() if items is not None}

    def lines(self) -> list[str]:
        """The parts it read, as Main's Chat shows them: a heading and a line for each."""
        lines: list[str] = []
        if self.queue is not None:
            lines += _section("queue", [f"{q.session}: {_queued(q)}" for q in self.queue])
        if self.subscriptions is not None:
            lines += _section(
                "subscriptions",
                [
                    f"{s.id} of {s.session}: {s.event}" + (f", {s.progress}" if s.progress else "")
                    for s in self.subscriptions
                ],
            )
        if self.attached is not None:
            lines += _section(
                "attached",
                [f"{a.name}: {', '.join(a.methods) or 'no methods'}" for a in self.attached],
            )
        if self.servers is not None:
            lines += _section("servers", [f"{s.server}: {_capacity(s)}" for s in self.servers])
        if self.supervisors is not None:
            lines += _section("supervisors", [_supervisor(s) for s in self.supervisors])
        return lines


def read(sources: Sources, part: StatusPart | None, in_active: InActive) -> StatusReport:
    """What ``part`` (or every part) shows now, of the active Profile's Sessions alone."""

    def wanted(name: StatusPart) -> bool:
        return part is None or part == name

    queue = sources.queue(in_active)
    return StatusReport(
        queue=queue.sessions if wanted("queue") else None,
        subscriptions=_subscriptions(sources, in_active) if wanted("subscriptions") else None,
        attached=tuple(sources.attached()) if wanted("attached") else None,
        servers=queue.servers if wanted("servers") else None,
        supervisors=_supervisors(sources, queue) if wanted("supervisors") else None,
    )


def _subscriptions(sources: Sources, in_active: InActive) -> tuple[SubscriptionView, ...]:
    return tuple(s for s in sources.subscriptions() if in_active(s.session))


def _supervisors(sources: Sources, queue: QueueView) -> tuple[SupervisorView, ...]:
    """Each with its Server's capacity, when an Enlace watches it (its key is its name)."""
    watched = {server.key: server for server in queue.servers}
    return tuple(
        s.model_copy(update={"server": watched.get(ServerKey(s.name))})
        for s in sources.supervisors()
    )


def _section(name: str, items: list[str]) -> list[str]:
    return [f"{name}:", *(f"  {item}" for item in items)] if items else [f"{name}: none"]


def _queued(queued: QueuedSession) -> str:
    running = "running" if queued.running else "waiting"
    return running + (f", {queued.queued} queued" if queued.queued else "")


def _capacity(server: WatchedServer) -> str:
    if server.slots is None:
        return server.state
    model = f" ({server.model})" if server.model else ""
    return f"{server.state}, {server.busy} of {server.slots} slot(s) busy{model}"


def _supervisor(supervisor: SupervisorView) -> str:
    default = " (default Enlace)" if supervisor.default else ""
    capacity = f"; its Server {_capacity(supervisor.server)}" if supervisor.server else ""
    return f"{supervisor.name}{default}: {supervisor.status}{capacity}"
