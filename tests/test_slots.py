import pytest

from allin.main.slots import ServerProps, capacity, parse_props, server_name

# What llama-server's /props says, trimmed to what Main reads.
PROPS = {
    "total_slots": 2,
    "model_path": "/models/m.gguf",
    "default_generation_settings": {"n_ctx": 64000},
}


def test_props_give_the_slots_the_context_of_each_slot_and_the_model():
    assert parse_props(PROPS) == ServerProps(
        total_slots=2, slot_context=64000, model_path="/models/m.gguf"
    )


@pytest.mark.parametrize(
    "props",
    [
        "not a dict",
        {**PROPS, "total_slots": 0},
        {**PROPS, "total_slots": "two"},
        {**PROPS, "total_slots": True},
        {key: value for key, value in PROPS.items() if key != "total_slots"},
        {**PROPS, "default_generation_settings": {}},
        {**PROPS, "default_generation_settings": {"n_ctx": 0}},
        {**PROPS, "default_generation_settings": None},
    ],
)
def test_props_that_say_nothing_usable_give_nothing(props: object):
    assert parse_props(props) is None


def test_a_missing_model_path_is_left_empty():
    props = {key: value for key, value in PROPS.items() if key != "model_path"}
    assert parse_props(props) == ServerProps(total_slots=2, slot_context=64000, model_path="")


SERVER = ServerProps(total_slots=2, slot_context=64000, model_path="m.gguf")


def test_without_parallel_main_runs_as_many_turns_as_the_server_has_slots():
    assert capacity(SERVER, parallel=None) == 2


def test_parallel_can_only_lower_the_capacity():
    assert capacity(SERVER, parallel=1) == 1
    assert capacity(SERVER, parallel=3) == 2


def test_until_supervisors_exist_a_server_is_named_by_its_port():
    assert server_name("http://127.0.0.1:9931/v1") == "9931"
    assert server_name("http://localhost/v1") == "80"
