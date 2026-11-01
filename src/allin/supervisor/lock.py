"""One process per Supervisor: an ``flock`` on ``servers/<name>.lock`` (ADR-0008).

The kernel drops it when the Supervisor ends, however it ends. Main never takes it: it only
looks whether someone holds it, which is how it knows a Supervisor is active.
"""

import fcntl
import os
import time
from pathlib import Path

from allin.core.errors import SupervisorAlreadyRunning

# Main's look holds the lock shared for an instant; a Supervisor starting then tries again.
_TRIES = 10
_PAUSE = 0.05


def lock_path(servers: Path, name: str) -> Path:
    return servers / f"{name}.lock"


class SupervisorLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        """Hold the lock until the process ends, or fail if another Supervisor holds it."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        for attempt in range(_TRIES):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if attempt == _TRIES - 1:
                    os.close(fd)
                    raise SupervisorAlreadyRunning(lock=self._path.name) from None
                time.sleep(_PAUSE)
            else:
                self._fd = fd
                return

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)


def supervisor_active(servers: Path, name: str) -> bool:
    """Whether a Supervisor process holds ``name``'s lock now."""
    try:
        fd = os.open(lock_path(servers, name), os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)  # also drops the shared lock, if it took it
    return False
