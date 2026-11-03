"""What the LLM is told of its own tokens, which it cannot see (#120, ADR-0004, ADR-0017):
that its last answer was cut, and how much Context it has left once the Context Notice
shows. Both are Notificaciones, so ``@conversation_logs`` turns each off."""

from pathlib import Path

import pytest

from allin.core.settings import Sampling
from allin.main.compaction import compacted
from allin.main.conversation import NOTIFICATIONS_HEADER, Conversation, Step, Turn
from allin.main.cut_turns import cut_line, last_cut
from allin.main.events import (
    InputReceived,
    MainCommandEntered,
    MainEvent,
    ServerChanged,
    ServerReady,
    WindowConnected,
)
from allin.main.sessions import Session
from tests.llm_fakes import FakeLlm, answer, completed
from tests.test_cut_turns import reply
from tests.test_dispatcher import KEY, MainHarness, server


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def second_prompt(main: MainHarness, session: Session, *before: MainEvent) -> str:
    """The ``user`` message the Session's second Prompt went in, after a first one."""
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        WindowConnected(session.id),
        *before,
        InputReceived(session.id, "uno"),
        InputReceived(session.id, "dos"),
        settle=True,
    )
    content = main.llm.requests[1]["input"][-1]["content"]
    assert isinstance(content, str)
    return content


def test_after_a_turn_cut_by_its_output_cap_the_next_prompt_tells_the_llm(main: MainHarness):
    session = main.create_session(sampling=Sampling(max_output_tokens=300))
    main.llm = FakeLlm(reply(80, 300), reply(80, 20))
    told = second_prompt(main, session)
    line = "Your last answer was cut: it reached its output cap of 300 tokens."
    assert told == f"{NOTIFICATIONS_HEADER}\n{line}\n\ndos"


def test_after_a_turn_that_filled_the_slot_the_next_prompt_tells_the_llm(main: MainHarness):
    main.server = server(context=3072)
    session = main.create_session(notices={"context": {"enabled": False}})
    main.llm = FakeLlm(reply(2400, 671), reply(80, 20))
    told = second_prompt(main, session)
    line = "Your last answer was cut: the Conversation filled its slot's context (3071 tokens)."
    assert told == f"{NOTIFICATIONS_HEADER}\n{line}\n\ndos"


def test_when_the_context_notice_shows_the_llm_is_told_how_much_is_left(main: MainHarness):
    main.server = server(context=10_000)
    session = main.create_session()
    main.llm = FakeLlm(
        [answer("a"), completed("", "a", tokens=(8_000, 500))],  # 85 %: the Notice shows
        [answer("b"), completed("", "b", tokens=(8_600, 100))],
    )
    told = second_prompt(main, session)
    line = "Your Context is at 85% of 10000 tokens: about 1500 tokens are left."
    assert told == f"{NOTIFICATIONS_HEADER}\n{line}\n\ndos"


def test_with_a_shared_pool_it_is_told_what_is_left_of_the_pool(main: MainHarness):
    main.server_up = False
    shared = ServerChanged(KEY, ServerReady(server(2, 10_000), shared_pool=True))
    session = main.create_session()
    main.llm = FakeLlm(
        [answer("a"), completed("", "a", tokens=(8_000, 500))],
        [answer("b"), completed("", "b", tokens=(8_600, 100))],
    )
    told = second_prompt(main, session, shared)
    assert "The shared pool is at 85% of 10000 tokens: about 1500 tokens are left." in told


def test_below_the_notice_s_threshold_nothing_is_told(main: MainHarness):
    main.server = server(context=10_000)
    session = main.create_session()
    main.llm = FakeLlm(
        [answer("a"), completed("", "a", tokens=(6_000, 500))],
        [answer("b"), completed("", "b", tokens=(6_600, 100))],
    )
    assert second_prompt(main, session) == "dos"


@pytest.mark.parametrize("source", ["cut", "context"])
def test_conversation_logs_off_keeps_each_out(main: MainHarness, source: str):
    main.server = server(context=3072)
    session = main.create_session()
    main.llm = FakeLlm(reply(2400, 671), reply(80, 20))  # cut, and past the threshold
    told = second_prompt(
        main, session, MainCommandEntered(f"/conversation_logs {session.id} off {source}")
    )
    assert ("Your last answer was cut" in told) == (source != "cut")
    assert ("Your Context is at" in told) == (source != "context")


def filled(prompt: str) -> Turn:
    usage = {"input_tokens": 2400, "output_tokens": 671}
    return Turn(prompt=prompt, steps=[Step(output=[], usage=usage)], cut="slot_limit")


def test_a_compaction_that_replaces_the_cut_turn_still_tells_it():
    conversation = Conversation(sysprompt="s", turns=[filled("p0")])
    note = last_cut(compacted(conversation, "We wrote a lot.", 1))
    assert note is not None
    assert cut_line(note) == (
        "Your last answer was cut: the Conversation filled its slot's context (3071 tokens)."
    )
    kept = compacted(
        Conversation(sysprompt="s", turns=[filled("p0"), Turn(prompt="p1", steps=[])]), "x", 1
    )
    assert last_cut(kept) is None, "its last Turn is literal and was not cut"


def test_a_rewind_takes_back_how_much_context_was_left(main: MainHarness):
    main.server = server(context=10_000)
    session = main.create_session()
    main.llm = FakeLlm(
        [answer("a"), completed("", "a", tokens=(8_000, 500))],  # 85 %: the Notice shows
        [answer("b"), completed("", "b", tokens=(100, 10))],
    )
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        WindowConnected(session.id),
        InputReceived(session.id, "uno"),
        MainCommandEntered(f"/rewind {session.id}"),
        InputReceived(session.id, "dos"),
        settle=True,
    )
    assert main.llm.requests[1]["input"][-1]["content"] == "dos"
