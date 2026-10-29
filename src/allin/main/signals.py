"""SIGINT, SIGTERM and SIGHUP shut Main down the same way ``/exit`` does.

A Python signal handler runs on the main thread between any two bytecodes, so it must not take
a lock the interrupted code may hold (e.g. a queue's). The handler does nothing; the signal
number reaches a pipe through ``signal.set_wakeup_fd`` and a thread reads it from there.
"""

import os
import signal
import threading
from collections.abc import Callable
from types import FrameType

SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def _ignore(signum: int, frame: FrameType | None) -> None:
    pass


class ShutdownSignals:
    def __init__(self, on_signal: Callable[[], None]) -> None:
        self._on_signal = on_signal
        self._previous: dict[signal.Signals, object] = {}
        self._previous_fd = -1
        self._fds: tuple[int, int] | None = None
        self._thread: threading.Thread | None = None

    def install(self) -> None:
        """Call from the main thread."""
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        self._fds = (read_fd, write_fd)
        self._previous_fd = signal.set_wakeup_fd(write_fd, warn_on_full_buffer=False)
        for signum in SHUTDOWN_SIGNALS:
            self._previous[signum] = signal.signal(signum, _ignore)
        self._thread = threading.Thread(target=self._read, name="signals", daemon=True)
        self._thread.start()

    def uninstall(self) -> None:
        """Give the signals back their previous handlers; call from the main thread."""
        if self._fds is None:
            return
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)  # pyright: ignore[reportArgumentType]
        signal.set_wakeup_fd(self._previous_fd)
        read_fd, write_fd = self._fds
        os.close(write_fd)  # the reader sees EOF
        if self._thread is not None:
            self._thread.join()
        os.close(read_fd)
        self._fds = None

    def _read(self) -> None:
        assert self._fds is not None
        numbers = {int(signum) for signum in SHUTDOWN_SIGNALS}
        while data := os.read(self._fds[0], 64):
            if any(byte in numbers for byte in data):
                self._on_signal()
