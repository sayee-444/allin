"""A cut Turn: its last Step ended at the Output cap or the slot's limit (ADR-0004)."""

from pathlib import Path

import pytest
from openai.types.responses import ResponseStreamEvent

from allin.core.settings import Sampling
from allin.ipc.messages import BoxShow, ChatAppend, InputRestore
from allin.main.chat_rebuild import rebuild_chat
from allin.main.conversation import Step
from allin.main.cut_turns import DEFAULT_OUTPUT_CAP, cut_reason, output_cap
from allin.main.events import InputReceived, MainCommandEntered, WindowConnected
from allin.main.log import read_log
from allin.main.sessions import Session
from tests.llm_fakes import FakeLlm, answer, completed
from tests.test_dispatcher import MainHarness, server


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def step(input_tokens: int, output_tokens: int) -> Step:
    return Step(output=[], usage={"input_tokens": input_tokens, "output_tokens": output_tokens})


def reply(input_tokens: int, output_tokens: int) -> list[ResponseStreamEvent]:
    return [answer("cut sho"), completed("", "cut sho", tokens=(input_tokens, output_tokens))]


def test_the_default_output_cap_is_16384_or_a_quarter_of_the_slot_if_smaller():
    assert output_cap(Sampling(), slot_context=131072) == DEFAULT_OUTPUT_CAP == 16384
    assert output_cap(Sampling(), slot_context=3072) == 768
    assert output_cap(Sampling(), slot_context=3074) == 768  # whole tokens
    assert output_cap(Sampling(), slot_context=3) == 1  # never nothing


def test_an_output_cap_in_the_settings_wins_over_the_default():
    assert output_cap(Sampling(max_output_tokens=300), slot_context=3072) == 300


def test_a_step_that_reached_its_output_cap_is_cut_by_the_cap():
    assert cut_reason(step(80, 300), cap=300, slot_context=3072) == "output_cap"


def test_a_step_that_filled_the_slot_but_one_token_is_cut_by_the_slot_limit():
    # Measured with build 10909: 80 in + 2 991 out = 3 071 of 3 072, and "completed".
    assert cut_reason(step(80, 2991), cap=16384, slot_context=3072) == "slot_limit"


def test_a_step_that_ended_on_its_own_is_not_cut():
    assert cut_reason(step(80, 299), cap=300, slot_context=3072) is None
    assert cut_reason(step(80, 2990), cap=16384, slot_context=3072) is None
    assert cut_reason(Step(output=[]), cap=300, slot_context=3072) is None


def test_each_step_sends_the_default_output_cap(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(reply(10, 5))
    main.run(InputReceived(session.id, "hi"))
    [first] = main.llm.requests
    main.server = server(context=8192)
    main.llm = FakeLlm(reply(10, 5))
    main.run(InputReceived(session.id, "again"))
    [second] = main.llm.requests
    assert (first["max_output_tokens"], second["max_output_tokens"]) == (16384, 2048)


def ask(main: MainHarness, session: Session, *replies: list[ResponseStreamEvent]) -> None:
    main.llm = FakeLlm(*replies)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        WindowConnected(session.id),
        InputReceived(session.id, "write a lot"),
        settle=True,
    )


def notices(main: MainHarness, session: Session) -> list[BoxShow]:
    """The cut Turn Notices its Window showed; a full slot also crosses the Context's."""
    [window] = main.windows(session.id)
    return [m for m in window.sent if isinstance(m, BoxShow) and m.title == "Turn cut"]


def test_a_turn_that_fills_the_slot_is_saved_cut_with_what_it_generated(main: MainHarness):
    main.server = server(context=3072)
    session = main.create_session()
    ask(main, session, reply(2400, 671))
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    [turn] = conversation.turns
    assert turn.cut == "slot_limit" and turn.steps[0].output != []
    [box] = notices(main, session)
    assert box.text == (
        f"the Conversation filled the slot's 3072 tokens of context; compact it"
        f" (/session compact {session.id}) or link the Session to a Server with more context"
        f" (/session set {session.id} server=<name>)"
    )
    assert box.title == "Turn cut"
    [finished] = [
        e for e in read_log(main.root / session.id / "log.jsonl") if e.event == "turn_finished"
    ]
    assert finished.fields["cut"] == "slot_limit"


def test_a_turn_cut_by_a_low_output_cap_says_so_and_keeps_its_prompt_out_of_the_input(
    main: MainHarness,
):
    session = main.create_session(sampling=Sampling(max_output_tokens=300))
    ask(main, session, reply(80, 300))
    [request] = main.llm.requests
    assert request["max_output_tokens"] == 300
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None and conversation.turns[0].cut == "output_cap"
    [box] = notices(main, session)
    assert box.text == (
        f"it reached its output cap of 300 tokens; raise it with /session set {session.id}"
        " sampling.max_output_tokens=<n>, or ask it to go on"
    )
    [window] = main.windows(session.id)
    assert not any(isinstance(message, InputRestore) for message in window.sent)
    assert ChatAppend(role="info", label="cut", text="turn cut: it reached its output cap") in (
        window.sent
    )


def test_a_turn_that_ends_on_its_own_is_not_marked(main: MainHarness):
    session = main.create_session()
    ask(main, session, reply(80, 299))
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None and conversation.turns[0].cut is None
    assert notices(main, session) == []
    [window] = main.windows(session.id)
    assert not any(isinstance(m, ChatAppend) and m.label == "cut" for m in window.sent)


def test_the_cut_notice_can_be_turned_off(main: MainHarness):
    session = main.create_session(notices={"cut_turn": {"enabled": False}})
    main.server = server(context=3072)
    ask(main, session, reply(2400, 671))
    assert notices(main, session) == []


def test_the_rebuilt_chat_shows_the_mark_of_a_cut_turn(main: MainHarness):
    main.server = server(context=3072)
    session = main.create_session()
    ask(main, session, reply(2400, 671))
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    log = read_log(main.root / session.id / "log.jsonl")
    chat = rebuild_chat(conversation, log, session.settings)
    assert chat[-1] == ChatAppend(
        role="info", label="cut", text="turn cut: it filled the slot's context"
    )
