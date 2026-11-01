from allin.core.ids import SessionId
from allin.main.prompts import Prompt, PromptQueue

A = SessionId("aaaaaa")
B = SessionId("bbbbbb")


def test_prompts_come_out_in_arrival_order():
    queue = PromptQueue()
    queue.push(Prompt(A, "first"))
    queue.push(Prompt(B, "second"))
    assert queue.pop_ready(active=set[SessionId]()) == Prompt(A, "first")
    assert queue.pop_ready(active=set[SessionId]()) == Prompt(B, "second")
    assert queue.pop_ready(active=set[SessionId]()) is None


def test_a_session_with_an_active_turn_does_not_block_the_others():
    queue = PromptQueue()
    queue.push(Prompt(A, "a-2"))
    queue.push(Prompt(B, "b-1"))
    assert queue.pop_ready(active={A}) == Prompt(B, "b-1")
    assert queue.pop_ready(active={A}) is None
    assert queue.pop_ready(active=set[SessionId]()) == Prompt(A, "a-2")


def test_prompts_of_one_session_keep_their_order():
    queue = PromptQueue()
    queue.push(Prompt(A, "1"))
    queue.push(Prompt(A, "2"))
    assert queue.pop_ready(active=set[SessionId]()) == Prompt(A, "1")
    assert queue.has(A)
    assert queue.pop_ready(active=set[SessionId]()) == Prompt(A, "2")
    assert not queue.has(A)
