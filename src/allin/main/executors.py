"""Executors: threads that run Turns the dispatcher hands them, one Turn each at a time.

They never touch Main's state: what a Turn streams and how it ends goes back to the dispatcher
as events (ADR-0001), and no executor ever waits for another Turn (ADR-0004).
"""

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from allin.core.errors import AllInError, TurnCrashed
from allin.main.events import MainEvent, StepEnded, TurnEnded, TurnOutput, TurnRaw
from allin.main.turn import Cancellation, Llm, TurnRequest, run_turn

_SHUTTING_DOWN = "main is shutting down"


@dataclass(frozen=True)
class TurnJob:
    session_id: str
    request: TurnRequest
    # The dispatcher keeps it to stop the Turn, e.g. when its Window closes.
    cancellation: Cancellation = field(default_factory=Cancellation)


def execute(llm: Llm, job: TurnJob, emit: Callable[[MainEvent], None]) -> None:
    """Run one Turn, reporting its output and its end through ``emit``."""
    try:
        steps = run_turn(
            llm,
            job.request,
            lambda kind, text: emit(TurnOutput(job.session_id, kind, text)),
            job.cancellation,
            lambda event: emit(TurnRaw(job.session_id, event)),
            lambda step: emit(StepEnded(job.session_id, step)),
        )
    except AllInError as error:
        emit(TurnEnded(job.session_id, error=error))
    except Exception as error:  # noqa: BLE001 -- a bug must not leave the Turn active forever
        emit(TurnEnded(job.session_id, error=TurnCrashed(reason=repr(error))))
    else:
        emit(TurnEnded(job.session_id, steps=steps))


class TurnExecutors:
    """``slots`` executor threads; Turns beyond that wait in Main's queue, not here."""

    def __init__(self, llm: Llm, slots: int, inbox: queue.Queue[MainEvent]) -> None:
        self._llm = llm
        self._inbox = inbox
        self._jobs: queue.Queue[TurnJob | None] = queue.Queue()
        self._lock = threading.Lock()
        self._stopping = False
        self._running: list[TurnJob] = []
        self._threads = [
            threading.Thread(target=self._work, name=f"executor-{n}", daemon=True)
            for n in range(slots)
        ]

    def start(self) -> None:
        for thread in self._threads:
            thread.start()

    def submit(self, job: TurnJob) -> None:
        self._jobs.put(job)

    def shutdown(self, timeout: float) -> None:
        """Cancel running Turns (their streams are closed) and wait up to ``timeout`` for them."""
        with self._lock:
            self._stopping = True
            running = list(self._running)
        for job in running:
            job.cancellation.cancel(_SHUTTING_DOWN)
        for _ in self._threads:
            self._jobs.put(None)
        for thread in self._threads:
            thread.join(timeout)

    def _work(self) -> None:
        while (job := self._jobs.get()) is not None:
            with self._lock:
                self._running.append(job)
                stopping = self._stopping
            if stopping:
                job.cancellation.cancel(_SHUTTING_DOWN)
            try:
                execute(self._llm, job, self._inbox.put)
            finally:
                with self._lock:
                    self._running.remove(job)
