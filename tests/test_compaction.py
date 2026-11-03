"""What a Compaction keeps, summarizes and checks, apart from running it (ADR-0017)."""

import pytest

from allin.core.settings import CompactSettings
from allin.main.compaction import (
    CLOSING,
    compacted,
    kept_turns,
    next_part,
    part_room,
    saves_half,
    summary_request,
    summary_text,
    wants_compaction,
)
from allin.main.conversation import Conversation, Step, Turn

ANSWER = {
    "type": "message",
    "role": "assistant",
    "content": [{"type": "output_text", "text": "Mango"}],
}


def turn(prompt: str) -> Turn:
    return Turn(prompt=prompt, steps=[Step(output=[ANSWER])])


@pytest.mark.parametrize(
    ("sizes", "budget", "kept"),
    [
        ([100, 200, 300], 1_000, 3),  # all fit
        ([100, 200, 300], 500, 2),  # exactly: 200 + 300
        ([100, 200, 300], 499, 1),
        ([100, 200, 300], 299, 0),  # the last one alone does not fit
        ([400, 10, 10], 100, 2),  # whole Turns from the end, never one past a gap
        ([], 100, 0),
    ],
)
def test_the_last_whole_turns_that_fit_in_the_budget_stay_literal(
    sizes: list[int], budget: int, kept: int
):
    assert kept_turns(sizes, budget) == kept


@pytest.mark.parametrize(
    ("sizes", "room", "part"),
    [
        ([100, 200, 300], 1_000, 3),
        ([100, 200, 300], 300, 2),  # exactly: 100 + 200
        ([100, 200, 300], 299, 1),
        ([100, 200, 300], 99, 0),  # the oldest one alone does not fit
        ([], 100, 0),
    ],
)
def test_a_part_takes_the_oldest_turns_that_fit_in_its_room(sizes: list[int], room: int, part: int):
    assert next_part(sizes, room) == part


def test_a_parts_room_is_the_slot_less_the_summary_output_the_request_and_the_summary_so_far():
    assert part_room(limit=1_000, cap=250, overhead=50, summary=100) == 600


@pytest.mark.parametrize(
    ("replaced", "summary", "saves"), [(100, 50, True), (100, 51, False), (101, 50, True)]
)
def test_a_summary_must_take_at_most_half_of_what_it_replaces(
    replaced: int, summary: int, saves: bool
):
    assert saves_half(replaced, summary) is saves


@pytest.mark.parametrize(
    ("need", "auto", "wants"),
    [(851, True, True), (850, True, False), (2_000, False, False), (100, True, False)],
)
def test_it_compacts_on_its_own_when_the_step_needs_more_than_the_threshold_of_the_slot(
    need: int, auto: bool, wants: bool
):
    settings = CompactSettings(auto=auto, threshold=0.85)
    assert wants_compaction(settings, need, limit=1_000) is wants


def test_the_summary_request_is_the_compaction_sysprompt_the_summary_so_far_and_the_part():
    request = summary_request("Summarize.", "Old news.", 3, [turn("a"), turn("b")], "")
    assert request == [
        {"role": "developer", "content": "Summarize."},
        {"role": "user", "content": "[Conversation summary · Prompts 1\N{EN DASH}3]\n\nOld news."},
        {"role": "user", "content": "a"},
        ANSWER,
        {"role": "user", "content": "b"},
        ANSWER,
        {"role": "user", "content": CLOSING},
    ]


def test_the_humans_instructions_go_only_in_the_closing_request():
    request = summary_request("Summarize.", None, 0, [turn("a")], "keep the API decisions")
    assert request[1] == {"role": "user", "content": "a"}  # no summary yet
    assert request[-1] == {
        "role": "user",
        "content": f"{CLOSING}\n\nWhat the human asks of this summary: keep the API decisions",
    }


def test_the_summary_is_the_answer_of_the_last_step_without_its_reasoning():
    reasoning = {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "hm"}]}
    message = {
        "type": "message",
        "content": [
            {"type": "output_text", "text": "  We chose "},
            {"type": "output_text", "text": "mango.\n"},
        ],
    }
    assert summary_text([Step(output=[]), Step(output=[reasoning, message])]) == "We chose mango."
    assert summary_text([Step(output=[reasoning])]) == ""
    bare = [{"type": "message"}, {"type": "message", "content": [{"type": "output_text"}]}]
    assert summary_text([Step(output=[*bare, message])]) == "We chose mango."
    assert summary_text([]) == ""


def test_compacting_replaces_the_first_turns_by_the_summary_and_counts_them():
    conversation = Conversation(
        sysprompt="s", summary="old", summarized=2, turns=[turn("a"), turn("b"), turn("c")]
    )
    assert compacted(conversation, "new", replaced=2) == Conversation(
        sysprompt="s", summary="new", summarized=4, turns=[turn("c")], compacted_since_turn=True
    )
    # Its next Turn's Steps tell its Context again (#157).
    assert not compacted(conversation, "new", 2).with_turn(turn("d")).compacted_since_turn
