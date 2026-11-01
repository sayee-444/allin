"""Main's side of every Server its Sessions use (ADR-0022): a watch, slot locks, a Wait list,
a slot waiter and a measurer each, started the first time a Session needs that Server, and the
waking of an asleep Supervisor when work comes for it.
"""

import contextlib
import os
import queue
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from allin.core.errors import SupervisorNotRunning
from allin.core.ids import ServerKey, SessionId, SupervisorName, parse_supervisor_name
from allin.core.slot_locks import SlotLock, SlotLocks
from allin.core.wait_list import WaitList
from allin.main.conversation import Item
from allin.main.events import MainEvent
from allin.main.links import Link
from allin.main.measure import Measurer
from allin.main.server_watch import ServerWatch
from allin.main.slot_locks import SlotWaiter
from allin.supervisor.lock import supervisor_active
from allin.supervisor.process import put_to_sleep
from allin.supervisor.state import read_state, state_path

# A Supervisor just woken takes a moment to hold its lock: not woken again meanwhile.
_WAKE_GRACE = 10.0
_LOCK_PAUSE = 0.05
_SLEEP_TRIES = 40  # two seconds


@dataclass
class _Watched:
    watch: ServerWatch
    locks: SlotLocks
    waits: WaitList
    waiter: SlotWaiter
    measurer: Measurer


class ServerLinks:
    def __init__(self, data_dir: Path, inbox: queue.Queue[MainEvent]) -> None:
        self._data_dir = data_dir
        self._servers = data_dir / "servers"
        self._inbox = inbox
        self._watched: dict[ServerKey, _Watched] = {}
        self._woken: dict[SupervisorName, float] = {}
        self._started: list[subprocess.Popen[bytes]] = []  # reaped once they end

    def watch(self, link: Link) -> None:
        """Start watching the link's Server, if Main does not already."""
        if link.key in self._watched:
            return
        supervisor = (self._servers, link.supervisor) if link.supervisor else None
        watch = ServerWatch(link.key, link.base_url, self._inbox, supervisor)
        locks = SlotLocks(self._servers, link.key)
        waits = WaitList(self._servers, link.key)
        waiter = SlotWaiter(locks, waits, self._inbox, link.key)
        measurer = Measurer(link.key, link.base_url, self._inbox)
        self._watched[link.key] = _Watched(watch, locks, waits, waiter, measurer)
        watch.start()

    def wait_list(self, server: ServerKey) -> WaitList:
        return self._watched[server].waits

    def measure(
        self, server: ServerKey, session_id: SessionId, ticket: int, items: list[Item]
    ) -> None:
        """Measure a Step's input on the Server; ``InputMeasured`` reaches the inbox."""
        self._watched[server].measurer.measure(session_id, ticket, items)

    def take_slot(self, server: ServerKey, slots: int) -> SlotLock | None:
        """``None`` also while its Supervisor drains: it takes every slot (ADR-0008)."""
        if self._draining(server):
            return None
        return self._watched[server].locks.take(slots)

    def _draining(self, server: ServerKey) -> bool:
        name = parse_supervisor_name(server)
        state = read_state(self._servers, name) if name is not None else None
        return state is not None and state.status == "draining"

    def restart(self, name: SupervisorName, now: bool) -> None:
        """Ask a running Supervisor to restart its Server with its pending Settings."""
        state = read_state(self._servers, name)
        if state is None or state.pid is None or not supervisor_active(self._servers, name):
            raise SupervisorNotRunning(supervisor=name)
        os.kill(state.pid, signal.SIGUSR1 if now else signal.SIGHUP)

    def wait_for_slot(self, server: ServerKey, slots: int, version: int) -> None:
        """Wait for a slot lock to be freed or the Wait list to move past ``version``. Not while
        its Supervisor drains, racing it for each slot: Main looks again once the Server is
        back."""
        if not self._draining(server):
            self._watched[server].waiter.start(slots, version)

    def recheck(self, server: ServerKey) -> None:
        if (watched := self._watched.get(server)) is not None:
            watched.watch.recheck()

    def sleep(self, name: SupervisorName) -> None:
        """Drain a running Supervisor and put it to sleep; mark one that does not run as put to
        sleep by a human, so that work never wakes it (ADR-0022)."""
        for _ in range(_SLEEP_TRIES):
            if put_to_sleep(self._servers, name):
                return
            state = read_state(self._servers, name)
            if state is not None and state.pid is not None:
                with contextlib.suppress(ProcessLookupError):  # ended: marked on the next try
                    os.kill(state.pid, signal.SIGUSR2)
                    return
            time.sleep(_LOCK_PAUSE)  # it starts and has not written its pid yet
        raise SupervisorNotRunning(supervisor=name)

    def wake(self, name: SupervisorName, human: bool = False) -> bool:
        """Start the Supervisor's process unless it runs, was just woken, or a human put it to
        sleep and this is not a ``human``'s wake; whether it was started."""
        if supervisor_active(self._servers, name):
            return False
        self._started = [process for process in self._started if process.poll() is None]
        state = read_state(self._servers, name)
        woken = self._woken.get(name)
        if (
            not human
            and woken is not None
            and time.monotonic() - woken < _WAKE_GRACE
            and not _ended_since(self._servers, name, time.time() - (time.monotonic() - woken))
        ):
            return False
        if state is not None and state.slept_by_human and not human:  # only /server wake
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
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # it outlives this Main, whatever ends it (ADR-0008)
        )
        self._started.append(process)
        return True

    def stop(self) -> None:
        for watched in self._watched.values():
            watched.watch.stop()
            watched.waiter.stop()
            watched.measurer.stop()


def _ended_since(servers: Path, name: SupervisorName, since: float) -> bool:
    """Whether the Supervisor woken at ``since`` (wall clock) already ended: its ``.json``
    was written after, and it holds no lock."""
    try:
        return state_path(servers, name).stat().st_mtime > since
    except FileNotFoundError:
        return False
