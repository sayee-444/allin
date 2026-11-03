"""A Server's Wait list: who waits for a slot and what each Step in course reserves of the
pool, shared by every Main that uses the Server (ADR-0022).

It lives next to the slot locks, as ``servers/<key>.waits.json``, read and written under an
``flock`` on ``<key>.waits.lock``. A Main is its id and its process, since two root Mains on
different data folders may share a Server. Each Main publishes its own waiters, replacing the
ones it published before, and reserves and releases its own Steps; the order and the admission are
``allin.main.admission``'s. Entries of a Main whose process ended are dropped on the next
change, since the kernel already dropped its slot locks.
"""

import contextlib
import fcntl
import os
from collections.abc import Callable, Generator, Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from allin.core.files import write_atomically
from allin.core.ids import MainId, ServerKey, SessionId

# What a waiter goes on with: work already started, a new Prompt, one an fproc brought
# (ADR-0026), or Main's own ``describir``.
type WaitClass = Literal["continuation", "prompt", "fproc", "describe"]


class Waiter(BaseModel, frozen=True):
    """A Session whose next Step is ready to start but for a slot or room in the pool."""

    main: MainId
    pid: int  # its Main's process
    session: SessionId
    kind: WaitClass
    since: float  # wall clock: when it started waiting, which orders it within its class
    need: int  # tokens its Step needs: measured input plus Output cap
    firm_at: float  # wall clock: from then on nobody overtakes it (ADR-0004)


class Reservation(BaseModel, frozen=True):
    """What a Step in course holds of the pool: its measured input plus its Output cap."""

    main: MainId
    pid: int
    session: SessionId
    tokens: int


class WaitListView(BaseModel, frozen=True):
    version: int = 0  # moves with every change, so a Main waiting can tell one happened
    waiting: tuple[Waiter, ...] = ()
    reserved: tuple[Reservation, ...] = ()


class WaitList:
    def __init__(self, directory: Path, server: ServerKey) -> None:
        self._directory = directory
        self._path = directory / f"{server}.waits.json"
        self._lock = directory / f"{server}.waits.lock"

    def look(self) -> WaitListView:
        """The list as it is now."""
        with self._locked():
            return self._read()

    def version(self) -> int:
        return self.look().version

    def publish(self, main: MainId, pid: int, waiters: Iterable[Waiter]) -> WaitListView:
        """Make ``waiters`` all that the Main has waiting; a Session that already waited keeps
        when it started. Returns the list after the change."""
        new = list(waiters)

        def change(view: WaitListView) -> WaitListView:
            mine = [w for w in view.waiting if (w.main, w.pid) == (main, pid)]
            before = {(w.session, w.kind): w for w in mine}
            kept = [_kept(waiter, before.get((waiter.session, waiter.kind))) for waiter in new]
            others = [w for w in view.waiting if (w.main, w.pid) != (main, pid)]
            return view.model_copy(update={"waiting": (*others, *kept)})

        return self._change(change)

    def reserve(self, reservation: Reservation) -> None:
        """A Step starts: it reserves its tokens and stops waiting."""

        def change(view: WaitListView) -> WaitListView:
            mine = (reservation.main, reservation.pid, reservation.session)
            waiting = tuple(w for w in view.waiting if (w.main, w.pid, w.session) != mine)
            reserved = (
                *(r for r in view.reserved if (r.main, r.pid, r.session) != mine),
                reservation,
            )
            return view.model_copy(update={"waiting": waiting, "reserved": reserved})

        self._change(change)

    def release(self, main: MainId, pid: int, session: SessionId) -> None:
        """The Session's Step in course ended, or paused: what it reserved is free."""
        mine = (main, pid, session)

        def change(view: WaitListView) -> WaitListView:
            reserved = tuple(r for r in view.reserved if (r.main, r.pid, r.session) != mine)
            return view.model_copy(update={"reserved": reserved})

        self._change(change)

    def _change(self, change: Callable[[WaitListView], WaitListView]) -> WaitListView:
        with self._locked():
            before = self._read()
            after = _without_dead(change(before))
            if after.waiting == before.waiting and after.reserved == before.reserved:
                return before
            after = after.model_copy(update={"version": before.version + 1})
            write_atomically(self._path, after.model_dump_json())
            return after

    def _read(self) -> WaitListView:
        try:
            return WaitListView.model_validate_json(self._path.read_bytes())
        except (OSError, ValidationError):  # none yet, or left broken: nobody waits
            return WaitListView()

    @contextlib.contextmanager
    def _locked(self) -> Generator[None]:
        self._directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _kept(waiter: Waiter, before: Waiter | None) -> Waiter:
    if before is None:
        return waiter
    return waiter.model_copy(update={"since": before.since, "firm_at": before.firm_at})


def _without_dead(view: WaitListView) -> WaitListView:
    alive: dict[int, bool] = {}

    def lives(pid: int) -> bool:
        if pid not in alive:
            alive[pid] = _alive(pid)
        return alive[pid]

    waiting = tuple(w for w in view.waiting if lives(w.pid))
    reserved = tuple(r for r in view.reserved if lives(r.pid))
    return view.model_copy(update={"waiting": waiting, "reserved": reserved})


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # another user's process: it exists
        return True
    return True
