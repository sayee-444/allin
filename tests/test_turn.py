import httpx2
import openai
import pytest

from allin.core.errors import LlmRequestFailed, TurnCancelled
from allin.main.conversation import Step
from allin.main.turn import TurnRequest, run_turn
from tests.llm_fakes import FakeLlm, answer, completed, failed, reasoning

REQUEST = TurnRequest(
    model="allin",
    input=[{"role": "developer", "content": "s"}, {"role": "user", "content": "hi"}],
    params={"temperature": 0.2},
)


def run(llm: FakeLlm, cancelled: bool = False) -> tuple[list[Step], list[tuple[str, str]]]:
    shown: list[tuple[str, str]] = []
    steps = run_turn(
        llm,
        REQUEST,
        lambda kind, text: shown.append((kind, text)),
        lambda: cancelled,
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
        openai.APIConnectionError(request=httpx2.Request("POST", "http://127.0.0.1:9931/v1")),
    ],
)
def test_a_step_that_does_not_complete_fails_the_turn_and_closes_its_stream(stream):
    llm = FakeLlm(stream)
    with pytest.raises(LlmRequestFailed):
        run(llm)
    assert llm.opened[0].closed


def test_a_cancelled_turn_stops_reading_and_closes_its_stream():
    llm = FakeLlm([answer("never shown"), completed("", "never")])
    with pytest.raises(TurnCancelled):
        run(llm, cancelled=True)
    assert llm.opened[0].closed


def test_each_step_keeps_llama_cpps_timings_and_is_reported_as_soon_as_it_ends():
    timings = {"prompt_n": 4, "predicted_n": 39, "predicted_per_second": 119.5}
    llm = FakeLlm([answer("ok"), completed("", "ok", timings=timings)])
    reported: list[Step] = []
    steps = run_turn(
        llm, REQUEST, lambda *_: None, lambda: False, lambda _raw: None, reported.append
    )
    assert reported == steps
    assert steps[0].timings == timings
