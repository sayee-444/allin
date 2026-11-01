"""A ``Tick`` in Main's inbox once a second, on its own thread (ADR-0001)."""

import queue
import threading

from allin.main.events import MainEvent, Tick

_INTERVAL = 1.0


class Ticker:
    def __init__(self, inbox: queue.Queue[MainEvent], interval: float = _INTERVAL) -> None:
        self._inbox = inbox
        self._interval = interval
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._tick, name="ticker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join()

    def _tick(self) -> None:
        while not self._stopping.wait(self._interval):
            self._inbox.put(Tick())
