import os
from pathlib import Path

import pytest

from allin.core.errors import SessionStorageFailed
from allin.main.conversation import (
    Conversation,
    Step,
    Turn,
    archive_conversation,
    load_archived,
    load_conversation,
    save_conversation,
)

REASONING = {
    "type": "reasoning",
    "id": "rs_1",
    "content": [{"type": "reasoning_text", "text": "hm"}],
}
ANSWER = {
    "type": "message",
    "id": "msg_1",
    "role": "assistant",
    "content": [{"type": "output_text", "text": "Mango"}],
}


def test_flattening_puts_the_sysprompt_first_then_each_turn_prompt_and_output():
    conversation = Conversation(sysprompt="You are AllIn.").with_turn(
        Turn(prompt="pick a fruit", steps=[Step(output=[REASONING, ANSWER])])
    )
    assert conversation.request_input("which one?") == [
        {"role": "developer", "content": "You are AllIn."},
        {"role": "user", "content": "pick a fruit"},
        REASONING,
        ANSWER,
        {"role": "user", "content": "which one?"},
    ]


def test_every_step_of_a_turn_is_flattened_in_order():
    first, second = {"type": "function_call", "id": "fc_1"}, ANSWER
    conversation = Conversation(sysprompt="s").with_turn(
        Turn(prompt="p", steps=[Step(output=[first]), Step(output=[second])])
    )
    assert conversation.request_input("next")[2:4] == [first, second]


def test_a_saved_conversation_loads_back_grouped_by_turn(tmp_path: Path):
    conversation = Conversation(sysprompt="s").with_turn(
        Turn(prompt="p", steps=[Step(output=[ANSWER], usage={"total_tokens": 9})])
    )
    save_conversation(tmp_path, conversation)
    assert load_conversation(tmp_path) == conversation


def test_a_folder_without_a_conversation_loads_none(tmp_path: Path):
    assert load_conversation(tmp_path) is None


def test_a_failed_save_leaves_the_previous_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    before = Conversation(sysprompt="before")
    save_conversation(tmp_path, before)

    def crash(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)  # the crash comes after the data is written
    with pytest.raises(SessionStorageFailed):
        save_conversation(tmp_path, before.with_turn(Turn(prompt="p", steps=[])))
    assert load_conversation(tmp_path) == before


def test_a_compacted_conversation_puts_its_summary_as_a_user_message_after_the_sysprompt():
    conversation = Conversation(
        sysprompt="You are AllIn.",
        summary="We chose mango.",
        summarized=40,
        turns=[Turn(prompt="and now?", steps=[Step(output=[ANSWER])])],
    )
    assert conversation.request_input("go on") == [
        {"role": "developer", "content": "You are AllIn."},
        {
            "role": "user",
            "content": "[Conversation summary · Prompts 1\N{EN DASH}40]\n\nWe chose mango.",
        },
        {"role": "user", "content": "and now?"},
        ANSWER,
        {"role": "user", "content": "go on"},
    ]


def test_turns_are_numbered_counting_the_ones_a_summary_replaced():
    plain = Conversation(sysprompt="s", turns=[Turn(prompt="p", steps=[])])
    compacted = plain.model_copy(update={"summary": "x", "summarized": 3})
    assert (plain.next_turn, compacted.next_turn) == (1, 4)
    assert not Conversation(sysprompt="s").started
    assert Conversation(sysprompt="s", summary="x", summarized=2).started
    assert plain.started


def test_each_archived_conversation_is_kept_in_its_own_file_in_order(tmp_path: Path):
    first = Conversation(sysprompt="s", turns=[Turn(prompt="a", steps=[])])
    second = Conversation(sysprompt="s", summary="x", summarized=1)
    assert archive_conversation(tmp_path, first) == "compacted/1.json"
    assert archive_conversation(tmp_path, second) == "compacted/2.json"
    assert load_archived(tmp_path) == [first, second]
    assert load_archived(tmp_path / "none") == []
