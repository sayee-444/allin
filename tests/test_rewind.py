"""Rewind of a Session (#58): ``/rewind <sid> [n]`` undoes its last Prompts, whatever their
Origin, and gives a human's texts back to the input; before a Compaction it brings back what
was archived (ADR-0017)."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from allin.core.origin import HUMAN, from_session
from allin.ipc.messages import ChatAppend, ChatClear, InputRestore
from allin.main.chat_rebuild import compacted_entries
from allin.main.conversation import Conversation, Step, Turn
from allin.main.events import MainCommandEntered
from allin.main.rewind import rewind
from allin.main.sessions import Session
from allin.ui.screen import ClearChat, ScreenUpdate
from allin.window.__main__ import MainListener, WindowRecord
from tests.llm_fakes import FakeLlm
from tests.test_compaction_turns import answers, compact, conversation, reply, talk, words
from tests.test_dispatcher import MainHarness, ended, server


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.servers.tokens_of = words
    harness.server = server(context=1_000)
    return harness


def rewind_cmd(session: Session, n: int | None = None) -> MainCommandEntered:
    return MainCommandEntered(f"/rewind {session.id}" + ("" if n is None else f" {n}"))


def results(main: MainHarness) -> list[str]:
    return [
        str(entry.fields["result"])
        for entry in main.main_log()
        if entry.event == "command" and str(entry.fields.get("command", "")).startswith("/rewind")
    ]


def prompts(main: MainHarness, session: Session) -> list[str]:
    loaded = main.sessions.load_conversation(session)
    return [] if loaded is None else [turn.prompt for turn in loaded.turns]


def after_last_clear(messages: list[BaseModel]) -> list[BaseModel]:
    clears = [n for n, message in enumerate(messages) if isinstance(message, ChatClear)]
    assert clears, "the Window's Chat was never rebuilt"
    return messages[clears[-1] + 1 :]


def users(messages: list[BaseModel]) -> list[str]:
    return [m.text for m in messages if isinstance(m, ChatAppend) and m.role == "user"]


def restored(messages: list[BaseModel]) -> list[str]:
    return [m.text for m in messages if isinstance(m, InputRestore)]


def rewound(main: MainHarness, session: Session) -> list[dict[str, object]]:
    return [entry.fields for entry in main.session_log(session) if entry.event == "rewound"]


# The Conversation alone


def turn(prompt: str) -> Turn:
    return Turn(prompt=prompt, steps=[Step(output=[])])


def test_rewinding_cuts_the_last_turns_and_says_which_were_undone():
    whole = Conversation(sysprompt="s", turns=[turn("a"), turn("b"), turn("c")])
    result = rewind(whole, [], 2)
    assert [t.prompt for t in result.conversation.turns] == ["a"]
    assert [t.prompt for t in result.undone] == ["b", "c"]
    assert result.turn == 1 and result.conversation.sysprompt == "s"


def test_rewinding_more_than_there_is_stops_at_the_start():
    whole = Conversation(sysprompt="s", turns=[turn("a")])
    result = rewind(whole, [], 5)
    assert result.conversation.turns == [] and result.turn == 0
    assert [t.prompt for t in result.undone] == ["a"]


def test_rewinding_to_before_a_compaction_takes_the_archive_without_its_summary():
    archived = Conversation(sysprompt="s", turns=[turn(p) for p in "abcd"])
    compacted = Conversation(sysprompt="s", summary="sum", summarized=3, turns=[turn("d")])
    result = rewind(compacted, [archived], 2)
    assert result.conversation.summary is None and result.conversation.summarized == 0
    assert [t.prompt for t in result.conversation.turns] == ["a", "b"]
    assert [t.prompt for t in result.undone] == ["c", "d"]


def test_rewinding_after_the_cut_keeps_the_summary():
    compacted = Conversation(sysprompt="s", summary="sum", summarized=3, turns=[turn("d")])
    result = rewind(compacted, [], 1)
    assert result.conversation.summary == "sum" and result.conversation.turns == []
    assert result.turn == 3


# Through Main


def test_rewind_undoes_the_last_prompt_and_its_text_goes_back_to_the_input(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(*answers(3))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *talk(session, 3),
        rewind_cmd(session),
        settle=True,
    )
    assert prompts(main, session) == ["p0", "p1"]
    [window] = main.windows(session.id)
    assert restored(window.sent) == ["p2"]
    assert users(after_last_clear(window.sent)) == ["p0", "p1"]
    [entry] = rewound(main, session)
    assert entry["turn"] == 2 and entry["texts"] == ["p2"]
    assert results(main) == ["ok"]


def test_rewind_n_undoes_that_many_in_order_and_more_than_there_are_stays_at_the_start(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(4))
    chat = main.run(
        MainCommandEntered(f"/open {session.id}"),
        *talk(session, 4),
        rewind_cmd(session, 3),
        rewind_cmd(session, 5),
        settle=True,
    )
    assert prompts(main, session) == []
    [window] = main.windows(session.id)
    # Each one shown pushes the one before it back: the first undone ends up first.
    assert restored(window.sent) == ["p3", "p2", "p1", "p0"]
    assert any("only 1 to undo" in line and "start" in line for line in chat)
    assert results(main) == ["ok", "ok"]


def test_rewind_zero_does_nothing(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(*answers(1))
    main.run(*talk(session, 1), rewind_cmd(session, 0), settle=True)
    assert prompts(main, session) == ["p0"]
    assert rewound(main, session) == []


def test_a_bad_count_is_an_error(main: MainHarness):
    session = main.create_session()
    main.run(rewind_cmd(session, -1), MainCommandEntered(f"/rewind {session.id} dos"))
    assert all(result.startswith("error invalid_arguments") for result in results(main))


def test_a_prompt_of_another_origin_is_undone_but_its_text_stays_in_the_log(main: MainHarness):
    session, other = main.create_session(), main.create_session()
    main.llm = FakeLlm(*answers(1))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/send {session.id} desde otra", origin=from_session(HUMAN, other.id)),
        rewind_cmd(session),
        settle=True,
    )
    assert prompts(main, session) == []
    [window] = main.windows(session.id)
    assert restored(window.sent) == []
    [entry] = rewound(main, session)
    assert entry["texts"] == [f"[from Session {other.id}]\ndesde otra"]
    assert entry["origins"] == [["human", f"session:{other.id}"]]


def test_after_a_compaction_a_rewind_to_before_the_cut_brings_back_the_archive(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply("We chose mango."))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *talk(session, 6),
        compact(session),
        rewind_cmd(session),  # p5: after the cut, the summary stays
        settle=True,
    )
    kept = conversation(main, session)
    assert kept.summary == "We chose mango." and [t.prompt for t in kept.turns] == ["p4"]
    # Main reopens the Window it left open, and the Rewind rebuilds its Chat.
    main.run(rewind_cmd(session, 2), settle=True)
    back = conversation(main, session)
    assert back.summary is None and back.summarized == 0
    assert [t.prompt for t in back.turns] == ["p0", "p1", "p2"]
    rebuilt = after_last_clear(main.windows(session.id)[-1].sent)
    assert users(rebuilt) == ["p0", "p1", "p2"]
    chat = [m for m in rebuilt if isinstance(m, ChatAppend)]
    assert not any(entry in chat for entry in compacted_entries(4, "We chose mango."))


def test_with_a_turn_active_it_is_refused_and_nothing_changes(main: MainHarness):
    session = main.create_session()
    main.hold_turns = True
    main.run(
        *talk(session, 1),
        ended(session),
        *talk(session, 1, first=1),
        rewind_cmd(session),
        ended(session),
        settle=True,
    )
    assert prompts(main, session) == ["p0", "p1"]
    [result] = results(main)
    assert result.startswith("error turn_active")
    assert rewound(main, session) == []


def test_with_its_window_closed_the_texts_come_back_when_it_opens(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(*answers(2))
    main.run(*talk(session, 2), rewind_cmd(session), settle=True)
    main.run(MainCommandEntered(f"/open {session.id}"), settle=True)
    [window] = main.windows(session.id)
    assert restored(window.sent) == ["p1"]
    assert users(window.sent) == ["p0"]


def test_a_new_turn_after_a_rewind_takes_the_number_of_the_undone_one(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(*answers(3))
    main.run(*talk(session, 2), rewind_cmd(session), *talk(session, 1, first=7), settle=True)
    assert prompts(main, session) == ["p0", "p7"]
    main.run(MainCommandEntered(f"/open {session.id}"), settle=True)
    assert users(main.windows(session.id)[0].sent) == ["p0", "p7"]


def test_the_window_empties_its_chat_when_main_asks(tmp_path: Path):
    shown: list[ScreenUpdate] = []
    listener = MainListener(shown.append, WindowRecord(tmp_path / "window.jsonl"))
    listener.on_message(ChatClear())
    assert shown == [ClearChat()]


def test_rewinding_into_pruned_turns_reads_fewer_of_them_pruned():
    turns = [Turn(prompt=f"p{n}", steps=[Step(output=[])]) for n in range(4)]
    conversation = Conversation(sysprompt="s", turns=turns, pruned=3)
    assert rewind(conversation, [], 2).conversation.pruned == 2
    assert rewind(conversation, [], 1).conversation.pruned == 3
