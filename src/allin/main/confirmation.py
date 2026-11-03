"""How a running Turn asks the user yes or no (ADR-0009) and waits for the answer.

The Turn's thread blocks in ``ask``. Main gives its slot back meanwhile, so other Sessions use
the Server, and resumes it only once the answer is in and the Turn holds a slot again, ahead
of new Prompts (a Continuation, ADR-0004). The decorator ``@confirm`` (F2) asks through this.
"""

import queue
from collections.abc import Callable
from dataclasses import dataclass

from allin.core.ids import SessionId
from allin.main.events import ConfirmationRequested, MainEvent
from allin.main.turn import Cancellation

_POLL = 0.1  # how often a waiting Turn looks whether it was cancelled


type Emit = Callable[[MainEvent], None]  # hands an event to Main's dispatcher


@dataclass(frozen=True)
class _Turn:
    session_id: SessionId
    cancellation: Cancellation
    emit: Emit


class ConfirmationGate:
    def __init__(self) -> None:
        self._turn: _Turn | None = None
        self._answers: queue.Queue[bool] = queue.Queue()

    def connect(
        self,
        session_id: SessionId,
        cancellation: Cancellation,
        emit: Emit,
    ) -> None:
        """The Turn it belongs to and where its events go; its executor connects it before
        the Turn runs."""
        self._turn = _Turn(session_id, cancellation, emit)

    def ask(self, title: str, text: str, inside: bool = False) -> bool:
        """From the Turn's thread: the user's answer, once the Turn may go on. ``inside``: what
        it asks about touches nothing outside its Session's Carpeta de trabajo (ADR-0026).

        Raises ``TurnCancelled`` if the Turn is cancelled while it waits.
        """
        assert self._turn is not None, "ask() before the executor connected the gate"
        turn = self._turn
        turn.emit(ConfirmationRequested(turn.session_id, title, text, inside))
        while True:
            turn.cancellation.check()
            try:
                return self._answers.get(timeout=_POLL)
            except queue.Empty:
                continue

    def resume(self, answer: bool) -> None:
        """From Main: the answer is in and the Turn holds a slot again."""
        self._answers.put(answer)
