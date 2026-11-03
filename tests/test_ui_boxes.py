from allin.core.ids import BoxId
from allin.ui.boxes import Box, BoxClosed, BoxStack, box_rows
from allin.ui.keys import Key, KeyChord, Paste

NOTICE = Box(BoxId("n1"), "notice", "Context", "80% full")
QUESTION = Box(BoxId("c1"), "confirm", "Reset", "Reset Main's Settings?")
SECRET = Box(BoxId("k1"), "secret", "Secret token of bot", "Its value")


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


def test_a_secret_box_takes_what_is_typed_and_pasted_and_gives_it_on_enter():
    stack = BoxStack()
    stack.show(SECRET)
    typed = [KeyChord("a"), Paste("b c\r\n"), KeyChord("x"), KeyChord(Key.BACKSPACE)]
    for event in [*typed, KeyChord("d"), KeyChord("u", ctrl=True), KeyChord(Key.LEFT)]:
        assert stack.key(event) is None
    assert stack.key(KeyChord(Key.ENTER)) == BoxClosed(SECRET.box_id, None, value="ab cd")
    assert stack.current is None


def test_a_secret_box_left_empty_gives_nothing_on_enter():
    stack = BoxStack()
    stack.show(SECRET)
    assert stack.key(KeyChord(Key.ENTER)) == BoxClosed(SECRET.box_id, None)


def test_a_secret_box_never_shows_what_was_typed_and_the_next_one_starts_empty():
    stack = BoxStack()
    stack.show(SECRET)
    stack.show(QUESTION)
    stack.key(Paste("hunter2"))
    rows = box_rows(SECRET, width=40, hidden=stack.typed)
    assert "hunter2" not in " ".join(rows)
    assert rows[-2:] == ["•••••••", "Enter saves · empty Enter cancels"]
    assert box_rows(SECRET, width=4, hidden=9)[-2] == "••••"
    stack.key(KeyChord(Key.ENTER))
    assert stack.typed == 0


def test_a_secret_box_withdrawn_forgets_what_was_typed():
    stack = BoxStack()
    stack.show(SECRET)
    stack.key(Paste("hunter2"))
    stack.hide(SECRET.box_id)
    stack.show(Box(BoxId("k2"), "secret", "Secret webhook of bot", "Its value"))
    assert stack.typed == 0
