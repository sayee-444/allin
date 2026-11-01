"""``SlotWaiter``: Main waits on its own thread for a slot lock another Main holds, or for its
Server's Wait list to change (ADR-0001, ADR-0008, ADR-0022); the locks themselves are in
``allin.core.slot_locks``."""

import queue
import threading

from allin.core.ids import ServerKey
from allin.core.slot_locks import SlotLocks
from allin.core.wait_list import WaitList
from allin.main.events import MainEvent, SlotFreed

_INTERVAL = 0.1


class SlotWaiter:
    """Waits, on its own thread, until a slot lock is freed or the Server's Wait list changes
    after the wait started; then it puts ``SlotFreed`` in the inbox, once (ADR-0001). One wait
    at a time; a new one can start as soon as the last one has reported."""

    def __init__(
        self,
        locks: SlotLocks,
        waits: WaitList,
        inbox: queue.Queue[MainEvent],
        server: ServerKey,
        interval: float = _INTERVAL,
    ) -> None:
        self._locks = locks
        self._waits = waits
        self._server = server
        self._inbox = inbox
        self._interval = interval
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, slots: int, version: int) -> None:
        """Wait for a change after what Main saw: the Wait list at ``version``, and the slot
        locks free now."""
        with self._lock:
            if self._stopping.is_set() or self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._wait,
                args=(slots, version, self._locks.free(slots)),
                name="slot-waiter",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stopping.set()
            thread = self._thread
        if thread is not None:
            thread.join()

    def _wait(self, slots: int, version: int, free: int) -> None:
        while not self._stopping.is_set():
            now_free = self._locks.free(slots)
            # A lock freed is left for the dispatcher to take; another Main may beat it.
            if now_free > free or self._waits.version() != version:
                with self._lock:  # before the event: Main may need to wait again right after
                    self._thread = None
                self._inbox.put(SlotFreed(self._server))
                return
            free = min(free, now_free)  # one taken meanwhile: freeing it again counts
            self._stopping.wait(self._interval)
