"""Main's side of every Server its Sessions use (ADR-0022): a watch, slot locks and a slot
waiter each, started the first time a Session needs that Server, and the waking of an asleep
Supervisor when work comes for it.
"""

import queue
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from allin.core.ids import ServerKey, SupervisorName
from allin.main.events import MainEvent
from allin.main.links import Link
from allin.main.server_watch import ServerWatch
from allin.main.slot_locks import SlotLock, SlotLocks, SlotWaiter
from allin.supervisor.lock import supervisor_active
from allin.supervisor.state import read_state

# A Supervisor just woken takes a moment to hold its lock: not woken again meanwhile.
_WAKE_GRACE = 10.0


@dataclass
class _Watched:
    watch: ServerWatch
    locks: SlotLocks
    waiter: SlotWaiter


class ServerLinks:
    def __init__(self, data_dir: Path, inbox: queue.Queue[MainEvent]) -> None:
        self._data_dir = data_dir
        self._servers = data_dir / "servers"
        self._inbox = inbox
        self._watched: dict[ServerKey, _Watched] = {}
        self._woken: dict[SupervisorName, float] = {}

    def watch(self, link: Link) -> None:
        """Start watching the link's Server, if Main does not already."""
        if link.key in self._watched:
            return
        supervisor = (self._servers, link.supervisor) if link.supervisor else None
        watch = ServerWatch(link.key, link.base_url, self._inbox, supervisor)
        locks = SlotLocks(self._servers, link.key)
        self._watched[link.key] = _Watched(watch, locks, SlotWaiter(locks, self._inbox, link.key))
        watch.start()

    def take_slot(self, server: ServerKey, slots: int) -> SlotLock | None:
        return self._watched[server].locks.take(slots)

    def wait_for_slot(self, server: ServerKey, slots: int) -> None:
        self._watched[server].waiter.start(slots)

    def recheck(self, server: ServerKey) -> None:
        if (watched := self._watched.get(server)) is not None:
            watched.watch.recheck()

    def wake(self, name: SupervisorName) -> bool:
        """Start the Supervisor's process unless it runs, was just woken, or a human put it to
        sleep; whether it was started."""
        if supervisor_active(self._servers, name):
            return False
        if time.monotonic() - self._woken.get(name, -_WAKE_GRACE) < _WAKE_GRACE:
            return False
        state = read_state(self._servers, name)
        if state is not None and state.slept_by_human:  # only /server wake undoes that
            return False
        self._woken[name] = time.monotonic()
        command = [
            sys.executable,
            "-m",
            "allin.supervisor",
            name,
            "--data-dir",
            str(self._data_dir),
        ]
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # it outlives this Main, whatever ends it (ADR-0008)
        )
        return True

    def stop(self) -> None:
        for watched in self._watched.values():
            watched.watch.stop()
            watched.waiter.stop()
