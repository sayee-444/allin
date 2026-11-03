"""Writing during a Turn (#56, ADR-0024): Enter queues, Up withdraws, Shift+Enter interrupts."""

from pathlib import Path

import pytest

from allin.core.errors import TurnCancelled, TurnCrashed
from allin.core.origin import HUMAN, from_session
from allin.ipc.messages import ChatAppend, InputRestore, TurnState
from allin.main.conversation import NOTIFICATIONS_HEADER, Step
from allin.main.cut_turns import INTERRUPTED_LINE
from allin.main.events import (
    DraftReceived,
    InputReceived,
    MainCommandEntered,
    QueuedWithdrawn,
    ToolsEnded,
    ToolsStarted,
    TurnEnded,
    TurnOutput,
    WindowClosed,
)
from allin.main.prompts import Prompt
from allin.main.state import MainState
from allin.main.turn_queue import CUT_CALL, INTERRUPTED
from tests.test_dispatcher import MainHarness, ended, restored


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.hold_turns = True
    return harness


def prompts_shown(main: MainHarness, session_id: str) -> list[str]:
    [window] = main.windows(session_id)
    return [m.text for m in window.sent if isinstance(m, ChatAppend) and m.role == "user"]


def last_queued(main: MainHarness, session_id: str) -> list[str]:
    [window] = main.windows(session_id)
    return [m for m in window.sent if isinstance(m, TurnState)][-1].queued


def prompt_of(job_input: list[dict[str, object]]) -> object:
    return job_input[-1]["content"]


def test_what_is_sent_during_a_turn_waits_as_queued_and_runs_next(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "a1"),
        InputReceived(session.id, "a2"),
        ended(session),
        settle=True,
    )
    [window] = main.windows(session.id)
    shown = [
        ("queued", m.queued) if isinstance(m, TurnState) else ("chat", m.text)
        for m in window.sent
        if (isinstance(m, ChatAppend) and m.role == "user") or isinstance(m, TurnState)
    ]
    # a2 waits below the Chat, and enters it only as its Turn starts.
    assert ("queued", ["a2"]) in shown
    assert shown.index(("queued", ["a2"])) < shown.index(("chat", "a2"))
    assert prompts_shown(main, session.id) == ["a1", "a2"]
    assert last_queued(main, session.id) == []
    assert [prompt_of(job.request.input) for job in main.held] == ["a1", "a2"]


def test_a_rebuilt_chat_shows_a_queued_prompt_where_its_turn_started(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "a1"),
        InputReceived(session.id, "a2"),
        TurnOutput(session.id, "answer", "uno"),
        ended(session),
        WindowClosed(session.id, returncode=0),
        MainCommandEntered(f"/open {session.id}"),
        settle=True,
    )
    reopened = main.windows(session.id)[-1]
    users = [m.text for m in reopened.sent if isinstance(m, ChatAppend) and m.role == "user"]
    assert users == ["a1", "a2"]


def test_up_withdraws_the_last_queued_prompt_as_it_was_typed(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "a1"),
        InputReceived(session.id, "a2"),
        InputReceived(session.id, "a3 @top_k=5"),
        QueuedWithdrawn(session.id),
        ended(session),
        ended(session),
        settle=True,
    )
    [window] = main.windows(session.id)
    assert [m.text for m in window.sent if isinstance(m, InputRestore)] == ["a3 @top_k=5"]
    queued = [m.queued for m in window.sent if isinstance(m, TurnState)]
    both = len(queued) - 1 - queued[::-1].index(["a2", "a3"])
    assert queued[both + 1] == ["a2"]  # withdrawn: a3 no longer waits
    [withdrawn] = [e for e in main.session_log(session) if e.event == "prompt_withdrawn"]
    assert withdrawn.fields["text"] == "a3"
    assert [prompt_of(job.request.input) for job in main.held] == ["a1", "a2"]
    assert prompts_shown(main, session.id) == ["a1", "a2"]


def test_withdrawing_with_nothing_queued_does_nothing(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "a1"),
        QueuedWithdrawn(session.id),
        settle=True,
    )
    [window] = main.windows(session.id)
    assert not any(isinstance(m, InputRestore) for m in window.sent)
    assert len(main.held) == 1 and main.held[0].cancellation.reason != INTERRUPTED


def test_shift_enter_keeps_the_turn_cut_and_runs_its_text_before_the_queue(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "cuenta hasta cien"),
        InputReceived(session.id, "luego esto"),
        TurnOutput(session.id, "reasoning", "voy"),
        TurnOutput(session.id, "answer", "1, 2, 3"),
        InputReceived(session.id, "para, solo hasta tres", interrupt=True),
        TurnEnded(session.id, error=TurnCancelled(reason=INTERRUPTED)),  # as its executor ends it
        ended(session),
        settle=True,
    )
    assert main.held[0].cancellation.reason == INTERRUPTED
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    cut, corrected = conversation.turns
    assert cut.cut == "interrupted"
    assert [item["type"] for item in cut.steps[0].output] == ["reasoning", "message"]
    assert cut.steps[0].output[1]["content"][0]["text"] == "1, 2, 3"
    [window] = main.windows(session.id)
    assert any(isinstance(m, ChatAppend) and m.label == "cut" for m in window.sent)
    # Its text runs next, ahead of what waited, and the LLM is told it was interrupted.
    _, second, third = (job.request.input for job in main.held)
    assert second[-2]["role"] == "assistant"
    note = f"{NOTIFICATIONS_HEADER}\n{INTERRUPTED_LINE}"
    assert prompt_of(second) == f"{note}\n\npara, solo hasta tres"
    assert prompt_of(third) == "luego esto"  # told once
    assert corrected.prompt == "para, solo hasta tres"
    assert corrected.notifications == [INTERRUPTED_LINE]
    assert prompts_shown(main, session.id) == [
        "cuenta hasta cien",
        "para, solo hasta tres",
        "luego esto",
    ]


def test_shift_enter_with_an_empty_input_only_interrupts(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "a1"),
        InputReceived(session.id, "a2"),
        InputReceived(session.id, "", interrupt=True),
        TurnEnded(session.id, error=TurnCancelled(reason=INTERRUPTED)),
        settle=True,
    )
    assert (
        prompt_of(main.held[1].request.input) == f"{NOTIFICATIONS_HEADER}\n{INTERRUPTED_LINE}\n\na2"
    )


def test_a_turn_that_ended_before_the_interrupt_is_saved_whole(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "a1"),
        InputReceived(session.id, "", interrupt=True),
        TurnEnded(session.id, steps=[Step(output=[])]),  # it had finished already
        settle=True,
    )
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    assert conversation.turns[0].cut is None


def test_closing_the_window_gives_the_queued_prompts_back_as_draft_blocks(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "a1"),
        InputReceived(session.id, "a2"),
        InputReceived(session.id, "a3 @top_k=5"),
        WindowClosed(session.id, returncode=0),
        TurnEnded(session.id, error=TurnCancelled(reason="window closed")),
        MainCommandEntered("/exit"),
        settle=True,
    )
    # The cancelled Turn's Prompt comes back too, ahead of what waited behind it (#156).
    assert main.state.load().drafts == {session.id: ["a1", "a2", "a3 @top_k=5"]}


def test_a_cancelled_human_prompt_comes_back_as_typed_ahead_also_after_a_restart(
    main: MainHarness,
):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "a1 @top_k=5"),
        InputReceived(session.id, "a2"),
        DraftReceived(session.id, ("typing", "a1 @top_k=5")),  # never twice
        WindowClosed(session.id, returncode=0),
        TurnEnded(session.id, error=TurnCancelled(reason="window closed")),
        MainCommandEntered("/exit"),
        settle=True,
    )
    main.run(MainCommandEntered(f"/open {session.id}"), MainCommandEntered("/exit"), settle=True)
    assert restored(main.windows(session.id)[-1]) == ["a1 @top_k=5", "a2", "typing"]


def test_closing_from_main_chat_gives_back_only_a_human_prompt(main: MainHarness):
    session, sender = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/send {session.id} trabajo", origin=from_session(HUMAN, sender.id)),
        DraftReceived(session.id, ("typing",)),
        MainCommandEntered(f"/close {session.id}"),  # cancels any Turn of the Session
        TurnEnded(session.id, error=TurnCancelled(reason="window closed")),
        MainCommandEntered("/exit"),
        settle=True,
    )
    assert main.held[0].cancellation.reason == "window closed"
    assert main.state.load().drafts == {session.id: ["typing"]}


def test_main_stopping_leaves_a_running_human_prompt_to_its_next_start_once(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "a1 @top_k=5"),
        DraftReceived(session.id, ("typing",)),
        MainCommandEntered("/exit"),
        settle=True,
    )
    main.run(MainCommandEntered(f"/open {session.id}"), MainCommandEntered("/exit"), settle=True)
    assert restored(main.windows(session.id)[-1]) == ["a1 @top_k=5", "typing"]


def test_a_restart_gives_back_only_the_running_prompts_of_a_human(main: MainHarness):
    session, sender = main.create_session(), main.create_session()
    main.state.save(
        MainState(
            running=[Prompt(session.id, "trabajo", origin=from_session(HUMAN, sender.id))],
            drafts={session.id: ["typing"]},
        )
    )
    main.run(MainCommandEntered(f"/open {session.id}"), MainCommandEntered("/exit"), settle=True)
    assert restored(main.windows(session.id)[-1]) == ["typing"]


# While its tools run (#121): what enters the Turn, and what a cut or a closed Window does
# with it. The Turn's executor is held, so the events of its thread are given by hand.

CALLED = Step(
    output=[{"type": "function_call", "call_id": "call_1", "name": "nap", "arguments": "{}"}]
)
RETURNED = CALLED.model_copy(
    update={"results": [{"type": "function_call_output", "call_id": "call_1", "output": '"ok"'}]}
)


def test_shift_enter_after_a_text_entered_keeps_it_and_the_real_outputs(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "duerme"),
        ToolsStarted(session.id, ("nap",), CALLED),
        InputReceived(session.id, "y luego esto"),
        ToolsEnded(session.id, RETURNED, 10),  # it holds a slot at once: the text enters
        InputReceived(session.id, "", interrupt=True),  # before its executor said the Step ended
        TurnEnded(session.id, error=TurnCancelled(reason=INTERRUPTED)),
        settle=True,
    )
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    [step] = conversation.turns[0].steps
    assert step.results == RETURNED.results  # what it gave back, not that it was cut
    assert step.interjected == ["y luego esto"]


def test_shift_enter_while_its_tools_run_marks_each_call_cut(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "duerme"),
        ToolsStarted(session.id, ("nap",), CALLED),
        InputReceived(session.id, "", interrupt=True),
        TurnEnded(session.id, error=TurnCancelled(reason=INTERRUPTED)),
        settle=True,
    )
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    [step] = conversation.turns[0].steps
    assert [result["output"] for result in step.results] == [CUT_CALL]


def test_closing_the_window_gives_back_the_texts_that_entered_its_turn(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "duerme"),
        ToolsStarted(session.id, ("nap",), CALLED),
        InputReceived(session.id, "y luego esto"),
        ToolsEnded(session.id, RETURNED, 10),
        InputReceived(session.id, "y aquello"),  # still queued
        WindowClosed(session.id, returncode=0),
        TurnEnded(session.id, error=TurnCancelled(reason="window closed")),
        MainCommandEntered("/exit"),
        settle=True,
    )
    assert main.state.load().drafts == {session.id: ["duerme", "y luego esto", "y aquello"]}
    cancelled = [e.fields for e in main.session_log(session) if e.event == "turn_cancelled"]
    assert cancelled[0]["joined"] == ["y luego esto"]


def test_a_rebuilt_chat_shows_the_texts_that_entered_a_turn_that_failed(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "duerme"),
        ToolsStarted(session.id, ("nap",), CALLED),
        InputReceived(session.id, "y luego esto"),
        ToolsEnded(session.id, RETURNED, 10),
        TurnEnded(session.id, error=TurnCrashed(reason="boom")),
        WindowClosed(session.id, returncode=0),
        MainCommandEntered(f"/open {session.id}"),
        settle=True,
    )
    reopened = main.windows(session.id)[-1]
    users = [m.text for m in reopened.sent if isinstance(m, ChatAppend) and m.role == "user"]
    assert users == ["duerme", "y luego esto"]


def test_text_sent_before_its_sessions_turn_started_waits_for_its_own(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(
        InputReceived(b.id, "b"),  # B holds the only slot
        InputReceived(a.id, "a1"),  # waits for a slot, no Turn yet
        InputReceived(a.id, "a2"),  # queued behind a1, before a1's Turn started
        ended(b),
        ToolsStarted(a.id, ("nap",), CALLED),
        ToolsEnded(a.id, RETURNED, 10),
        settle=True,
    )
    assert [e for e in main.session_log(a) if e.event == "prompt_joined"] == []
