from allin.main.conversation import Step
from allin.main.usage import context_tokens, tokens_per_second, turn_tokens

# Usage and timings as llama-server sent them for "hola" in a Session that had chatted before.
HOLA = Step(
    output=[],
    usage={
        "input_tokens": 328,
        "input_tokens_details": {"cached_tokens": 324},
        "output_tokens": 39,
        "total_tokens": 367,
    },
    timings={"cache_n": 324, "prompt_n": 4, "predicted_n": 39, "predicted_per_second": 119.5},
)
# The first Step of a tool call, then the Step that reads its result back.
TOOL_CALL = Step(
    output=[],
    usage={
        "input_tokens": 333,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 119,
        "total_tokens": 452,
    },
    timings={"cache_n": 0, "prompt_n": 333, "predicted_n": 119, "predicted_per_second": 116.8},
)
TOOL_RESULT = Step(
    output=[],
    usage={
        "input_tokens": 480,
        "input_tokens_details": {"cached_tokens": 452},
        "output_tokens": 60,
        "total_tokens": 540,
    },
    timings={"cache_n": 452, "prompt_n": 28, "predicted_n": 60, "predicted_per_second": 110.0},
)


def test_the_context_is_what_the_last_step_read_and_wrote():
    assert context_tokens([HOLA]) == 367


def test_a_turn_of_several_steps_does_not_add_up_their_input_into_the_context():
    assert context_tokens([TOOL_CALL, TOOL_RESULT]) == 540


def test_the_speed_is_how_fast_the_last_step_generated():
    assert tokens_per_second([TOOL_CALL, TOOL_RESULT]) == 110.0


def test_a_turns_tokens_are_what_it_generated_plus_the_input_the_server_had_not_cached():
    assert turn_tokens([HOLA]) == 4 + 39
    assert turn_tokens([TOOL_CALL, TOOL_RESULT]) == (333 + 119) + (28 + 60)


def test_without_timings_the_new_input_is_the_input_minus_what_was_cached():
    step = HOLA.model_copy(update={"timings": None})
    assert turn_tokens([step]) == 4 + 39
    assert tokens_per_second([step]) is None


def test_steps_without_usage_count_nothing():
    bare = Step(output=[])
    assert context_tokens([bare]) is None
    assert context_tokens([]) is None
    assert turn_tokens([bare]) == 0
