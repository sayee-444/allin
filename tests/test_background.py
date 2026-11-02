"""Segundo plano and the Máximo de Ventanas (#52, ADR-0020)."""

from pathlib import Path

import pytest

from allin.core.ids import SessionId
from allin.core.settings import MainSettings, SessionSettings, WindowsSettings
from allin.core.themes import Theme
from allin.ipc.messages import ChatAppend, ChatDelta, Close, InputRestore
from allin.main.conversation import Step
from allin.main.events import (
    ConfirmationRequested,
    DraftReceived,
    InputReceived,
    MainCommandEntered,
    TurnEnded,
    TurnOutput,
    WindowClosed,
    WindowUsed,
)
from allin.main.links import LinkResolver
from allin.main.log import Log
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.state import MainStateStore
from allin.main.window_room import displaced, fits
from allin.window.__main__ import ClickReporter
from tests.llm_fakes import FakeLlm, completed
from tests.test_dispatcher import ME, FakeWindowLink, MainHarness, server

A, B, C, D = (SessionId(name) for name in ("aaaa", "bbbb", "cccc", "dddd"))
NONE: frozenset[SessionId] = frozenset()  # no Session waits for a Confirmation


def command(text: str) -> MainCommandEntered:
    return MainCommandEntered(text)


def answered(session: Session, text: str) -> TurnEnded:
    item = {"type": "message", "content": [{"type": "output_text", "text": text}]}
    return TurnEnded(session.id, steps=[Step(output=[item])])


def with_max(main: MainHarness, maximum: int) -> None:
    main.settings = MainSettings(
        session=main.settings.session, windows=WindowsSettings(max=maximum)
    )


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


# The rule, with fake Windows.


def test_with_room_nobody_leaves_the_view():
    assert displaced([A, B], {A: 1, B: 2}, NONE, maximum=3) == []
    assert fits(2, 3) and not fits(3, 3)


def test_without_room_the_least_used_leaves_the_view():
    assert displaced([A, B, C], {A: 3, B: 1, C: 2}, NONE, maximum=3) == [B]


def test_one_waiting_for_a_confirmation_leaves_only_if_no_other_is_left():
    assert displaced([A, B], {A: 1, B: 2}, {A}, maximum=2) == [B]
    assert displaced([A, B], {A: 1, B: 2}, {A, B}, maximum=2) == [A]


def test_a_lower_maximum_sends_away_those_over_it_least_used_first():
    assert displaced([A, B, C, D], {A: 4, B: 1, C: 3, D: 2}, NONE, 2, coming=0) == [B, D]


def test_no_limit_never_sends_anyone_away():
    assert displaced([A, B, C], {}, NONE, maximum=0) == [] and fits(99, 0)


# Main's Commands.


def test_bg_closes_the_xterm_without_cancelling_the_turn_which_ends_and_shows_on_open(
    main: MainHarness,
):
    session = main.create_session()
    main.hold_turns = True
    chat = main.run(
        command(f"/open {session.id}"),
        InputReceived(session.id, "hola"),
        command(f"/bg {session.id}"),
        WindowClosed(session.id, returncode=0),  # its xterm says it is gone
        answered(session, "¡Hola!"),
        command(f"/open {session.id}"),
        settle=True,
    )
    [job] = main.held
    assert job.cancellation.reason is None
    first, second = main.windows(session.id)
    assert first.closed and Close() in first.sent
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None and len(conversation.turns) == 1
    assert ChatAppend(role="answer", label="AllIn", text="¡Hola!") in second.sent
    assert not any("cancel" in line for line in chat)


def test_a_window_brought_back_mid_turn_shows_what_it_streamed_and_goes_on(main: MainHarness):
    session = main.create_session()
    main.hold_turns = True
    main.run(
        command(f"/open {session.id}"),
        InputReceived(session.id, "cuenta"),
        TurnOutput(session.id, "answer", "uno, "),
        command(f"/bg {session.id}"),
        TurnOutput(session.id, "answer", "dos, "),
        command(f"/open {session.id}"),
        TurnOutput(session.id, "answer", "tres"),
        settle=True,
    )
    _, back = main.windows(session.id)
    texts = [m.text for m in back.sent if isinstance(m, ChatAppend | ChatDelta)]
    assert "uno, dos, tres" in "".join(texts)


def test_a_prompt_to_a_session_in_the_background_runs_its_turn_without_a_window(
    main: MainHarness,
):
    main.llm = FakeLlm([completed("", "Hola")])
    chat = main.run(command("/new --bg --id=fondo"), command("/send fondo hola"), settle=True)
    assert main.opened == []
    assert "[fondo] turn finished" in chat
    conversation = main.sessions.load_conversation(main.sessions.load(SessionId("fondo")))
    assert conversation is not None and len(conversation.turns) == 1


def test_close_cancels_the_turn_as_the_x_does_also_in_the_background(main: MainHarness):
    seen, hidden = main.create_session(), main.create_session()
    main.hold_turns = True
    main.server = server(slots=2)
    main.run(
        command(f"/open {seen.id}"),
        command(f"/open {hidden.id} --bg"),
        InputReceived(seen.id, "uno"),
        command(f"/send {hidden.id} dos"),
        command(f"/close {seen.id}"),
        command(f"/close {hidden.id}"),
        settle=True,
    )
    assert [job.cancellation.reason for job in main.held] == ["window closed"] * 2
    assert main.windows(hidden.id) == []
    assert main.state.saved[-1].background == []


def test_close_and_bg_need_an_open_session(main: MainHarness):
    session = main.create_session()
    chat = main.run(command(f"/bg {session.id}"), command(f"/close {session.id}"))
    assert sum("session_not_open" in line for line in chat) == 2


def test_with_a_maximum_of_two_a_third_new_sends_the_least_used_away(main: MainHarness):
    with_max(main, 2)
    first, second = main.create_session(), main.create_session()
    main.run(
        command(f"/open {first.id}"),
        command(f"/open {second.id}"),
        DraftReceived(first.id, ("escribo",)),  # the human used the first one last
        command("/new"),
        settle=True,
    )
    assert main.state.saved[-1].background == [second.id]
    assert len(main.state.saved[-1].windows) == 2


def test_a_click_counts_as_using_a_window(main: MainHarness):
    with_max(main, 2)
    first, second = main.create_session(), main.create_session()
    main.run(
        command(f"/open {first.id}"),
        command(f"/open {second.id}"),
        WindowUsed(first.id),
        command("/new"),
        settle=True,
    )
    assert main.state.saved[-1].background == [second.id]


def test_new_in_the_background_displaces_nobody(main: MainHarness):
    with_max(main, 2)
    first, second = main.create_session(), main.create_session()
    main.run(command(f"/open {first.id}"), command(f"/open {second.id}"), command("/new --bg"))
    saved = main.state.saved[-1]
    assert list(saved.windows) == [first.id, second.id] and len(main.opened) == 2
    [hidden] = saved.background
    assert hidden not in (first.id, second.id)


def test_a_session_waiting_for_a_confirmation_stays_in_view(main: MainHarness):
    with_max(main, 2)
    first, second = main.create_session(), main.create_session()
    main.hold_turns = True
    main.run(
        command(f"/open {first.id}"),
        command(f"/open {second.id}"),
        InputReceived(first.id, "haz algo"),
        DraftReceived(second.id, ("escribo",)),  # the first is the least used…
        ConfirmationRequested(first.id, "Run it?", "really?"),  # …but it waits for an answer
        command("/new"),
        settle=True,
    )
    assert main.state.saved[-1].background == [second.id]


def test_a_lower_maximum_sends_the_extra_windows_away_at_once(main: MainHarness):
    sessions = [main.create_session() for _ in range(3)]
    main.run(*(command(f"/open {s.id}") for s in sessions), command("/set windows.max=1"))
    saved = main.state.saved[-1]
    assert saved.background == [sessions[0].id, sessions[1].id]
    assert list(saved.windows) == [sessions[2].id]


def test_nothing_comes_back_from_the_background_by_itself(main: MainHarness):
    with_max(main, 1)
    first, second = main.create_session(), main.create_session()
    main.run(
        command(f"/open {first.id}"),
        command(f"/open {second.id}"),  # first goes to the background
        command(f"/close {second.id}"),  # a free place
    )
    assert len(main.windows(first.id)) == 1
    assert main.state.saved[-1].background == [first.id]


def test_the_draft_of_a_session_in_the_background_comes_back_with_it(main: MainHarness):
    session = main.create_session()
    main.run(
        command(f"/open {session.id}"),
        DraftReceived(session.id, ("a medio escribir",)),
        command(f"/bg {session.id}"),
        command(f"/open {session.id}"),
        settle=True,
    )
    _, back = main.windows(session.id)
    assert InputRestore(text="a medio escribir") in back.sent


def test_after_a_restart_what_was_in_view_comes_back_up_to_the_maximum(main: MainHarness):
    sessions = [main.create_session() for _ in range(3)]
    hidden = main.create_session()
    main.run(
        *(command(f"/open {s.id}") for s in sessions),
        command(f"/open {hidden.id} --bg"),
    )
    with_max(main, 2)
    main.opened.clear()
    main.run()  # as after /exit or kill -9: MainState was saved after every event
    assert [sid for sid, _ in main.opened] == [sessions[1].id, sessions[2].id]
    assert main.state.saved[-1].background == [hidden.id, sessions[0].id]


def test_a_profile_switch_and_back_keeps_the_same_sessions_in_the_background(
    main: MainHarness,
):
    session = main.create_session()
    other = main.profiles.create("otro", main.profile)
    main.run(
        command(f"/open {session.id} --bg"),
        command("/profile switch otro"),
        lambda: None,  # once switched
        command("/profile switch default"),
        settle=True,
    )
    assert MainStateStore(other.state).load().background == []
    assert MainStateStore(main.profile.state).load().background == [session.id]


def test_what_another_origin_opens_never_displaces_a_window(tmp_path: Path):
    sessions = SessionStore(tmp_path / "sessions")
    reporter = Reporter(lambda _: None, Log(tmp_path / "main.jsonl"), sessions)
    opened: list[SessionId] = []

    def open_window(session_id: SessionId, _: Theme) -> FakeWindowLink:
        opened.append(session_id)
        return FakeWindowLink(pid=len(opened))

    windows = OpenWindows(
        open_window, reporter, sessions, LinkResolver(tmp_path, "http://127.0.0.1:9931/v1")
    )
    windows.set_maximum(1)
    first = sessions.create(ME, SessionSettings(), "")
    second = sessions.create(ME, SessionSettings(), "")
    assert windows.show(first, None, human=True)
    assert not windows.show(second, None, human=False)
    assert opened == [first.id] and windows.background_ids() == [second.id]
    assert windows.show(second, None, human=True)  # a human's always shows
    assert opened == [first.id, second.id] and windows.background_ids() == [first.id]


def test_a_window_tells_main_of_its_clicks_at_most_once_every_two_seconds():
    sent: list[float] = []
    now = [0.0]
    report = ClickReporter(lambda: sent.append(now[0]), clock=lambda: now[0])
    for moment in (0.0, 0.5, 1.9, 2.0, 2.1, 5.0):
        now[0] = moment
        report()
    assert sent == [0.0, 2.0, 5.0]
