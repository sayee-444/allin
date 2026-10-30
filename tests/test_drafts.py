from allin.core.drafts import put_back


def test_a_text_put_back_is_shown_and_the_rest_waits_behind_it():
    assert put_back(["typing"], "a1") == ["a1", "typing"]
    assert put_back(["a1", "typing"], "a2") == ["a2", "a1", "typing"]


def test_a_text_already_in_the_draft_moves_first_without_repeating():
    assert put_back(["typing", "a1"], "a1") == ["a1", "typing"]
    assert put_back(["a1"], "a1") == ["a1"]


def test_empty_blocks_are_dropped():
    assert put_back(["", "typing"], "a1") == ["a1", "typing"]
    assert put_back(["typing"], "") == ["typing"]
