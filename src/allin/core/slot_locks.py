"""Slot locks: one ``flock`` per slot of a Server, shared by every Main that uses it and by its
Supervisor, which takes them all to drain it (ADR-0008).

A Step runs only while its Main holds one, so Mains sharing a Server never run more Steps than
it has slots, and a Prompt with no free lock waits in its Main's queue, not in the server's. The
kernel drops the locks of a process that dies, however it dies, and the descriptors are not
inherited (PEP 446), so Windows never hold one.
"""

import fcntl
import os
from pathlib import Path

from allin.core.ids import ServerKey


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
    def __init__(self, directory: Path, server: ServerKey) -> None:
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
