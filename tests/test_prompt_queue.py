from allin.core.ids import SessionId
from allin.main.prompts import Prompt, PromptQueue

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
