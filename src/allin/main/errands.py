"""Encargos and Lotes (#117, ADR-0011): a Suscripción to the answers of the Prompts a Session
hands out to others.

- **An Encargo** (``@errand``) gives each of its workers its task at once. Its Session wakes
  once, with every result, when all end: well, failing, or with no answer by their deadline.
  With a quorum ``k`` it wakes once ``k`` ended well, or once that can no longer be, and what
  is still out is cancelled: a race is ``k=1``.
- **A Lote** (``@batch``) has tasks and workers, and gives the next task to a worker of its
  list with no Turn nor queued Prompt whose Server has a free slot, so free Supervisors take
  turns at it. Its results also say the Server and the model that answered each.
- **Retries.** A task whose worker fails goes out again, up to ``retries`` times, without
  waking its Session; a Lote's may go to another worker.
- **Deadline.** Each task's runs while it is out and its worker's Server is ready (waiting for
  a slot counts), and only with its Profile active; past it, it is cancelled as no answer.
- **The tree.** No Encargo waits for its own Session nor for one waiting for it, so each ends.
- **Cancelling** one (a Rewind of its Session to before it opened it, ``/unsubscribe``, its
  quorum, or a deadline for the part past it) cancels what its workers have queued or running
  for it, and what that opened in turn.

This module holds what an Encargo is and how a result changes it; ``Subscriptions`` runs it.
"""

import json
from typing import Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from allin.core.errors import ErrandInvalid
from allin.core.ids import NO_PROMPT_ID, PromptId, SessionId, SubscriptionId, parse_session_id
from allin.core.origin import Origin
from allin.core.settings import ErrandSettings

ERRAND_USAGE = (
    "/errand <sid> [--k=<n>] [--deadline=<seconds>] [--retries=<n>]"
    ' --to=<sid>,<sid>… <task> | {"<sid>": "<task>", …}'
)
BATCH_USAGE = (
    '/batch <sid> [--k=<n>] [--deadline=<seconds>] [--retries=<n>] --to=<sid>,<sid>… ["<task>", …]'
)

type PartStatus = Literal["pending", "out", "done", "failed", "late", "lost", "cancelled"]
_ENDED: frozenset[PartStatus] = frozenset({"done", "failed", "late", "lost", "cancelled"})
type OutcomeKind = Literal["answered", "failed", "lost"]
_ENDED_AS: dict[OutcomeKind, PartStatus] = {"answered": "done", "failed": "failed", "lost": "lost"}


class Part(BaseModel, frozen=True):
    """One task of an Encargo, and how it went."""

    task: str
    worker: SessionId | None = None  # a Lote's task has none until it goes out
    status: PartStatus = "pending"
    prompt_id: PromptId = NO_PROMPT_ID  # its Prompt, while out
    tries: int = 0  # the Prompts it went out in
    spent: float = 0.0  # seconds of its deadline gone
    result: str = ""  # the answer, or why there is none
    origin: Origin | None = None  # the answer's chain, its worker last
    served_by: str = ""  # a Lote's: the Server and the model that answered

    @property
    def ended(self) -> bool:
        return self.status in _ENDED


class OnErrand(BaseModel, frozen=True):
    """What an Encargo's Suscripción waits for: its parts' results."""

    kind: Literal["errand"] = "errand"
    batch: bool = False
    workers: tuple[SessionId, ...] = ()  # a Lote's, who may take its tasks
    k: int | None = None  # its quorum; ``None``: every part
    deadline: float | None = None  # each part's seconds; ``None``: none
    retries: int = 0
    parts: tuple[Part, ...]

    @property
    def answered(self) -> int:
        return sum(part.status == "done" for part in self.parts)

    @property
    def settled(self) -> bool:
        """Whether it wakes its Session now: every part ended, or its quorum is met or out
        of reach."""
        if self.k is not None:
            still = sum(not part.ended for part in self.parts)
            if self.answered >= self.k or self.answered + still < self.k:
                return True
        return all(part.ended for part in self.parts)

    @property
    def progress(self) -> str:
        return f"{self.answered} of {len(self.parts)} answered"

    @property
    def waits_for(self) -> set[SessionId]:
        """The workers it still waits for: a Lote with tasks to go, every one of its list."""
        out = {p.worker for p in self.parts if p.worker is not None and not p.ended}
        return out | set(self.workers) if not self.settled else out

    @property
    def assigned(self) -> tuple[SessionId, ...]:
        """Who it hands its tasks to: a Lote's list, or each part's worker."""
        return self.workers or tuple(p.worker for p in self.parts if p.worker is not None)

    def with_workers(self, known: dict[SessionId, SessionId]) -> "OnErrand":
        """The same, each worker named by ``known`` instead (by its id now)."""
        parts = tuple(
            p.model_copy(update={"worker": known.get(p.worker, p.worker)}) if p.worker else p
            for p in self.parts
        )
        workers = tuple(known.get(w, w) for w in self.workers)
        return self.model_copy(update={"parts": parts, "workers": workers})

    @property
    def event(self) -> str:
        if self.batch:
            return f"batch:{len(self.parts)} tasks"
        return "errand:" + ",".join(str(part.worker) for part in self.parts)

    def with_part(self, index: int, part: Part) -> "OnErrand":
        parts = (*self.parts[:index], part, *self.parts[index + 1 :])
        return self.model_copy(update={"parts": parts})

    def renamed(self, old: SessionId, new: SessionId) -> "OnErrand":
        def key(session_id: SessionId | None) -> SessionId | None:
            return new if session_id == old else session_id

        parts = tuple(p.model_copy(update={"worker": key(p.worker)}) for p in self.parts)
        workers = tuple(new if w == old else w for w in self.workers)
        return self.model_copy(update={"parts": parts, "workers": workers})


class Outcome(BaseModel, frozen=True):
    """How a Prompt someone waits for ended."""

    session_id: SessionId  # the one that answered, or did not
    kind: OutcomeKind  # ``lost``: its Turn died with Main
    text: str  # the answer, or why there is none
    origin: Origin  # its Prompt's chain, its Session last

    @property
    def told(self) -> str:
        """As a reply tells it."""
        if self.kind == "answered":
            return f"Session {self.session_id} answered:\n{self.text}"
        return f"Session {self.session_id} did not answer: {self.text}"


def part_id(errand: SubscriptionId, index: int) -> SubscriptionId:
    """What a part's Prompt owes its answer to."""
    return SubscriptionId(f"{errand}.{index}")


def parse_part_id(owed: SubscriptionId) -> tuple[SubscriptionId, int] | None:
    errand, dot, index = owed.partition(".")
    return (SubscriptionId(errand), int(index)) if dot and index.isdigit() else None


def with_outcome(errand: OnErrand, index: int, outcome: Outcome, served_by: str) -> OnErrand:
    """``errand`` once part ``index``'s Prompt ended as ``outcome``: done, out again if it
    failed with retries left, or ended without an answer. A part not out any more (cancelled,
    late) keeps as it is: what comes for it is dropped."""
    if index >= len(errand.parts) or errand.parts[index].status != "out":
        return errand
    part = errand.parts[index]
    if outcome.kind == "failed" and part.tries <= errand.retries:
        worker = None if errand.batch else part.worker  # a Lote's may go to another
        again = part.model_copy(
            update={"status": "pending", "worker": worker, "prompt_id": NO_PROMPT_ID}
        )
        return errand.with_part(index, again)
    status = _ENDED_AS[outcome.kind]
    update = {
        "status": status,
        "result": outcome.text,
        "origin": outcome.origin,
        "prompt_id": NO_PROMPT_ID,
        "served_by": served_by if errand.batch else "",
    }
    return errand.with_part(index, part.model_copy(update=update))


def report(errand: OnErrand) -> str:
    """Its results, as the Prompt that wakes its Session carries them."""
    lines = [errand.progress]
    for number, part in enumerate(errand.parts, start=1):
        who = f"Session {part.worker}" if part.worker is not None else "no worker"
        head = f"- task {number} ({who})" if errand.batch else f"- {who}"
        tries = f", after {part.tries} tries" if part.tries > 1 else ""
        served = f", on {part.served_by}" if part.served_by else ""
        match part.status:
            case "done":
                lines.append(f"{head} answered{served}{tries}:\n{part.result}")
            case "failed" | "lost":
                lines.append(f"{head} did not answer{tries}: {part.result}")
            case "late":
                lines.append(f"{head}: no answer within its deadline{tries}")
            case "cancelled":
                met = errand.k is not None and errand.answered >= errand.k
                why = "its quorum was met" if met else "its quorum could no longer be met"
                lines.append(f"{head}: cancelled, {why}")
            case "pending" | "out":
                lines.append(f"{head}: still out")
    return "\n".join(lines)


def task_text(header: str, errand: SubscriptionId, task: str) -> str:
    """A part's Prompt: ``[from Session ab12 · Encargo e1]`` and its task, as data."""
    return f"{header.removesuffix(']')} · Encargo {errand}]\n{task}"


_TASKS = TypeAdapter(list[str])
_ASSIGNED = TypeAdapter(dict[str, str])


def parse_errand(text: str, batch: bool, settings: ErrandSettings) -> OnErrand:
    """An Encargo (or a Lote) as ``/errand`` (``/batch``) takes it after its ``<sid>``; raises
    ``ErrandInvalid``."""
    usage = BATCH_USAGE if batch else ERRAND_USAGE
    options: dict[str, str] = {}
    rest = text.strip()
    while rest.startswith("--"):
        word, _, rest = rest.partition(" ")
        key, equals, value = word[2:].partition("=")
        if not equals or key not in ("k", "deadline", "retries", "to") or key in options:
            raise ErrandInvalid(option=word, usage=usage)
        options[key] = value
        rest = rest.strip()
    workers = tuple(_worker(name, usage) for name in options.get("to", "").split(",") if name)
    if len(set(workers)) != len(workers):
        raise ErrandInvalid(reason="a worker is named twice", usage=usage)
    if batch:
        parts = tuple(Part(task=task) for task in _tasks(rest, usage))
        if not workers:
            raise ErrandInvalid(reason="a Lote needs --to=", usage=usage)
    elif rest.startswith("{"):
        if workers:
            raise ErrandInvalid(reason="--to= or a task for each, not both", usage=usage)
        assigned = _json(_ASSIGNED, rest, usage)
        parts = tuple(Part(task=t, worker=_worker(w, usage)) for w, t in assigned.items())
        workers = ()
    else:
        if not workers or not rest:
            raise ErrandInvalid(reason="it needs workers and a task", usage=usage)
        parts = tuple(Part(task=rest, worker=worker) for worker in workers)
        workers = ()
    if not parts or any(not part.task.strip() for part in parts):
        raise ErrandInvalid(reason="a task is empty", usage=usage)
    k = _number(options.get("k"), int, usage)
    if k is not None and not 1 <= k <= len(parts):
        raise ErrandInvalid(reason=f"--k= goes from 1 to {len(parts)}", usage=usage)
    deadline = _number(options.get("deadline"), float, usage)
    if deadline is None and settings.deadline > 0:
        deadline = settings.deadline
    if deadline is not None and not 0 < deadline < float("inf"):
        raise ErrandInvalid(reason="--deadline= is a number of seconds above 0", usage=usage)
    retries = _number(options.get("retries"), int, usage)
    retries = settings.retries if retries is None else retries
    if not 0 <= retries <= 10:
        raise ErrandInvalid(reason="--retries= goes from 0 to 10", usage=usage)
    return OnErrand(
        batch=batch, workers=workers, k=k, deadline=deadline, retries=retries, parts=parts
    )


def _worker(name: str, usage: str) -> SessionId:
    session_id = parse_session_id(name)
    if session_id is None:
        raise ErrandInvalid(worker=name, usage=usage)
    return session_id


def _tasks(rest: str, usage: str) -> list[str]:
    if rest.startswith("["):
        return _json(_TASKS, rest, usage)
    return [line for line in rest.splitlines() if line.strip()]  # one per line, as it may come


def _json[T](adapter: TypeAdapter[T], text: str, usage: str) -> T:
    try:
        return adapter.validate_python(json.loads(text))
    except (ValueError, ValidationError) as error:
        raise ErrandInvalid(reason=f"not JSON as it should be: {error}", usage=usage) from error


def _number[N: (int, float)](value: str | None, kind: type[N], usage: str) -> N | None:
    if value is None:
        return None
    try:
        return kind(value)
    except ValueError as error:
        raise ErrandInvalid(option=value, usage=usage) from error
