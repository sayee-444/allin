"""The Origin of a Prompt or a command: the chain it came through, and its weakest link
(ADR-0013, #105)."""

import pytest

from allin.core.origin import (
    HUMAN,
    MAIN,
    Link,
    Origin,
    from_main,
    from_process,
    from_session,
    from_subscription,
    weakest_link,
)
from allin.main.conversation import Turn

A, B = "aaaa", "bbbb"


@pytest.mark.parametrize(
    ("origin", "weakest"),
    [
        (HUMAN, "human"),
        (MAIN, "main"),
        (from_session(HUMAN, A), "session"),  # human → A
        (from_session(from_session(HUMAN, A), B), "session"),  # human → A → B
        (from_session(from_process(from_session(HUMAN, A), "rag"), B), "session"),
        (from_process(HUMAN, "rag"), "session"),  # a human calling it does not make it human
        (from_session(MAIN, A), "session"),  # what Main asked, then a Session
        (from_main(HUMAN), "main"),  # Main acting on its own for a human's action
        (from_subscription(from_session(HUMAN, A), "s1"), "session"),
    ],
)
def test_an_action_has_the_authority_of_the_weakest_link_of_its_chain(origin: Origin, weakest: str):
    assert weakest_link(origin) == weakest
    assert origin.is_human == (weakest == "human")


def test_the_chain_keeps_every_link_in_order():
    origin = from_session(from_process(from_session(HUMAN, A), "rag"), B)
    assert origin.chain == (
        Link("human"),
        Link("session", A),
        Link("process", "rag"),
        Link("session", B),
    )
    assert origin.sessions == (A, B)
    assert origin.labels == ["human", f"session:{A}", "process:rag", f"session:{B}"]
    assert str(origin) == f"human > session:{A} > process:rag > session:{B}"


def test_it_is_saved_as_its_labels_and_read_back_whole():
    origin = from_subscription(from_session(HUMAN, A), "s1")
    saved = origin.model_dump_json()
    assert saved == f'["human","session:{A}","subscription:s1"]'
    assert Origin.model_validate_json(saved) == origin


@pytest.mark.parametrize(
    ("saved", "read"),
    [("human", HUMAN), ("main", MAIN), ("session", Origin(chain=(Link("session"),)))],
)
def test_an_origin_saved_before_the_chain_is_read_as_one_link(saved: str, read: Origin):
    assert Origin.model_validate(saved) == read
    assert not Origin.model_validate("session").is_human


@pytest.mark.parametrize("saved", [[], ["robot"], ["session:a:b:c", "human:x"], "human > main"])
def test_a_chain_that_is_not_one_is_refused(saved: object):
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic's ValidationError is a ValueError
        Origin.model_validate(saved)


def test_a_turn_saved_before_the_chain_keeps_who_sent_it():
    turn = Turn.model_validate_json('{"prompt": "x", "steps": [], "origin": "session"}')
    assert not turn.origin.is_human
    assert Turn.model_validate_json('{"prompt": "x", "steps": []}').origin == HUMAN
