from allin.ui.status import context_status


def test_the_border_shows_the_context_against_its_limit_and_the_speed():
    assert context_status(400, 64000, 119.505) == "context 400 / 64k · 119.5 tok/s"
    assert context_status(400, 131072, None) == "context 400 / 131k · ? tok/s"
    assert context_status(400, 512, None) == "context 400 / 512 · ? tok/s"


def test_what_is_not_known_yet_is_shown_as_a_question_mark():
    assert context_status(None, None, None) == "context ? / ? · ? tok/s"
