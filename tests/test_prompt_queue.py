from dataclasses import replace
from typing import Any

import pytest

from allin.core.ids import SessionId, SubscriptionId
from allin.main.conversation import Attachment, Step
from allin.main.prompt_commands import TurnOverrides
from allin.main.prompts import Prompt, PromptQueue
from tests.origins import A_SESSION

A = SessionId("aaaaaa")
B = SessionId("bbbbbb")
NONE_ACTIVE = set[SessionId]()


def take_first(queue: PromptQueue, active: set[SessionId]) -> Prompt | None:
    waiting = queue.waiting(active=active)
    if not waiting:
        return None
    queue.remove(waiting[0])
    return waiting[0]


def test_prompts_come_out_in_arrival_order():
    queue = PromptQueue()
    queue.push(Prompt(A, "first"))
    queue.push(Prompt(B, "second"))
    assert take_first(queue, NONE_ACTIVE) == Prompt(A, "first")
    assert take_first(queue, NONE_ACTIVE) == Prompt(B, "second")
    assert take_first(queue, NONE_ACTIVE) is None


def test_a_session_with_an_active_turn_does_not_block_the_others():
    queue = PromptQueue()
    queue.push(Prompt(A, "a-2"))
    queue.push(Prompt(B, "b-1"))
    assert take_first(queue, {A}) == Prompt(B, "b-1")
    assert take_first(queue, {A}) is None
    assert take_first(queue, NONE_ACTIVE) == Prompt(A, "a-2")


def test_prompts_of_one_session_keep_their_order():
    queue = PromptQueue()
    queue.push(Prompt(A, "1"))
    queue.push(Prompt(A, "2"))
    assert take_first(queue, NONE_ACTIVE) == Prompt(A, "1")
    assert queue.has(A)
    assert take_first(queue, NONE_ACTIVE) == Prompt(A, "2")
    assert not queue.has(A)


def test_removing_a_prompt_takes_out_only_the_first_equal_one():
    queue = PromptQueue()
    queue.push(Prompt(A, "same"))
    queue.push(Prompt(B, "b"))
    queue.push(Prompt(A, "same"))
    queue.remove(Prompt(A, "same"))
    assert queue.all() == [Prompt(B, "b"), Prompt(A, "same")]


def test_an_entry_can_go_just_before_a_queued_prompt():
    queue = PromptQueue()
    queue.push(Prompt(B, "b"))
    queue.push(Prompt(A, "a"))
    compact = Prompt(A, "", "auto_compact")
    queue.insert_before(Prompt(A, "a"), compact)
    assert queue.all() == [Prompt(B, "b"), compact, Prompt(A, "a")]
    assert compact != Prompt(A, "")  # a Compaction is no Prompt of the same text


# What a human typed in a Session's Window during its Turn enters it (#121, ADR-0024).

TYPED = Prompt(A, "typed", queued=True, in_window=True)


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "compact"},
        {"queued": False},
        {"in_window": False},
        {"origin": A_SESSION},
        {"attachments": (Attachment(path="a.txt", kind="text"),)},
        {"overrides": TurnOverrides(temperature=0.2)},
        {"answered_by": "-y"},
        {"owed": (SubscriptionId("s1"),)},
        {"woke_by": (SubscriptionId("s1"),)},
        {"suspended": True},
    ],
)
def test_only_plain_text_typed_in_the_window_and_queued_may_join(change: dict[str, Any]):
    assert TYPED.may_join
    assert not replace(TYPED, **change).may_join


def test_mark_typed_in_window_marks_only_the_sessions_last_prompt_if_it_is_queued():
    queue = PromptQueue()
    queue.push(Prompt(A, "a1", queued=True))
    queue.push(Prompt(A, "a2", queued=True))
    queue.push(Prompt(B, "b1"))
    queue.mark_typed_in_window(A)
    queue.mark_typed_in_window(B)  # it waits for no Turn: nothing to join
    queue.mark_typed_in_window(SessionId("cccccc"))  # none queued: nothing
    assert [p.in_window for p in queue.all()] == [False, True, False]


def test_take_joining_takes_in_order_what_fits_and_leaves_the_rest():
    queue = PromptQueue()
    for text in ("uno", "dos", "trescientos"):
        queue.push(replace(TYPED, text=text))
    queue.push(replace(TYPED, session_id=B, text="otra"))
    assert [p.text for p in queue.take_joining(A, room=6)] == ["uno", "dos"]
    assert [p.text for p in queue.all()] == ["trescientos", "otra"]
    assert queue.take_joining(A, room=10) == []  # 11 characters: it waits its own Turn
    assert [p.text for p in queue.take_joining(A, room=11)] == ["trescientos"]


def test_take_joining_skips_what_others_sent_and_stops_at_what_the_human_typed_for_a_turn():
    queue = PromptQueue()
    queue.push(replace(TYPED, text="1"))
    queue.push(Prompt(A, "from a session", queued=True, origin=A_SESSION))
    queue.push(replace(TYPED, text="2"))
    queue.push(replace(TYPED, text="3", overrides=TurnOverrides(temperature=0.2)))
    queue.push(replace(TYPED, text="4"))
    assert [p.text for p in queue.take_joining(A, room=100)] == ["1", "2"]
    assert [p.text for p in queue.all()] == ["from a session", "3", "4"]


def test_take_joining_stops_at_a_compaction():
    queue = PromptQueue()
    queue.push(Prompt(A, "", "compact"))
    queue.push(replace(TYPED, text="1"))
    assert queue.take_joining(A, room=100) == []


def test_a_new_turn_leaves_what_its_session_has_waiting_to_wait_for_its_own():
    queue = PromptQueue()
    queue.push(replace(TYPED, text="1"))
    queue.push(replace(TYPED, session_id=B, text="otra"))
    queue.turn_started(A)
    assert [p.in_window for p in queue.all()] == [False, True]


def test_a_compaction_leaves_out_of_the_context_the_steps_its_session_had_ended():
    queue = PromptQueue()
    step = Step(output=[])
    queue.push(Prompt(A, "yielded", suspended=True, steps=(step, step)))
    queue.push(Prompt(A, "next"))
    queue.push(Prompt(B, "other", suspended=True, steps=(step,)))
    queue.compacted(A)
    assert [p.context_from for p in queue.all()] == [2, 0, 0]


def test_shift_enter_puts_the_last_prompt_ahead_of_the_others_but_not_of_a_turn_under_way():
    queue = PromptQueue()
    queue.push(Prompt(A, "auto", "auto_compact"))
    queue.push(Prompt(A, "duerme", suspended=True))
    queue.push(Prompt(A, "a2"))
    queue.push(Prompt(B, "b1"))
    queue.push(Prompt(A, "a3"))
    queue.push(Prompt(A, "otra"))
    queue.jump_last(A)
    assert [p.text for p in queue.all()] == ["auto", "duerme", "otra", "a2", "b1", "a3"]


def test_shift_enter_marks_the_sessions_turn_waiting_in_the_queue_to_be_cut():
    queue = PromptQueue()
    queue.push(Prompt(A, "a1"))
    assert not queue.interrupt(A), "a Prompt whose Turn never started is not cut"
    queue.push_front(Prompt(A, "duerme", suspended=True))
    queue.push_front(Prompt(A, "auto", "auto_compact", suspended=True))
    assert queue.interrupt(A)
    assert [p.interrupted for p in queue.all()] == [False, True, False]
