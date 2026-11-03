"""Consultar (ADR-0011, #118): the state a Session has within reach, read only, of the active
Profile alone: the queue, the Subscriptions and Encargos, the Procesos adheridos, the Servers
its Sessions use and the Supervisors. The tree of Mains comes with F3."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel, JsonValue

from allin.core.errors import InvalidArguments
from allin.core.ids import SessionId

type StatusPart = Literal["queue", "subscriptions", "attached", "servers"]
type ServerStateName = Literal["ready", "down", "loading", "asleep", "failed", "restarting"]
PARTS: tuple[StatusPart, ...] = get_args(StatusPart.__value__)
USAGE = "/status <sid> [queue|subscriptions|attached|servers]"


class SessionWork(BaseModel, frozen=True):
    """A Session with a Turn running or Prompts waiting for one."""

    session: SessionId
    running: bool
    queued: int


class ServerWork(BaseModel, frozen=True):
    """A Server this Main's Sessions use: how it is and how much of it they take."""

    server: str  # as an Enlace describes it, with its key
    state: ServerStateName
    slots: int | None  # its Turns at once, once ready
    busy: int  # this Main's Turns running on it
    model: str | None


class QueueView(BaseModel, frozen=True):
    sessions: tuple[SessionWork, ...]
    servers: tuple[ServerWork, ...]


class SubscriptionView(BaseModel, frozen=True):
    id: str
    session: SessionId
    event: str
    progress: str | None  # an Encargo's, as "1 of 3 answered"


class AttachedView(BaseModel, frozen=True):
    name: str
    methods: tuple[str, ...]


class SupervisorView(BaseModel, frozen=True):
    name: str
    status: str
    default: bool  # the default Enlace's


@dataclass(frozen=True)
class Sources:
    """Where Consultar reads from; none of it changes anything."""

    queue: Callable[[], QueueView]
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
    """What Consultar read, as the tool gets it and as Main's Chat shows it."""

    queue: tuple[SessionWork, ...] | None = None
    subscriptions: tuple[SubscriptionView, ...] | None = None
    attached: tuple[AttachedView, ...] | None = None
    servers: tuple[ServerWork, ...] | None = None
    supervisors: tuple[SupervisorView, ...] | None = None

    def as_json(self) -> JsonValue:
        """The parts it read, as the tool gets them."""
        dumped: dict[str, JsonValue] = self.model_dump(mode="json")
        return {part: items for part, items in dumped.items() if items is not None}

    def lines(self) -> list[str]:
        lines: list[str] = []
        if self.queue is not None:
            lines.append("queue:" if self.queue else "queue: empty")
            lines += [f"  {w.session}: {_work(w)}" for w in self.queue]
        if self.subscriptions is not None:
            lines.append("subscriptions:" if self.subscriptions else "subscriptions: none")
            lines += [
                f"  {s.id} of {s.session}: {s.event}" + (f", {s.progress}" if s.progress else "")
                for s in self.subscriptions
            ]
        if self.attached is not None:
            lines.append("attached:" if self.attached else "attached: none")
            lines += [f"  {a.name}: {', '.join(a.methods) or 'no methods'}" for a in self.attached]
        if self.servers is not None:
            lines.append("servers:" if self.servers else "servers: none watched")
            lines += [f"  {s.server}: {_server(s)}" for s in self.servers]
        if self.supervisors is not None:
            lines.append("supervisors:" if self.supervisors else "supervisors: none")
            lines += [
                f"  {s.name}{' (default Enlace)' if s.default else ''}: {s.status}"
                for s in self.supervisors
            ]
        return lines


def read(
    sources: Sources, part: StatusPart | None, in_active: Callable[[SessionId], bool]
) -> StatusReport:
    """What ``part`` (or every part) shows now, of the active Profile's Sessions alone."""

    def wanted(name: StatusPart) -> bool:
        return part is None or part == name

    queue = sources.queue() if wanted("queue") or wanted("servers") else None
    return StatusReport(
        queue=tuple(w for w in queue.sessions if in_active(w.session))
        if queue is not None and wanted("queue")
        else None,
        subscriptions=tuple(s for s in sources.subscriptions() if in_active(s.session))
        if wanted("subscriptions")
        else None,
        attached=tuple(sources.attached()) if wanted("attached") else None,
        servers=queue.servers if queue is not None and wanted("servers") else None,
        supervisors=tuple(sources.supervisors()) if wanted("servers") else None,
    )


def _work(work: SessionWork) -> str:
    running = "running" if work.running else "waiting"
    return running + (f", {work.queued} queued" if work.queued else "")


def _server(server: ServerWork) -> str:
    if server.slots is None:
        return server.state
    model = f" ({server.model})" if server.model else ""
    return f"{server.state}, {server.busy} of {server.slots} slot(s) busy{model}"
