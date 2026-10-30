"""Slot locks: one ``flock`` per slot of a Server, shared by every Main that uses it (ADR-0008).

A Step runs only while its Main holds one, so Mains sharing a Server never run more Steps than
it has slots, and a Prompt with no free lock waits in its Main's queue, not in the server's. The
kernel drops the locks of a Main that dies, however it dies, and the descriptors are not
inherited (PEP 446), so Windows never hold one.
"""

import fcntl
import os
import queue
import threading
from pathlib import Path

from allin.main.events import MainEvent, SlotFreed

_INTERVAL = 0.1


class SlotLock:
    def __init__(self, fd: int, slot: int) -> None:
        self._fd: int | None = fd
        self.slot = slot

    def release(self) -> None:
        """Closing the descriptor drops the lock; releasing twice does nothing."""
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)


class SlotLocks:
    def __init__(self, directory: Path, server: str) -> None:
        self._directory = directory
        self._server = server

    def take(self, slots: int) -> SlotLock | None:
        """A free lock among the server's first ``slots``; ``None`` if every one is held."""
        self._directory.mkdir(parents=True, exist_ok=True)
        for slot in range(slots):
            path = self._directory / f"{self._server}.slot{slot}.lock"
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            return SlotLock(fd, slot)
        return None


class SlotWaiter:
    """Waits, on its own thread, for a slot lock another Main holds to be free; then it puts
    ``SlotFreed`` in the inbox, once (ADR-0001). One wait at a time; a new one can start as
    soon as the last one has reported."""

    def __init__(
        self, locks: SlotLocks, inbox: queue.Queue[MainEvent], interval: float = _INTERVAL
    ) -> None:
        self._locks = locks
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
                self._inbox.put(SlotFreed())
                return
            self._stopping.wait(self._interval)
