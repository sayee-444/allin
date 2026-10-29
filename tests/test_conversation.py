import os
from pathlib import Path

import pytest

from allin.core.errors import SessionStorageFailed
from allin.main.conversation import Conversation, Step, Turn, load_conversation, save_conversation

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


def test_a_failed_save_leaves_the_previous_file_whole(tmp_path: Path, monkeypatch):
    before = Conversation(sysprompt="before")
    save_conversation(tmp_path, before)

    def crash(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)  # the crash comes after the data is written
    with pytest.raises(SessionStorageFailed):
        save_conversation(tmp_path, before.with_turn(Turn(prompt="p", steps=[])))
    assert load_conversation(tmp_path) == before
