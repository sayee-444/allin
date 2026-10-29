"""Watching llama-server's ``/health`` while Main's queue is paused because it was unreachable.

The watch runs on its own thread and only tells the dispatcher, through its inbox, that the
server is back (ADR-0001); it ends there, or when Main stops.
"""

import queue
import threading
import urllib.request

from allin.main.events import LlmAvailable, MainEvent
from allin.main.slots import server_root

_INTERVAL = 1.0
_TIMEOUT = 2.0


def server_healthy(base_url: str, timeout: float = _TIMEOUT) -> bool:
    """Whether ``/health`` answers OK; llama-server says 503 while it loads the model."""
    try:
        with urllib.request.urlopen(f"{server_root(base_url)}/health", timeout=timeout):
            return True
    except OSError:  # no server, or an HTTP error status
        return False


class HealthWatch:
    """One watch at a time; a new one can start as soon as the last one has reported."""

    def __init__(
        self, base_url: str, inbox: queue.Queue[MainEvent], interval: float = _INTERVAL
    ) -> None:
        self._base_url = base_url
        self._inbox = inbox
        self._interval = interval
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Watch until ``/health`` is OK, then put ``LlmAvailable`` in the inbox, once."""
        with self._lock:
            if self._stopping.is_set() or self._thread is not None:
                return
            self._thread = threading.Thread(target=self._watch, name="health", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stopping.set()
            thread = self._thread
        if thread is not None:
            thread.join()

    def _watch(self) -> None:
        while not self._stopping.is_set():
            if server_healthy(self._base_url):
                with self._lock:  # before the event: the queue may pause again right after it
                    self._thread = None
                self._inbox.put(LlmAvailable())
                return
            self._stopping.wait(self._interval)
