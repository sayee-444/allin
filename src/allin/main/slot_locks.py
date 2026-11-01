"""``SlotWaiter``: Main waits on its own thread for a slot lock another Main holds (ADR-0001,
ADR-0008); the locks themselves are in ``allin.core.slot_locks``."""

import queue
import threading

from allin.core.ids import ServerKey
from allin.core.slot_locks import SlotLocks
from allin.main.events import MainEvent, SlotFreed

_INTERVAL = 0.1


class SlotWaiter:
    """Waits, on its own thread, for a slot lock another Main holds to be free; then it puts
    ``SlotFreed`` in the inbox, once (ADR-0001). One wait at a time; a new one can start as
    soon as the last one has reported."""

    def __init__(
        self,
        locks: SlotLocks,
        inbox: queue.Queue[MainEvent],
        server: ServerKey,
        interval: float = _INTERVAL,
    ) -> None:
        self._locks = locks
        self._server = server
        self._inbox = inbox
        self._interval = interval
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, slots: int) -> None:
        with self._lock:
            if self._stopping.is_set() or self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._wait, args=(slots,), name="slot-waiter", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stopping.set()
            thread = self._thread
        if thread is not None:
            thread.join()

    def _wait(self, slots: int) -> None:
        while not self._stopping.is_set():
            lock = self._locks.take(slots)
            if lock is not None:  # left for the dispatcher to take; another Main may beat it
                lock.release()
                with self._lock:  # before the event: Main may need to wait again right after
                    self._thread = None
                self._inbox.put(SlotFreed(self._server))
                return
            self._stopping.wait(self._interval)
