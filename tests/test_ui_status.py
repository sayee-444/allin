from allin.ui.status import context_status


def test_the_border_shows_the_context_against_its_limit_and_the_speed():
    assert context_status(400, 8192, 119.505) == "context 400/8192 · 119.5 tok/s"


def test_what_is_not_known_yet_is_shown_as_a_question_mark():
    assert context_status(None, 131072, None) == "context ?/131072 · ? tok/s"
