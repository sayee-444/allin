"""One end of the FIFO pair that links Main with a Window (ADR-0002).

Each end has a blocking reader thread that decodes lines and hands them to a listener, and a
writer thread fed by a queue so ``send`` never blocks the caller (e.g. the dispatcher) while the
peer has not opened its end yet. Each FIFO is opened in its own thread, so the open order of the
two processes can never deadlock.
"""

import os
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from allin.core.errors import AllInError, ChannelBroken, MalformedMessage
from allin.ipc.messages import encode


@dataclass(frozen=True)
class FifoPair:
    """The two FIFOs of a Session, named from the Window's point of view."""

    to_window: Path
    from_window: Path

    def remove(self) -> None:
        self.to_window.unlink(missing_ok=True)
        self.from_window.unlink(missing_ok=True)


def create_fifo_pair(fifo_dir: Path, session_id: str) -> FifoPair:
    fifo_dir.mkdir(parents=True, exist_ok=True)
    pair = FifoPair(fifo_dir / f"{session_id}.in", fifo_dir / f"{session_id}.out")
    pair.remove()
    os.mkfifo(pair.to_window)
    os.mkfifo(pair.from_window)
    return pair


class ChannelListener[In](Protocol):
    def on_message(self, message: In) -> None: ...
    def on_error(self, error: AllInError) -> None: ...
    def on_eof(self) -> None: ...


class Channel[In]:
    def __init__(
        self,
        read_path: Path,
        write_path: Path,
        decode: Callable[[str], In],
        listener: ChannelListener[In],
    ) -> None:
        self._read_path = read_path
        self._write_path = write_path
        self._decode = decode
        self._listener = listener
        self._outbox: queue.Queue[BaseModel | None] = queue.Queue()
        name = read_path.stem
        self._reader = threading.Thread(target=self._read_loop, name=f"reader-{name}", daemon=True)
        self._writer = threading.Thread(target=self._write_loop, name=f"writer-{name}", daemon=True)

    def start(self) -> None:
        self._reader.start()
        self._writer.start()

    def send(self, message: BaseModel) -> None:
        self._outbox.put(message)

    def close(self, timeout: float = 1.0) -> None:
        """Flush pending messages and close the write end; the peer's reader then sees EOF.

        Waits up to ``timeout`` for the flush, which never completes if the peer never opened
        its read end.
        """
        self._outbox.put(None)
        if self._writer.is_alive():
            self._writer.join(timeout)

    def _read_loop(self) -> None:
        try:
            with open(self._read_path, "rb") as fifo:
                for raw in fifo:
                    try:
                        self._listener.on_message(self._decode(_decode_utf8(raw)))
                    except AllInError as error:
                        self._listener.on_error(error)
        except OSError as error:
            self._listener.on_error(ChannelBroken(path=str(self._read_path), reason=str(error)))
            return
        self._listener.on_eof()

    def _write_loop(self) -> None:
        # Open lazily: an unused write end never blocks waiting for a peer.
        message = self._outbox.get()
        if message is None:
            return
        try:
            with open(self._write_path, "w", encoding="utf-8") as fifo:
                while message is not None:
                    fifo.write(encode(message))
                    fifo.flush()
                    message = self._outbox.get()
        except OSError as error:
            self._listener.on_error(ChannelBroken(path=str(self._write_path), reason=str(error)))
            # The peer is gone: drop whatever is still queued instead of letting it pile up.
            while self._outbox.get() is not None:
                pass


def _decode_utf8(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        raise MalformedMessage(line=line, reason=str(error)) from error
