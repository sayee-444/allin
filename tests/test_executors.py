import queue

from allin.core.errors import TurnCancelled
from allin.main.events import MainEvent, TurnEnded
from allin.main.executors import TurnExecutors, TurnJob
from allin.main.turn import TurnRequest
from tests.llm_fakes import FakeLlm, HangingStream, answer, full_reply

REQUEST = TurnRequest(model="allin", input=[{"role": "user", "content": "hi"}], params={})


def turn_end(inbox: queue.Queue[MainEvent], session_id: str) -> TurnEnded:
    while True:
        event = inbox.get(timeout=5)
        if isinstance(event, TurnEnded) and event.session_id == session_id:
            return event


def test_cancelling_a_job_ends_its_turn_as_cancelled_and_frees_the_executor():
    hanging = HangingStream([answer("half")])
    inbox: queue.Queue[MainEvent] = queue.Queue()
    executors = TurnExecutors(FakeLlm(hanging, full_reply("", "next")), 1, inbox)
    executors.start()
    try:
        cancelled = TurnJob("aaaaaa", REQUEST)
        executors.submit(cancelled)
        assert hanging.waiting.wait(timeout=5)
        cancelled.cancellation.cancel("window closed")
        end = turn_end(inbox, "aaaaaa")
        assert isinstance(end.error, TurnCancelled) and hanging.closed

        executors.submit(TurnJob("bbbbbb", REQUEST))  # the only executor takes it
        assert turn_end(inbox, "bbbbbb").steps is not None
    finally:
        executors.shutdown(timeout=5)


def test_shutdown_cancels_the_turns_that_are_running():
    hanging = HangingStream([answer("half")])
    inbox: queue.Queue[MainEvent] = queue.Queue()
    executors = TurnExecutors(FakeLlm(hanging), 1, inbox)
    executors.start()
    executors.submit(TurnJob("aaaaaa", REQUEST))
    assert hanging.waiting.wait(timeout=5)
    executors.shutdown(timeout=5)
    end = turn_end(inbox, "aaaaaa")
    assert isinstance(end.error, TurnCancelled) and "shutting down" in str(end.error)
