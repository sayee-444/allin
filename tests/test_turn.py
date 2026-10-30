import threading

import pytest

from allin.core.errors import LlmRequestFailed, LlmUnavailable, TurnCancelled
from allin.main.conversation import Step
from allin.main.turn import Cancellation, TurnRequest, run_turn
from tests.llm_fakes import (
    DroppedStream,
    FakeLlm,
    HangingStream,
    Unreachable,
    answer,
    completed,
    connection_lost,
    failed,
    reasoning,
    status_error,
    timed_out,
)

REQUEST = TurnRequest(
    model="allin",
    input=[{"role": "developer", "content": "s"}, {"role": "user", "content": "hi"}],
    params={"temperature": 0.2},
)


def run(
    llm: FakeLlm, cancellation: Cancellation | None = None
) -> tuple[list[Step], list[tuple[str, str]]]:
    shown: list[tuple[str, str]] = []
    steps = run_turn(
        llm,
        REQUEST,
        lambda kind, text: shown.append((kind, text)),
        cancellation or Cancellation(),
        lambda _raw: None,
        lambda _step: None,
    )
    return steps, shown


def test_reasoning_and_answer_are_streamed_as_they_arrive_and_kept_as_one_step():
    llm = FakeLlm(
        [
            reasoning("let me "),
            reasoning("think"),
            answer("Hel"),
            answer("lo"),
            completed("let me think", "Hello"),
        ]
    )
    steps, shown = run(llm)
    assert shown == [
        ("reasoning", "let me "),
        ("reasoning", "think"),
        ("answer", "Hel"),
        ("answer", "lo"),
    ]
    [step] = steps
    assert [item["type"] for item in step.output] == ["reasoning", "message"]
    assert step.output[1]["content"][0]["text"] == "Hello"
    assert step.usage is not None and step.usage["total_tokens"] == 10


def test_the_request_carries_the_model_the_input_and_only_the_params_given():
    llm = FakeLlm([completed("", "ok")])
    run(llm)
    assert llm.requests == [{"model": "allin", "input": REQUEST.input, "temperature": 0.2}]


@pytest.mark.parametrize(
    "stream",
    [
        [answer("half")],  # the connection dropped before response.completed
        [answer("half"), failed("out of memory")],
        connection_lost(),
    ],
)
def test_a_step_that_does_not_complete_fails_the_turn_and_closes_its_stream(stream):
    llm = FakeLlm(stream)
    with pytest.raises(LlmRequestFailed):
        run(llm)
    assert llm.opened[0].closed


def test_a_cancelled_turn_stops_reading_and_closes_its_stream():
    llm = FakeLlm([answer("never shown"), completed("", "never")])
    cancellation = Cancellation()
    cancellation.cancel("main is shutting down")
    with pytest.raises(TurnCancelled, match="main is shutting down"):
        run(llm, cancellation)
    assert llm.opened[0].closed


@pytest.mark.parametrize("ends_quietly", [False, True])
def test_cancelling_from_another_thread_closes_a_stream_still_waiting_for_the_server(
    ends_quietly: bool,
):
    stream = HangingStream([answer("half")], ends_quietly=ends_quietly)
    cancellation = Cancellation()
    outcome: list[BaseException] = []

    def turn() -> None:
        try:
            run(FakeLlm(stream), cancellation)
        except BaseException as error:  # noqa: BLE001 -- the test checks which one
            outcome.append(error)

    thread = threading.Thread(target=turn)
    thread.start()
    assert stream.waiting.wait(timeout=5)
    cancellation.cancel("window closed")
    thread.join(timeout=5)
    assert not thread.is_alive() and stream.closed
    [error] = outcome
    assert isinstance(error, TurnCancelled) and "window closed" in str(error)


def test_each_step_keeps_llama_cpps_timings_and_is_reported_as_soon_as_it_ends():
    timings = {"prompt_n": 4, "predicted_n": 39, "predicted_per_second": 119.5}
    llm = FakeLlm([answer("ok"), completed("", "ok", timings=timings)])
    reported: list[Step] = []
    steps = run_turn(
        llm, REQUEST, lambda *_: None, Cancellation(), lambda _raw: None, reported.append
    )
    assert reported == steps
    assert steps[0].timings == timings


@pytest.mark.parametrize(
    "stream",
    [connection_lost(), DroppedStream([answer("half")])],
    ids=["server down", "server dies mid-step"],
)
def test_a_server_that_cannot_be_reached_makes_the_llm_unavailable(stream):
    llm = FakeLlm(stream)
    with pytest.raises(LlmUnavailable):
        run(llm)
    assert llm.opened[0].closed


def test_an_unreachable_server_is_unavailable_even_before_a_stream_opens():
    with pytest.raises(LlmUnavailable):
        run(FakeLlm(Unreachable()))


def test_a_server_still_loading_its_model_is_unavailable():
    with pytest.raises(LlmUnavailable):
        run(FakeLlm(status_error(503)))


@pytest.mark.parametrize(
    "error", [status_error(400), status_error(500), timed_out()], ids=["400", "500", "timeout"]
)
def test_other_http_errors_and_a_slow_server_only_fail_the_step(error):
    with pytest.raises(LlmRequestFailed) as raised:
        run(FakeLlm(error))
    assert not isinstance(raised.value, LlmUnavailable)
