"""The Origin of a Prompt or a command: the chain it came through, and its weakest link
(ADR-0013, #105)."""

import pytest

from allin.core.origin import (
    HUMAN,
    MAIN,
    Link,
    Origin,
    acting_process,
    actors,
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
        (from_subscription(from_session(HUMAN, A), "s1"), "session"),
    ],
)
def test_an_action_has_the_authority_of_the_weakest_link_of_its_chain(origin: Origin, weakest: str):
    assert weakest_link(origin) == weakest
    assert origin.is_human == (weakest == "human")


@pytest.mark.parametrize(
    ("origin", "acting", "process"),
    [
        (HUMAN, [], None),
        (from_process(HUMAN, "bot"), ["process:bot"], "bot"),
        (from_session(from_process(HUMAN, "bot"), A), ["process:bot", f"session:{A}"], None),
        # A Suscripción only passes it on.
        (
            from_subscription(from_process(from_session(HUMAN, A), "bot"), "s1"),
            [f"session:{A}", "process:bot"],
            "bot",
        ),
        (Origin.model_validate(["session"]), [], None),  # saved before #105: names no one
    ],
)
def test_who_acts_in_a_chain_is_its_last_session_or_proceso_adherido(
    origin: Origin, acting: list[str], process: str | None
):
    assert [str(link) for link in actors(origin)] == acting
    assert acting_process(origin) == process


def test_the_chain_keeps_every_link_in_order():
    origin = from_session(from_process(from_session(HUMAN, A), "rag"), B)
    assert origin.chain == (
        Link("human"),
        Link("session", A),
        Link("process", "rag"),
        Link("session", B),
    )
    assert origin.sessions == (A, B)
    assert from_process(from_session(HUMAN, A), "rag").sender == A  # its LLM called the process
    assert HUMAN.sender is None
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
