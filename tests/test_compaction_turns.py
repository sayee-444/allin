"""Compacting a Session's Conversation, by hand or on its own between Turns (#46, ADR-0017),
through Main's dispatcher with a fake Server that counts one token per word."""

import os
from pathlib import Path

import pytest
from openai.types.responses import ResponseStreamEvent
from pydantic import BaseModel

from allin.core.errors import TurnCancelled
from allin.core.ids import SessionId
from allin.core.origin import Origin
from allin.core.settings import CompactSettings
from allin.core.wait_list import Reservation, WaitList
from allin.ipc.messages import BoxShow, ChatAppend, InputRestore, TurnState
from allin.main.chat_rebuild import compacted_entries
from allin.main.compaction import CLOSING, DEFAULT_SYSPROMPT
from allin.main.conversation import Conversation, Item, summary_item, turn_items
from allin.main.events import (
    InputReceived,
    MainCommandEntered,
    ServerChanged,
    ServerReady,
    Tick,
    TurnEnded,
    WindowClosed,
)
from allin.main.measure import chat_messages
from allin.main.prompts import Prompt
from allin.main.sessions import Session
from allin.main.slots import ServerProps
from allin.main.state import MainState
from tests.llm_fakes import DroppedStream, FakeLlm, Script, answer, completed, failed
from tests.pieces import approve_sysprompt
from tests.test_dispatcher import KEY, MainHarness, ended, server, server_ready
from tests.test_pool_admission import OTHER, waiting

WORDS = 99  # each answer; with its one-word Prompt, a Turn of 100 tokens


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.servers.tokens_of = words
    harness.server = server(context=1_000)  # an Output cap of 250
    return harness


def words(items: list[Item]) -> int:
    """What the fake Server counts in ``items``: one token per word."""
    return sum(
        len(str(message["content"]).split())
        + len(str(message.get("reasoning_content", "")).split())
        for message in chat_messages(items)
    )


def reply(text: str) -> list[ResponseStreamEvent]:
    return [answer(text), completed("", text, tokens=(100, len(text.split())))]


def answers(count: int) -> list[Script]:
    return [reply(" ".join(["w"] * WORDS)) for _ in range(count)]


def talk(session: Session, count: int, first: int = 0) -> list[InputReceived]:
    return [InputReceived(session.id, f"p{n}") for n in range(first, first + count)]


def conversation(main: MainHarness, session: Session) -> Conversation:
    loaded = main.sessions.load_conversation(session)
    assert loaded is not None
    return loaded


def sent(main: MainHarness, session: Session) -> list[BaseModel]:
    [window] = main.windows(session.id)
    return window.sent


def events(main: MainHarness, session: Session) -> list[str]:
    return [entry.event for entry in main.session_log(session)]


def compact(session: Session, instructions: str = "") -> MainCommandEntered:
    return MainCommandEntered(f"/session compact {session.id} {instructions}".rstrip())


def sysprompt(main: MainHarness) -> str:
    return DEFAULT_SYSPROMPT.replace("$username", "ana").replace("$assistant_name", "AllIn")


def test_compacting_by_hand_summarizes_the_old_turns_and_keeps_the_recent_ones_literal(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply("We chose mango."))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *talk(session, 6),
        compact(session, "keep the API decisions"),
        settle=True,
    )
    before = main.sessions.load_archived(session)
    after = conversation(main, session)
    # compact.keep is 250 tokens of the 1 000: the last two Turns, whole, stay literal.
    assert after.summary == "We chose mango." and after.summarized == 4
    assert after.sysprompt == "I am AllIn; you are ana."
    assert [turn.prompt for turn in after.turns] == ["p4", "p5"]
    assert after.request_input("next")[1] == {
        "role": "user",
        "content": "[Conversation summary · Prompts 1\N{EN DASH}4]\n\nWe chose mango.",
    }
    summary_step = main.llm.requests[6]["input"]
    assert summary_step[0] == {"role": "developer", "content": sysprompt(main)}
    assert [item["content"] for item in summary_step if item.get("role") == "user"] == [
        "p0",
        "p1",
        "p2",
        "p3",
        f"{CLOSING}\n\nWhat the human asks of this summary: keep the API decisions",
    ]
    # Nothing is lost: what it replaced is archived whole.
    [archived] = before
    assert [turn.prompt for turn in archived.turns] == [f"p{n}" for n in range(6)]


def test_the_window_keeps_showing_every_turn_with_the_cut_line_and_the_summary(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply("We chose mango."))
    main.run(
        MainCommandEntered(f"/open {session.id}"), *talk(session, 6), compact(session), settle=True
    )
    live = [m for m in sent(main, session) if isinstance(m, ChatAppend)]
    cut = live.index(compacted_entries(4, "We chose mango.")[0])
    assert live[cut : cut + 2] == compacted_entries(4, "We chose mango.")
    border = [m.waiting for m in sent(main, session) if isinstance(m, TurnState)]
    assert "compacting…" in border
    main.run(MainCommandEntered(f"/open {session.id}"), settle=True)  # rebuilt from disk
    reopened = main.windows(session.id)[1].sent
    users = [m.text for m in reopened if isinstance(m, ChatAppend) and m.role == "user"]
    assert users == [f"p{n}" for n in range(6)]  # the summarized Turns too
    rebuilt = [m for m in reopened if isinstance(m, ChatAppend)]
    assert rebuilt[-2:] == compacted_entries(4, "We chose mango.")


def test_a_prompt_that_would_pass_the_threshold_compacts_first_and_its_turn_goes_on(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply("We chose mango."), reply("done"))
    # p6 needs 6 + 600 + 1 + 250 = 857 tokens: past 0.85 of 1 000.
    main.run(*talk(session, 7), settle=True)
    summary_step, turn = main.llm.requests[6]["input"], main.llm.requests[7]["input"]
    assert summary_step[0]["content"] == sysprompt(main)
    assert turn == [
        {"role": "developer", "content": "I am AllIn; you are ana."},
        summary_item("We chose mango.", 4),
        *turn_items(conversation(main, session).turns[0]),
        *turn_items(conversation(main, session).turns[1]),
        {"role": "user", "content": "p6"},
    ]
    assert [t.prompt for t in conversation(main, session).turns] == ["p4", "p5", "p6"]
    assert events(main, session).count("turn_finished") == 7


def test_with_compact_auto_off_a_prompt_that_does_not_fit_fails_as_before(main: MainHarness):
    session = main.create_session(compact=CompactSettings(auto=False))
    main.llm = FakeLlm(*answers(8))
    main.run(MainCommandEntered(f"/open {session.id}"), *talk(session, 9), settle=True)
    assert "compaction_started" not in events(main, session)
    assert InputRestore(text="p8") in sent(main, session)
    errors = [
        m.text for m in sent(main, session) if isinstance(m, ChatAppend) and m.role == "error"
    ]
    assert errors == ["prompt_too_big: need=1057, limit=1000"]
    hint = ChatAppend(
        role="info", label="context", text=f"/session compact {session.id} may make room for it"
    )
    assert hint in sent(main, session)
    assert conversation(main, session).summary is None


def test_a_pool_full_of_other_sessions_never_sets_off_a_compaction(main: MainHarness):
    main.server = server(slots=2, context=1_000)
    session = main.create_session()
    main.llm = FakeLlm(*answers(3))

    def other_main_takes(tokens: int) -> None:
        reservation = Reservation(
            main=OTHER, pid=os.getpid(), session=SessionId("x"), tokens=tokens
        )
        WaitList(main.slot_locks_dir, KEY).reserve(reservation)

    main.run(
        MainCommandEntered(f"/open {session.id}"),
        *talk(session, 2),
        lambda: other_main_takes(800),  # p2 needs 457: it fits in the slot, not in the pool
        *talk(session, 1, first=2),
        settle=True,
    )
    assert waiting(main, session)[-1] == "waiting for room in the pool"
    assert "compaction_started" not in events(main, session)
    assert len(main.llm.requests) == 2


def test_a_summary_that_does_not_save_half_leaves_the_conversation_as_it_was(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply(" ".join(["long"] * 210)))
    main.run(
        MainCommandEntered(f"/open {session.id}"), *talk(session, 6), compact(session), settle=True
    )
    assert conversation(main, session).summary is None
    assert len(conversation(main, session).turns) == 6
    assert main.sessions.load_archived(session) == []
    [entry] = [e for e in main.session_log(session) if e.event == "compaction_failed"]
    assert "does not save half" in entry.fields["error"]
    assert "summary=215, replaced=400" in entry.fields["error"]


@pytest.mark.parametrize(
    "summary",
    [reply(""), [failed("the request timed out")]],
    ids=["empty", "failed"],
)
def test_an_empty_or_failed_summary_changes_nothing(
    main: MainHarness, summary: list[ResponseStreamEvent]
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), summary)
    main.run(*talk(session, 6), compact(session), settle=True)
    assert len(conversation(main, session).turns) == 6
    assert "compaction_failed" in events(main, session)


def test_a_server_that_falls_mid_compaction_makes_it_repeat_once_back(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), DroppedStream([answer("We ch")]), reply("We chose mango."))
    main.run(*talk(session, 6), compact(session), server_ready(main.server), settle=True)
    assert main.llm.requests[6]["input"] == main.llm.requests[7]["input"]  # the same Step
    assert conversation(main, session).summary == "We chose mango."
    assert "turn_suspended" in events(main, session)


def test_an_automatic_compaction_that_fails_lets_a_turn_that_still_fits_go_on_with_a_notice(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply(" ".join(["long"] * 210)), reply("done"))
    main.run(MainCommandEntered(f"/open {session.id}"), *talk(session, 7), settle=True)
    titles = [m.title for m in sent(main, session) if isinstance(m, BoxShow)]
    assert "Compaction failed" in titles
    assert len(conversation(main, session).turns) == 7  # p6 ran on the whole Conversation
    assert len(main.llm.requests) == 8  # and was not compacted for again


def test_an_automatic_compaction_that_cannot_start_lets_a_turn_that_still_fits_go_on(
    main: MainHarness,
):
    (main.sysprompts / "compact.txt").write_text("Summarize, never approved.")  # in quarantine
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply("done"))
    main.run(MainCommandEntered(f"/open {session.id}"), *talk(session, 7), settle=True)
    assert "prompt_not_started" not in events(main, session), "p6 left with its Compaction"
    assert len(conversation(main, session).turns) == 7
    titles = [m.title for m in sent(main, session) if isinstance(m, BoxShow)]
    assert "Compaction failed" in titles


def test_a_compaction_a_fall_suspended_that_then_fails_takes_its_falls_with_it(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(
        *answers(6),
        DroppedStream([answer("We ch")]),  # its summary Step falls: suspended, one fall
        DroppedStream([answer("hal")]),  # the next summary Step's first fall: its own first
        reply("We chose mango."),
        reply("done"),
    )

    def unmeasurable() -> None:  # back, but it cannot measure: the Compaction fails
        main.servers.tokens_of = lambda items: None

    def measurable() -> None:
        main.servers.tokens_of = words

    main.run(
        *talk(session, 6),
        compact(session),
        unmeasurable,
        server_ready(main.server),
        measurable,
        *talk(session, 1, first=6),  # it compacts first, and that Step falls once
        server_ready(main.server),
        settle=True,
    )
    failed = [
        e.fields["error"] for e in main.session_log(session) if e.event == "compaction_failed"
    ]
    assert len(failed) == 1 and "could not measure" in str(failed[0])
    assert events(main, session).count("turn_suspended") == 2
    assert len(conversation(main, session).turns) == 3  # compacted, and p6 saved


def test_a_conversation_bigger_than_a_lowered_slot_is_compacted_in_parts_and_its_turn_goes_on(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply("s1"), reply("s2"), reply("s3"), reply("done"))
    lowered = ServerProps(total_slots=1, slot_context=500, model_path="m.gguf")
    main.run(
        *talk(session, 6),
        ServerChanged(KEY, ServerReady(lowered)),  # -c went from 1 000 down to 500
        *talk(session, 1, first=6),
        settle=True,
    )
    steps = [request["input"] for request in main.llm.requests[6:9]]
    prompts = [
        [item["content"] for item in step[1:-1] if item.get("role") == "user"] for step in steps
    ]
    # Each part fits in 500 with what the Step may generate (125) and the rest of its request.
    assert prompts == [
        ["p0", "p1"],
        ["[Conversation summary · Prompts 1\N{EN DASH}2]\n\ns1", "p2", "p3"],
        ["[Conversation summary · Prompts 1\N{EN DASH}4]\n\ns2", "p4"],
    ]
    after = conversation(main, session)
    assert (after.summary, after.summarized) == ("s3", 5)
    assert [turn.prompt for turn in after.turns] == ["p5", "p6"]


def test_a_profile_compaction_sysprompt_replaces_the_built_in_one(main: MainHarness):
    (main.sysprompts / "compact.txt").write_text("Summarize for $username, briefly.")
    approve_sysprompt(main.sysprompts, "compact.txt")
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply("We chose mango."))
    main.run(*talk(session, 6), compact(session), settle=True)
    assert main.llm.requests[6]["input"][0]["content"] == "Summarize for ana, briefly."


def test_without_a_file_of_its_own_a_profile_uses_the_built_in_compaction_sysprompt(
    main: MainHarness,
):
    assert not (main.sysprompts / "compact.txt").exists()  # a Profile made before #46
    session = main.create_session()
    main.llm = FakeLlm(*answers(6), reply("We chose mango."))
    main.run(*talk(session, 6), compact(session), settle=True)
    assert main.llm.requests[6]["input"][0]["content"] == sysprompt(main)
    assert conversation(main, session).summary == "We chose mango."


def test_compacting_a_short_conversation_says_there_is_nothing_to_compact(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(*answers(2))
    main.run(*talk(session, 2), compact(session), settle=True)
    [entry] = [e for e in main.session_log(session) if e.event == "compaction_failed"]
    assert "nothing to compact" in entry.fields["error"]
    assert len(main.llm.requests) == 2


def test_with_compact_auto_off_the_context_notice_suggests_compacting(main: MainHarness):
    session = main.create_session(compact=CompactSettings(auto=False))
    crossing: list[ResponseStreamEvent] = [answer("w"), completed("", "w", tokens=(850, 1))]
    main.llm = FakeLlm(*answers(7), crossing)
    main.run(MainCommandEntered(f"/open {session.id}"), *talk(session, 8), settle=True)
    [notice] = [
        m.text for m in sent(main, session) if isinstance(m, BoxShow) and m.title == "Context"
    ]
    assert notice.endswith(f"; /session compact {session.id} makes room")


def test_closing_the_window_mid_compaction_stops_it_and_changes_nothing(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6))
    main.run(*talk(session, 6), settle=True)
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        compact(session),
        WindowClosed(session.id, 0),
        TurnEnded(session.id, error=TurnCancelled(reason="window closed")),
        settle=True,
    )
    [job] = main.held
    assert job.cancellation.reason == "window closed"
    assert len(conversation(main, session).turns) == 6
    assert main.sessions.load_archived(session) == []
    assert "compaction_failed" in events(main, session)


def test_a_compaction_running_when_main_stops_is_queued_again_never_given_back_as_a_draft(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6))
    main.run(*talk(session, 6), settle=True)
    main.hold_turns = True
    main.run(compact(session, "be brief"), settle=True)
    [job] = main.held  # its summary Step was running
    assert job.cancellation.reason is not None
    last = main.state.saved[-1]
    assert last.queue == [Prompt(session.id, "be brief", "compact")]
    assert last.running == []


def test_the_border_says_compacting_while_its_summary_step_runs(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(*answers(6))
    main.run(MainCommandEntered(f"/open {session.id}"), *talk(session, 6), settle=True)
    main.hold_turns = True
    main.run(compact(session), settle=True)
    assert "compaction_step_started" in events(main, session)
    window = main.windows(session.id)[-1]  # the second run is Main started again
    states = [m for m in window.sent if isinstance(m, TurnState)]
    assert states[-1] == TurnState(active=True, waiting="compacting…")


def test_a_compaction_wakes_who_subscribed_to_it(main: MainHarness):
    session, watcher = main.create_session(), main.create_session()
    main.llm = FakeLlm(*answers(6), reply("We chose mango."), reply("visto"))
    main.run(
        MainCommandEntered(f"/subscribe {watcher.id} compact:{session.id}"),
        *talk(session, 6),
        compact(session, ""),
        Tick(),
        settle=True,
    )
    [wake] = [e.fields for e in main.session_log(watcher) if e.event == "prompt"]
    ids = [str(e.fields["prompt_id"]) for e in main.session_log(session) if e.event == "prompt"]
    shown = f"Session {session.id} compacted: Prompts 1\N{EN DASH}4 summarized"
    assert f"{shown} (ids {', '.join(ids[:4])})" in str(wake["text"])


def test_the_compaction_before_a_prompt_of_an_fprocs_initiative_waits_with_it_behind_a_humans(
    main: MainHarness,
):
    session, first, human = main.create_session(), main.create_session(), main.create_session()
    main.llm = FakeLlm(*answers(6))
    main.run(*talk(session, 6), settle=True)
    by_itself = Prompt(
        session.id,
        "[from attached process relay]\nhola",
        origin=Origin.model_validate(["human", "process:relay"]),
        initiative=True,
    )
    # Restarted with it queued behind a human's: its Compaction goes first (ADR-0017).
    main.state.save(MainState(queue=[Prompt(first.id, "f1"), by_itself]))
    main.hold_turns = True
    main.run(
        lambda: setattr(main, "now", 1.0),
        InputReceived(human.id, "h1"),
        ended(first),
        ended(human),
        settle=True,
    )
    assert [job.session_id for job in main.held] == [first.id, human.id, session.id]
    assert "compaction_step_started" in events(main, session)
