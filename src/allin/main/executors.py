"""Executors: threads that run the Turns the dispatcher hands them, one thread per Turn.

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

SHUTTING_DOWN = "main is shutting down"


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
    """A thread per Turn; the dispatcher only submits one while the server has room for it,
    so how many run at once can change with the server's slots (ADR-0004)."""

    def __init__(self, llm: Llm, inbox: queue.Queue[MainEvent]) -> None:
        self._llm = llm
        self._inbox = inbox
        self._lock = threading.Lock()
        self._stopping = False
        self._running: dict[threading.Thread, TurnJob] = {}

    def submit(self, job: TurnJob) -> None:
        thread = threading.Thread(target=self._work, args=(job,), name=f"turn-{job.session_id}")
        with self._lock:
            if self._stopping:
                job.cancellation.cancel(SHUTTING_DOWN)
            self._running[thread] = job
        thread.daemon = True
        thread.start()

    def shutdown(self, timeout: float) -> None:
        """Cancel running Turns (their streams are closed) and wait up to ``timeout`` for them."""
        with self._lock:
            self._stopping = True
            running = dict(self._running)
        for job in running.values():
            job.cancellation.cancel(SHUTTING_DOWN)
        for thread in running:
            thread.join(timeout)

    def _work(self, job: TurnJob) -> None:
        try:
            execute(self._llm, job, self._inbox.put)
        finally:
            with self._lock:
                del self._running[threading.current_thread()]
