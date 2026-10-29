"""One Main per id and data dir at a time (ADR-0007).

An ``flock`` on a file next to MainState: the kernel drops it when Main's process ends, however
it ends, so a Main killed outright never blocks the next one. The descriptor is not inherited
(PEP 446), so Windows that outlive Main never hold it.
"""

import fcntl
import os
from pathlib import Path

from allin.core.errors import MainAlreadyRunning


class MainLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        """Hold the lock until the process ends, or fail if another Main holds it."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(fd)
            raise MainAlreadyRunning(lock=str(self._path)) from error
        self._fd = fd
