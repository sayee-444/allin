"""Notificaciones and Conversation logs (#109, ADR-0013): what Main leaves pending opens the
next Prompt, under ``[LOGS @COMMANDS]`` in its ``user`` message; two that undo each other
before any Turn told them cancel out; and Conversation logs keep a source out of the
Conversation, never out of the Log or the Chat."""

from pathlib import Path

import pytest

from allin.core.errors import LlmUnavailable, TurnCancelled
from allin.ipc.messages import ChatAppend
from allin.main.conversation import NOTIFICATIONS_HEADER, Item
from allin.main.cut_turns import INTERRUPTED_LINE
from allin.main.events import InputReceived, MainCommandEntered, TurnEnded
from allin.main.sessions import Session
from allin.main.turn_queue import INTERRUPTED
from tests.test_dispatcher import MainHarness, ended, server, server_ready

GIVEN = "@send was given to you: your tools may run it"
TAKEN = "@send was taken away: your tools may no longer run it"


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.hold_turns = True
    return harness


def prompt_item(main: MainHarness, turn: int) -> Item:
    """The ``user`` message the held Turn ``turn`` sent its Prompt in."""
    return main.held[turn].request.input[-1]


def opened_with(*lines: str) -> str:
    return "\n".join([NOTIFICATIONS_HEADER, *lines]) + "\n\n"


def logs_shown(main: MainHarness, session: Session) -> list[str]:
    [window] = main.windows(session.id)
    return [m.text for m in window.sent if isinstance(m, ChatAppend) and m.label == "logs"]


def logged(main: MainHarness, session: Session, event: str) -> list[dict[str, object]]:
    return [entry.fields for entry in main.session_log(session) if entry.event == event]


def test_a_pending_one_opens_the_next_prompt_as_user_and_only_that_one(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/func add {session.id} @send"),
        InputReceived(session.id, "hola"),
        ended(session),
        InputReceived(session.id, "otra"),
        ended(session),
        settle=True,
    )
    first = prompt_item(main, 0)
    assert first["role"] == "user"
    assert first["content"] == opened_with(GIVEN) + "hola"
    assert prompt_item(main, 1)["content"] == "otra", "told once"
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None and conversation.turns[0].notifications == [GIVEN]
    assert main.sessions.load(session.id).notifications == ()


def test_giving_and_taking_away_the_same_before_the_prompt_leaves_nothing(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/func add {session.id} @send"),
        MainCommandEntered(f"/func remove {session.id} @send"),
        InputReceived(session.id, "hola"),
        settle=True,
    )
    assert prompt_item(main, 0)["content"] == "hola"
    assert logs_shown(main, session) == [GIVEN, TAKEN], "the Chat still shows both"
    [cancelled] = logged(main, session, "notifications_cancelled")
    assert cancelled["texts"] == [GIVEN, TAKEN]


def test_one_a_running_turn_told_does_not_cancel_out(main: MainHarness):
    # The LLM already read that it got @send: losing it is news for its next Prompt.
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/func add {session.id} @send"),
        InputReceived(session.id, "uno"),
        MainCommandEntered(f"/func remove {session.id} @send"),
        ended(session),
        InputReceived(session.id, "dos"),
        settle=True,
    )
    assert prompt_item(main, 0)["content"] == opened_with(GIVEN) + "uno"
    assert prompt_item(main, 1)["content"] == opened_with(TAKEN) + "dos"


def test_a_turn_that_fails_leaves_them_for_the_next(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/func add {session.id} @send"),
        InputReceived(session.id, "uno"),
        TurnEnded(session.id, error=LlmUnavailable(reason="test")),
        settle=True,
    )
    assert main.sessions.load(session.id).notifications != ()


def test_conversation_logs_off_keeps_them_out_of_the_conversation_only(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "@conversation_logs off /func"),
        MainCommandEntered(f"/func add {session.id} @send"),
        InputReceived(session.id, "hola"),
        settle=True,
    )
    assert prompt_item(main, 0)["content"] == "hola"
    assert logs_shown(main, session) == [GIVEN], "the Chat shows it"
    [note] = logged(main, session, "notification")
    assert note["text"] == GIVEN and note["conversation"] is False, "and the Log keeps it"


def test_off_for_all_and_on_for_one(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/conversation_logs {session.id} off"),
        MainCommandEntered(f"/conversation_logs {session.id} on /func"),
        MainCommandEntered(f"/func add {session.id} @send"),
        InputReceived(session.id, "hola"),
        settle=True,
    )
    assert prompt_item(main, 0)["content"] == opened_with(GIVEN) + "hola"
    chat = main.run(MainCommandEntered(f"/conversation_logs {session.id} list"))
    assert f"[{session.id}] /func: on" in chat
    assert f"[{session.id}] interrupted: off" in chat


def test_a_bad_action_says_how_it_is_used(main: MainHarness):
    session = main.create_session()
    chat = main.run(MainCommandEntered(f"/conversation_logs {session.id} maybe"))
    assert any("invalid_arguments" in line for line in chat)
    main.run(
        MainCommandEntered(f"/open {session.id}"), InputReceived(session.id, "@conversation_logs")
    )
    [window] = main.windows(session.id)
    errors = [m.text for m in window.sent if isinstance(m, ChatAppend) and m.role == "error"]
    assert any("invalid_session_command" in error for error in errors)


def test_the_rebuilt_chat_shows_them(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/func add {session.id} @send"),
        MainCommandEntered(f"/open {session.id}"),
        settle=True,
    )
    assert logs_shown(main, session) == [GIVEN]


def test_off_also_drops_those_already_pending(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/func add {session.id} @send"),
        MainCommandEntered(f"/conversation_logs {session.id} off /func"),
        InputReceived(session.id, "hola"),
        settle=True,
    )
    assert prompt_item(main, 0)["content"] == "hola"


def test_they_wait_across_a_restart_of_main(main: MainHarness):
    session = main.create_session()
    main.run(MainCommandEntered(f"/func add {session.id} @send"), settle=True)
    main.run(InputReceived(session.id, "hola"), settle=True)  # another Main, same data
    assert prompt_item(main, 0)["content"] == opened_with(GIVEN) + "hola"


def test_a_suspended_turn_goes_on_with_what_it_told_and_it_never_cancels_out(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/func add {session.id} @send"),
        InputReceived(session.id, "uno"),
        TurnEnded(session.id, error=LlmUnavailable(reason="fell")),  # a fall suspends it
        MainCommandEntered(f"/func remove {session.id} @send"),
        server_ready(server()),
        ended(session),
        InputReceived(session.id, "dos"),
        settle=True,
    )
    first, resumed, second = (job.request.input[-1]["content"] for job in main.held)
    assert first == resumed == opened_with(GIVEN) + "uno"
    assert second == opened_with(TAKEN) + "dos"


def test_an_interrupted_turn_is_saved_with_them(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/func add {session.id} @send"),
        InputReceived(session.id, "uno"),
        InputReceived(session.id, "dos", interrupt=True),
        TurnEnded(session.id, error=TurnCancelled(reason=INTERRUPTED)),
        settle=True,
    )
    assert prompt_item(main, 1)["content"] == opened_with(INTERRUPTED_LINE) + "dos"
    assert main.sessions.load(session.id).notifications == ()
