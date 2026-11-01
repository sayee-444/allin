import threading
from pathlib import Path

import pytest

from allin.core.errors import TurnCancelled
from allin.core.ids import BoxId
from allin.core.origin import Origin
from allin.ipc.messages import BoxHide, BoxShow, Coverage
from allin.main.events import (
    BoxAnswered,
    ConfirmationRequested,
    InputReceived,
    MainCommandEntered,
    TurnEnded,
    WindowClosed,
    WindowConnected,
)
from allin.main.executors import TurnJob
from allin.main.settings_files import MainSettingsFiles
from allin.main.turn import Cancellation
from allin.ui.marks import DEVASTATION_MARK
from allin.ui.screen import HideBox, ShowBox, TitleMark
from tests.test_dispatcher import SETTINGS, FakeWindowLink, MainHarness, server

FIRST_BOX = BoxId("b1")


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.settings = SETTINGS.model_copy(update={"parallel": 2})  # /reset puts it back
    return harness


def current_parallel(main: MainHarness) -> int | None:
    return MainSettingsFiles(main.settings_dir).load().current.parallel


def shown_boxes(main: MainHarness) -> list[ShowBox]:
    return [update for update in main.main_screen if isinstance(update, ShowBox)]


def coverage(window: FakeWindowLink) -> list[Coverage]:
    return [message for message in window.sent if isinstance(message, Coverage)]


def resumed(job: TurnJob) -> bool | None:
    """The answer Main resumed the held Turn with; ``None`` if it has not resumed it."""
    cancellation = Cancellation()
    timer = threading.Timer(0.2, cancellation.cancel, args=("test",))
    timer.start()
    job.confirmation.connect(job.session_id, cancellation, lambda _: None)
    try:
        return job.confirmation.ask("again", "")
    except TurnCancelled:
        return None
    finally:
        timer.cancel()


# Confirmation of a Command main


def test_a_delicate_command_asks_first_and_no_runs_nothing(main: MainHarness):
    chat = main.run(MainCommandEntered("/reset"), BoxAnswered(FIRST_BOX, False), settle=True)
    [box] = shown_boxes(main)
    assert box.box.kind == "confirm" and box.box.box_id == FIRST_BOX
    assert "/reset: waiting for confirmation" in chat and "/reset: rejected" in chat
    assert current_parallel(main) == 2
    assert HideBox(FIRST_BOX) in main.main_screen
    [rejected] = [entry for entry in main.main_log() if entry.fields.get("result") == "rejected"]
    assert rejected.fields["command"] == "/reset"


def test_a_delicate_command_runs_once_answered_yes(main: MainHarness):
    chat = main.run(MainCommandEntered("/reset"), BoxAnswered(FIRST_BOX, True), settle=True)
    assert "/reset: ok" in chat
    assert current_parallel(main) is None


def test_a_second_answer_to_the_same_box_does_nothing(main: MainHarness):
    main.settings = SETTINGS.model_copy(update={"parallel": 2})
    main.run(
        MainCommandEntered("/reset"),
        BoxAnswered(FIRST_BOX, False),
        BoxAnswered(FIRST_BOX, True),
        settle=True,
    )
    assert current_parallel(main) == 2


def test_y_skips_the_confirmation_for_that_invocation_only(main: MainHarness):
    main.run(MainCommandEntered("/reset -y"), MainCommandEntered("/set parallel=3"), settle=True)
    assert current_parallel(main) == 3 and shown_boxes(main) == []
    main.run(MainCommandEntered("/reset"))
    assert len(shown_boxes(main)) == 1


@pytest.mark.parametrize("origin", ["session", "main"])
def test_y_from_any_origin_but_a_human_is_an_error(main: MainHarness, origin: Origin):
    chat = main.run(MainCommandEntered("/reset -y", origin=origin))
    assert any("error human_only" in line for line in chat)
    assert current_parallel(main) == 2 and shown_boxes(main) == []


@pytest.mark.parametrize("command", ["/devastation on", "/set session.exempt=false"])
def test_only_a_human_lights_devastation_or_changes_the_exemption(main: MainHarness, command: str):
    chat = main.run(MainCommandEntered(command, origin="session"))
    assert any("error human_only" in line for line in chat)
    assert MainSettingsFiles(main.settings_dir).load().current.session.exempt


# A Turn waiting for its Confirmation


def test_a_turn_waiting_for_its_confirmation_holds_no_slot(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.hold_turns = True
    main.run(
        InputReceived(a.id, "hi"),
        ConfirmationRequested(a.id, "delete", "Delete it?"),
        InputReceived(b.id, "hi"),
        settle=True,
    )
    assert [job.session_id for job in main.held] == [a.id, b.id], "one slot, both started"


def test_an_answered_turn_goes_on_before_the_new_prompts_of_its_main(main: MainHarness):
    a, b, c = main.create_session(), main.create_session(), main.create_session()
    main.hold_turns = True
    main.run(
        InputReceived(a.id, "hi"),
        ConfirmationRequested(a.id, "delete", "Delete it?"),
        InputReceived(b.id, "hi"),
        InputReceived(c.id, "hi"),  # waits: B holds the only slot
        BoxAnswered(FIRST_BOX, True),  # A waits too, but ahead of C
        TurnEnded(b.id, steps=[]),  # B's slot goes to A, not to C
        settle=True,
    )
    assert [job.session_id for job in main.held] == [a.id, b.id], "C still waits"
    assert resumed(main.held[0]) is True


def test_a_rejected_turn_goes_on_with_the_rejection(main: MainHarness):
    a = main.create_session()
    main.hold_turns = True
    main.run(
        InputReceived(a.id, "hi"),
        ConfirmationRequested(a.id, "delete", "Delete it?"),
        BoxAnswered(FIRST_BOX, False),
        settle=True,
    )
    [job] = main.held
    assert resumed(job) is False


def test_no_setting_alone_answers_a_turns_confirmation(main: MainHarness):
    a = main.create_session(exempt=True)
    main.hold_turns = True
    main.run(InputReceived(a.id, "hi"), ConfirmationRequested(a.id, "t", "x"), settle=True)
    [job] = main.held
    assert len(shown_boxes(main)) == 1
    assert resumed(job) is None


def test_the_box_shows_in_the_sessions_window_and_an_answer_there_hides_it_everywhere(
    main: MainHarness,
):
    a = main.create_session()
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "hi"),
        ConfirmationRequested(a.id, "delete", "Delete it?"),
        BoxAnswered(FIRST_BOX, True),  # as the Window sends it
        settle=True,
    )
    [window] = main.windows(a.id)
    assert BoxShow(box_id=FIRST_BOX, kind="confirm", title="delete", text="Delete it?") in (
        window.sent
    )
    assert BoxHide(box_id=FIRST_BOX) in window.sent
    assert HideBox(FIRST_BOX) in main.main_screen
    [box] = shown_boxes(main)
    assert box.box.title == f"[{a.id}] delete", "Main's Chat says whose it is"


def test_a_box_that_came_while_the_window_was_closed_shows_when_it_opens(main: MainHarness):
    a = main.create_session()
    main.hold_turns = True
    main.run(
        InputReceived(a.id, "hi"),
        ConfirmationRequested(a.id, "delete", "Delete it?"),
        MainCommandEntered(f"/open {a.id}"),
        WindowConnected(a.id),  # as the Window says once it is up
        settle=True,
    )
    [window] = main.windows(a.id)
    assert BoxShow(box_id=FIRST_BOX, kind="confirm", title="delete", text="Delete it?") in (
        window.sent
    )


def test_cancelling_a_turn_that_waits_withdraws_its_box(main: MainHarness):
    a = main.create_session()
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "hi"),
        ConfirmationRequested(a.id, "delete", "Delete it?"),
        WindowClosed(a.id, returncode=0),
        settle=True,
    )
    assert HideBox(FIRST_BOX) in main.main_screen


# --devastation and the Exemption


def test_devastation_answers_the_confirmations_of_mains_chat_and_logs_them(main: MainHarness):
    main.devastation = True
    chat = main.run(MainCommandEntered("/reset"))
    assert current_parallel(main) is None
    assert any("confirmation answered by --devastation" in line for line in chat)
    assert any(entry.event == "confirmation_auto_answered" for entry in main.main_log())
    assert all(update.box.kind == "notice" for update in shown_boxes(main))


def test_devastation_answers_exempt_sessions_but_not_one_without_the_exemption(
    main: MainHarness,
):
    exempt, asking = main.create_session(), main.create_session(exempt=False)
    main.server = server(slots=2)
    main.hold_turns = True
    main.run(
        MainCommandEntered("/devastation on"),
        InputReceived(exempt.id, "hi"),
        InputReceived(asking.id, "hi"),
        ConfirmationRequested(exempt.id, "t", "x"),
        ConfirmationRequested(asking.id, "t", "x"),
        settle=True,
    )
    job_exempt, job_asking = main.held
    assert resumed(job_exempt) is True
    assert resumed(job_asking) is None
    assert any(e.event == "confirmation_auto_answered" for e in main.session_log(exempt))
    confirms = [update for update in shown_boxes(main) if update.box.kind == "confirm"]
    assert [box.box.title for box in confirms] == [f"[{asking.id}] t"]


def test_a_running_turn_keeps_what_it_had_when_it_started(main: MainHarness):
    a = main.create_session()
    main.hold_turns = True
    main.run(
        InputReceived(a.id, "hi"),
        MainCommandEntered("/devastation on"),
        ConfirmationRequested(a.id, "t", "x"),
        settle=True,
    )
    [job] = main.held
    assert resumed(job) is None, "it started without --devastation, so it still asks"


def test_taking_the_exemption_away_reaches_only_the_next_turn(main: MainHarness):
    a = main.create_session()
    main.devastation = True
    main.hold_turns = True
    main.run(
        InputReceived(a.id, "hi"),
        MainCommandEntered(f"/session set {a.id} exempt=false"),
        ConfirmationRequested(a.id, "t", "x"),
        settle=True,
    )
    [job] = main.held
    assert resumed(job) is True


def test_lighting_devastation_marks_the_borders_and_says_who_stops_asking(main: MainHarness):
    exempt, asking = main.create_session(), main.create_session(exempt=False)
    main.run(
        MainCommandEntered(f"/open {exempt.id}"),
        MainCommandEntered(f"/open {asking.id}"),
        MainCommandEntered("/devastation on"),
        settle=True,
    )
    assert TitleMark(DEVASTATION_MARK) in main.main_screen
    [window_exempt], [window_asking] = main.windows(exempt.id), main.windows(asking.id)
    assert coverage(window_exempt) == [Coverage(covered=True)]
    assert coverage(window_asking) == [Coverage(covered=False)]
    [notice] = shown_boxes(main)
    assert notice.box.kind == "notice" and exempt.id in notice.box.text
    assert asking.id not in notice.box.text


def test_devastation_off_clears_the_marks(main: MainHarness):
    a = main.create_session()
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered("/devastation on"),
        MainCommandEntered("/devastation off"),
        settle=True,
    )
    assert main.main_screen[-1] == TitleMark("")
    [window] = main.windows(a.id)
    assert coverage(window) == [Coverage(covered=True), Coverage(covered=False)]


def test_after_a_restart_everything_asks_again(main: MainHarness):
    main.run(MainCommandEntered("/devastation on"))
    main.main_screen.clear()
    main.run(MainCommandEntered("/reset"))
    assert [box.box.kind for box in shown_boxes(main)] == ["confirm"]


def test_devastation_does_not_answer_a_command_a_session_asked_for(main: MainHarness):
    main.devastation = True
    main.run(MainCommandEntered("/reset", origin="session"))
    assert [box.box.kind for box in shown_boxes(main)] == ["notice", "confirm"]
    assert current_parallel(main) == 2


def test_a_prompt_already_waiting_takes_the_slot_a_confirmation_frees(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.hold_turns = True
    main.run(
        InputReceived(a.id, "hi"),
        InputReceived(b.id, "hi"),  # waits: A holds the only slot
        ConfirmationRequested(a.id, "delete", "Delete it?"),
        settle=True,
    )
    assert [job.session_id for job in main.held] == [a.id, b.id]
