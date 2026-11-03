"""How a Proceso adherido serves its methods, inside its own process (ADR-0012).

The warden's runner hands it the fproc's instance and its two pipes, and it answers each call:

- **By id.** Each call runs in a thread of its own, and its answers carry its id: Main sends no
  more at once than the class's ``@concurrent`` allows, one without it, so a simple fproc never
  meets a second thread. What a method asks of Main (``allin.channel.command``) and what it says
  of its progress (``Fproc.progress``) go with the id of the call it serves.
- **The revision.** It goes up with each ``@writes`` method and each ``Fproc.changed()``, and
  each answer says which one the call saw. A checked ``@writes`` call whose Session has not seen
  the current one is not run: the check and the change happen with every other ``@writes`` held off, while a
  ``Fproc.changed()`` from another thread may still come between them: then the next check
  catches it.
- **Cancelling.** Main says which call it gave up on; ``Fproc.cancelled()`` tells the method,
  and a Command session it asks for from then on does not run.

It runs inside the Proceso adherido, so it uses the standard library alone.
"""

import json
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Queue
from types import FunctionType
from typing import IO, cast

from allin.decorators import COMMUTATIVE_MARK, WRITES_MARK

type _Json = bool | int | float | str | list["_Json"] | dict[str, "_Json"] | None

_CANCELLED: _Json = {"error": "attached_call_cancelled: its call was cancelled"}
_PROGRESS_MAX = 2000  # as Main reads it (``messages.PROGRESS_MAX``)


@dataclass
class _Call:
    id: int
    prompt_id: str  # the Prompt whose Turn made it; none for a human's
    cancelled: threading.Event = field(default_factory=threading.Event)
    results: Queue[_Json] = field(default_factory=Queue[_Json])  # of its Command sessions


class _Runtime:
    """The calls under way and the revision, shared by every thread of the process."""

    def __init__(self, fproc_class: type, instance: object, replies: IO[bytes]) -> None:
        self._class = fproc_class
        self._instance = instance
        self._replies = replies
        self._writing = threading.Lock()  # one line at a time on the pipe to Main
        self._calls: dict[int, _Call] = {}
        self._calls_lock = threading.Lock()
        self._revision = 0
        self._revision_lock = threading.Lock()
        self._writes = threading.Lock()  # one ``@writes`` at a time, with its check
        self._local = threading.local()  # ``call``: the one the thread serves

    def serve(self, calls: IO[bytes]) -> None:
        """Read Main's lines until they end: calls, the results of their Command sessions, and
        which ones were cancelled."""
        for line in calls:
            try:
                message = cast(dict[str, _Json], json.loads(line))
            except ValueError:
                continue  # the warden passes on whole, typed lines only
            kind, call_id = message.get("type"), message.get("id")
            if not isinstance(call_id, int):
                continue
            if kind == "method":
                self._start(call_id, message)
            elif (call := self._call(call_id)) is not None:
                if kind == "command_result":
                    call.results.put(message.get("result"))
                elif kind == "cancel_call":
                    call.cancelled.set()

    def command(self, text: str) -> _Json:
        """For ``allin.channel.command``: ask Main for a Command session for the call this
        thread serves. Raises ``RuntimeError`` in a thread that serves none."""
        call = self._current()
        if call.cancelled.is_set():
            return _CANCELLED
        self._send({"type": "command", "id": call.id, "text": text})
        return call.results.get()  # Main answers each, a cancelled call's too

    def progress(self, text: str) -> None:
        call = getattr(self._local, "call", None)
        if isinstance(call, _Call):
            self._send({"type": "progress", "id": call.id, "text": text[:_PROGRESS_MAX]})

    def cancelled(self) -> bool:
        call = getattr(self._local, "call", None)
        return isinstance(call, _Call) and call.cancelled.is_set()

    def announce(self, text: str) -> None:
        """Tell Main something happened, for the Sessions subscribed to this process. Raises
        ``RuntimeError`` in a thread that serves no call."""
        call = self._current()
        self._send({"type": "announce", "id": call.id, "text": text[:_PROGRESS_MAX]})

    def prompt_id(self) -> str:
        call = getattr(self._local, "call", None)
        return call.prompt_id if isinstance(call, _Call) else ""

    def changed(self) -> None:
        with self._revision_lock:
            self._revision += 1

    def _start(self, call_id: int, message: dict[str, _Json]) -> None:
        prompt_id = message.get("prompt_id")
        call = _Call(call_id, prompt_id if isinstance(prompt_id, str) else "")
        with self._calls_lock:
            self._calls[call_id] = call
        threading.Thread(
            target=self._run, args=(call, message), name=f"call-{call_id}", daemon=True
        ).start()

    def _run(self, call: _Call, message: dict[str, _Json]) -> None:
        self._local.call = call
        try:
            try:
                answer = self._answer(call, message)
            except BaseException as error:  # noqa: BLE001 - whatever it was, Main hears of it
                answer = _raised(call, error)
            self._send(answer)
        finally:
            with self._calls_lock:
                del self._calls[call.id]

    def _answer(self, call: _Call, message: dict[str, _Json]) -> dict[str, _Json]:
        method, arguments, seen = (
            message.get("method"),
            message.get("arguments"),
            message.get("seen"),
        )
        function = vars(self._class).get(method) if isinstance(method, str) else None
        if (
            not isinstance(method, str)
            or method.startswith("_")
            or not isinstance(function, FunctionType)
            or not isinstance(arguments, dict)
        ):
            return {"type": "raised", "id": call.id, "error": f"NoSuchMethod: {method!r}"}
        if not getattr(function, WRITES_MARK, False):
            revision = self._current_revision()
            return self._ran(call, lambda: getattr(self._instance, method)(**arguments), revision)
        checked = isinstance(seen, int) and not getattr(function, COMMUTATIVE_MARK, False)
        with self._writes:
            before = self._current_revision()
            if checked and seen != before:
                return {"type": "stale", "id": call.id, "revision": before}
            answer = self._ran(call, lambda: getattr(self._instance, method)(**arguments), None)
            self.changed()
            if seen == before:  # it saw everything until its own change
                answer["revision"] = before + 1
            return answer

    def _ran(
        self, call: _Call, run: Callable[[], object], revision: int | None
    ) -> dict[str, _Json]:
        try:
            value = cast(_Json, run())
            answer: dict[str, _Json] = {"type": "returned", "id": call.id, "value": value}
        except BaseException as error:  # noqa: BLE001 - the method's error is its output, as data
            return _raised(call, error)
        try:
            json.dumps(value)
        except (TypeError, ValueError) as error:
            return {"type": "raised", "id": call.id, "error": f"TypeError: {error}"}
        answer["revision"] = revision
        return answer

    def _current_revision(self) -> int:
        with self._revision_lock:
            return self._revision

    def _call(self, call_id: int) -> _Call | None:
        with self._calls_lock:
            return self._calls.get(call_id)

    def _current(self) -> _Call:
        call = getattr(self._local, "call", None)
        if not isinstance(call, _Call):
            msg = "no call under way in this thread: a Command session runs only from a method"
            raise RuntimeError(msg)  # noqa: TRY004 - what the fproc's author reads
        return call

    def _send(self, message: dict[str, _Json]) -> None:
        line = json.dumps(message).encode() + b"\n"
        with self._writing:
            sys.stdout.flush()
            sys.stderr.flush()
            self._replies.write(line)
            self._replies.flush()


def _raised(call: _Call, error: BaseException) -> dict[str, _Json]:
    return {"type": "raised", "id": call.id, "error": f"{type(error).__name__}: {error}"}


_runtime: _Runtime | None = None  # the process's one, once its class was made


def serve(fproc_class: type, instance: object, calls_out: int, replies: IO[bytes]) -> None:
    """Set by the warden's runner once the class was made: answer Main's calls until they
    end."""
    global _runtime  # noqa: PLW0603 - the process's one Proceso adherido
    _runtime = _Runtime(fproc_class, instance, replies)
    with os.fdopen(calls_out, "rb") as calls:
        _runtime.serve(calls)


def runtime() -> _Runtime | None:
    """The Proceso adherido this process serves, if it is one and its class was made."""
    return _runtime
