from allin.main.prompts import Prompt, PromptQueue


def test_prompts_come_out_in_arrival_order():
    queue = PromptQueue()
    queue.push(Prompt("aaaaaa", "first"))
    queue.push(Prompt("bbbbbb", "second"))
    assert queue.pop_ready(active=set()) == Prompt("aaaaaa", "first")
    assert queue.pop_ready(active=set()) == Prompt("bbbbbb", "second")
    assert queue.pop_ready(active=set()) is None


def test_a_session_with_an_active_turn_does_not_block_the_others():
    queue = PromptQueue()
    queue.push(Prompt("aaaaaa", "a-2"))
    queue.push(Prompt("bbbbbb", "b-1"))
    assert queue.pop_ready(active={"aaaaaa"}) == Prompt("bbbbbb", "b-1")
    assert queue.pop_ready(active={"aaaaaa"}) is None
    assert queue.pop_ready(active=set()) == Prompt("aaaaaa", "a-2")


def test_prompts_of_one_session_keep_their_order():
    queue = PromptQueue()
    queue.push(Prompt("aaaaaa", "1"))
    queue.push(Prompt("aaaaaa", "2"))
    assert queue.pop_ready(active=set()) == Prompt("aaaaaa", "1")
    assert queue.has("aaaaaa")
    assert queue.pop_ready(active=set()) == Prompt("aaaaaa", "2")
    assert not queue.has("aaaaaa")
