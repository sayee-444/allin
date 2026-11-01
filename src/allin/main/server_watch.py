"""Main's watch on a Server: ``/health`` and ``/props``, all the time Main runs (ADR-0004).

On its own thread, it tells the dispatcher through its inbox (ADR-0001) each time what the
Server says of itself changes: down, loading its model (503), or ready with its props. For a
Server with a Supervisor it also reads the Supervisor's ``.json`` while the Server does not
answer, to tell an asleep or failed Supervisor from a Server that is starting (ADR-0008).
"""

import json
import queue
import threading
import urllib.error
import urllib.request
from pathlib import Path

from allin.core.ids import ServerKey, SupervisorName, parse_supervisor_name
from allin.main.events import (
    MainEvent,
    ServerAsleep,
    ServerChanged,
    ServerDown,
    ServerFailed,
    ServerLoading,
    ServerReady,
    ServerState,
)
from allin.main.slots import parse_props, server_root
from allin.supervisor.lock import supervisor_active
from allin.supervisor.state import read_state

_INTERVAL = 1.0
_TIMEOUT = 2.0


def probe(base_url: str, timeout: float = _TIMEOUT) -> ServerState:
    """What the Server says of itself now."""
    root = server_root(base_url)
    try:
        with urllib.request.urlopen(f"{root}/health", timeout=timeout):
            pass
        with urllib.request.urlopen(f"{root}/props", timeout=timeout) as reply:
            props = parse_props(json.load(reply))
    except urllib.error.HTTPError as error:
        return ServerLoading() if error.code == 503 else ServerDown()
    except (OSError, ValueError):  # no server, or not JSON
        return ServerDown()
    return ServerDown() if props is None else ServerReady(props)


def supervisor_view(servers: Path, name: SupervisorName) -> ServerState:
    """What a Supervisor whose Server does not answer says of itself."""
    state = read_state(servers, name)
    if supervisor_active(servers, name):
        starting = state is None or state.status == "starting"
        return ServerLoading() if starting else ServerDown()
    if state is not None and state.status == "failed" and state.failure is not None:
        holders = active_supervisors(servers) if state.failure == "vram" else ()
        return ServerFailed(state.failure, state.reason or "", holders)
    return ServerAsleep(slept_by_human=state is not None and state.slept_by_human)


def active_supervisors(servers: Path) -> tuple[SupervisorName, ...]:
    """Every Supervisor running now on this data folder, by name."""
    names = (parse_supervisor_name(path.stem) for path in sorted(servers.glob("*.toml")))
    return tuple(name for name in names if name is not None and supervisor_active(servers, name))


class ServerWatch:
    def __init__(
        self,
        server: ServerKey,
        base_url: str,
        inbox: queue.Queue[MainEvent],
        supervisor: tuple[Path, SupervisorName] | None = None,
        interval: float = _INTERVAL,
    ) -> None:
        """``supervisor``: the servers folder and the Supervisor's name, if it has one."""
        self._server = server
        self._base_url = base_url
        self._inbox = inbox
        self._supervisor = supervisor
        self._interval = interval
        self._lock = threading.Lock()
        self._last: ServerState | None = None  # what it last reported
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._watch, name=f"server-watch-{server}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def recheck(self) -> None:
        """Report what it sees next even if nothing changed, e.g. when a Turn found the
        Server unavailable between two looks and Main paused its queue."""
        with self._lock:
            self._last = None

    def stop(self) -> None:
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join()

    def _look(self) -> ServerState:
        if self._supervisor is None:
            return probe(self._base_url)
        # What answers on its port while it is not running is not its Server: waking it then
        # fails, saying who holds the port.
        if not supervisor_active(*self._supervisor):
            return supervisor_view(*self._supervisor)
        state = probe(self._base_url)
        return supervisor_view(*self._supervisor) if isinstance(state, ServerDown) else state

    def _watch(self) -> None:
        while not self._stopping.is_set():
            state = self._look()
            with self._lock:
                changed, self._last = state != self._last, state
            if changed:
                self._inbox.put(ServerChanged(self._server, state))
            self._stopping.wait(self._interval)
