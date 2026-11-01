from allin.core.ids import BoxId
from allin.ui.boxes import Box, BoxClosed, BoxStack, box_rows
from allin.ui.keys import Key, KeyChord, Paste

NOTICE = Box(BoxId("n1"), "notice", "Context", "80% full")
QUESTION = Box(BoxId("c1"), "confirm", "Reset", "Reset Main's Settings?")


def test_with_no_box_keys_are_left_to_the_input():
    stack = BoxStack()
    assert stack.current is None
    assert stack.key(KeyChord("s")) is None
    assert not stack.takes_keys


def test_a_confirmation_is_answered_yes_with_s_or_y_and_no_with_n():
    for char, answer in [("s", True), ("S", True), ("y", True), ("n", False), ("N", False)]:
        stack = BoxStack()
        stack.show(QUESTION)
        assert stack.key(KeyChord(char)) == BoxClosed(QUESTION.box_id, answer)
        assert stack.current is None


def test_a_confirmation_ignores_other_keys_and_pastes():
    stack = BoxStack()
    stack.show(QUESTION)
    for event in [KeyChord("x"), KeyChord(Key.ENTER), Paste("s")]:
        assert stack.key(event) is None
    assert stack.current == QUESTION


def test_a_notice_closes_with_any_key():
    stack = BoxStack()
    stack.show(NOTICE)
    assert stack.key(KeyChord(Key.ENTER)) == BoxClosed(NOTICE.box_id, None)


def test_a_resize_or_scroll_never_answers_a_box():
    stack = BoxStack()
    stack.show(NOTICE)
    for key in (Key.RESIZE, Key.PAGE_UP, Key.PAGE_DOWN):
        assert stack.key(KeyChord(key)) is None
    assert stack.current == NOTICE


def test_boxes_wait_in_arrival_order_and_hiding_one_drops_it_wherever_it_is():
    stack = BoxStack()
    stack.show(NOTICE)
    stack.show(QUESTION)
    assert stack.current == NOTICE
    stack.hide(QUESTION.box_id)
    stack.key(KeyChord("x"))
    assert stack.current is None


def test_showing_a_box_again_does_not_queue_it_twice():
    stack = BoxStack()
    stack.show(NOTICE)
    stack.show(NOTICE)
    stack.key(KeyChord("x"))
    assert stack.current is None


def test_a_box_shows_its_text_wrapped_and_the_keys_that_answer_it():
    rows = box_rows(QUESTION, width=12)
    assert rows[:-1] == ["Reset Main's", " Settings?"]
    assert rows[-1] == "[s]i / [n]o"
    assert box_rows(NOTICE, width=40)[-1] == "any key closes"
