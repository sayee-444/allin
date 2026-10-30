"""Main's watch on its Server: ``/health`` and ``/props``, all the time Main runs (ADR-0004).

On its own thread, it tells the dispatcher through its inbox (ADR-0001) each time what the
Server says of itself changes: down, loading its model (503), or ready with its props.
"""

import json
import queue
import threading
import urllib.error
import urllib.request

from allin.main.events import MainEvent, ServerDown, ServerLoading, ServerReady, ServerState
from allin.main.slots import parse_props, server_root

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


class ServerWatch:
    def __init__(
        self, base_url: str, inbox: queue.Queue[MainEvent], interval: float = _INTERVAL
    ) -> None:
        self._base_url = base_url
        self._inbox = inbox
        self._interval = interval
        self._lock = threading.Lock()
        self._last: ServerState | None = None  # what it last reported
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="server-watch", daemon=True)

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

    def _watch(self) -> None:
        while not self._stopping.is_set():
            state = probe(self._base_url)
            with self._lock:
                changed, self._last = state != self._last, state
            if changed:
                self._inbox.put(state)
            self._stopping.wait(self._interval)
