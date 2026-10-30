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


def test_each_turn_runs_on_its_own_thread_so_none_waits_for_another():
    hanging = HangingStream([answer("half")])
    inbox: queue.Queue[MainEvent] = queue.Queue()
    executors = TurnExecutors(FakeLlm(hanging, full_reply("", "next")), inbox)
    try:
        cancelled = TurnJob("aaaaaa", REQUEST)
        executors.submit(cancelled)
        assert hanging.waiting.wait(timeout=5)
        executors.submit(TurnJob("bbbbbb", REQUEST))  # no fixed pool: it does not wait
        assert turn_end(inbox, "bbbbbb").steps is not None

        cancelled.cancellation.cancel("window closed")
        end = turn_end(inbox, "aaaaaa")
        assert isinstance(end.error, TurnCancelled) and hanging.closed
    finally:
        executors.shutdown(timeout=5)


def test_shutdown_cancels_the_turns_that_are_running():
    hanging = HangingStream([answer("half")])
    inbox: queue.Queue[MainEvent] = queue.Queue()
    executors = TurnExecutors(FakeLlm(hanging), inbox)
    executors.submit(TurnJob("aaaaaa", REQUEST))
    assert hanging.waiting.wait(timeout=5)
    executors.shutdown(timeout=5)
    end = turn_end(inbox, "aaaaaa")
    assert isinstance(end.error, TurnCancelled) and "shutting down" in str(end.error)


def test_a_turn_submitted_while_main_shuts_down_is_cancelled():
    inbox: queue.Queue[MainEvent] = queue.Queue()
    executors = TurnExecutors(FakeLlm(full_reply("", "late")), inbox)
    executors.shutdown(timeout=5)
    executors.submit(TurnJob("aaaaaa", REQUEST))
    assert isinstance(turn_end(inbox, "aaaaaa").error, TurnCancelled)
