"""The Origin's chain inside Main: what it keeps, what it refuses and what it never runs
(ADR-0013, #105)."""

from pathlib import Path

import pytest

from allin.core.errors import TurnCancelled
from allin.core.ids import BoxId
from allin.core.origin import HUMAN, Origin, from_process, from_session
from allin.main.conversation import Turn
from allin.main.events import (
    BoxAnswered,
    InputReceived,
    MainCommandEntered,
    TurnEnded,
    WindowClosed,
)
from allin.main.sessions import Session
from allin.ui.screen import ShowBox
from tests.llm_fakes import FakeLlm
from tests.test_compaction_turns import answers
from tests.test_dispatcher import MainHarness, server


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    main = MainHarness(tmp_path)
    main.llm = FakeLlm(*answers(2))
    return main


def last_turn(main: MainHarness, session: Session) -> Turn:
    conversation = main.sessions.load_conversation(main.sessions.load(session.id))
    assert conversation is not None
    return conversation.turns[-1]


def boxes(main: MainHarness) -> list[str]:
    return [u.box.kind for u in main.main_screen if isinstance(u, ShowBox)]


def test_prompts_and_commands_keep_their_whole_chain_in_the_log(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    origin = from_session(from_session(HUMAN, a.id), b.id)
    main.run(MainCommandEntered(f"/send {a.id} hola", origin=origin), settle=True)
    chain = ["human", f"session:{a.id}", f"session:{b.id}"]
    [prompt] = [e.fields for e in main.session_log(a) if e.event == "prompt"]
    assert prompt["origin"] == chain
    [command] = [e.fields for e in main.main_log() if e.event == "command"]
    assert command["origin"] == chain
    assert last_turn(main, a).origin == origin  # the Turn too


def test_y_from_a_session_is_an_error_kept_where_its_llm_reads(main: MainHarness):
    a = main.create_session()
    chat = main.run(MainCommandEntered("/reset -y", origin=from_session(HUMAN, a.id)))
    assert any("error human_only" in line for line in chat)
    [command] = [e.fields for e in main.session_log(a) if e.event == "command"]
    assert command["result"].startswith("error human_only")
    assert command["origin"] == ["human", f"session:{a.id}"]
    assert boxes(main) == []  # nothing asked, nothing run


def test_text_from_a_session_is_data_and_runs_nothing(main: MainHarness):
    a, b, c = main.create_session(), main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(
            f"/send {a.id} @send {c.id} hola /reset", origin=from_session(HUMAN, b.id)
        ),
        settle=True,
    )
    [prompt] = [e.fields for e in main.session_log(a) if e.event == "prompt"]
    assert prompt["text"] == f"[from Session {b.id}]\n@send {c.id} hola /reset"
    assert [e for e in main.session_log(c) if e.event == "prompt"] == []
    assert boxes(main) == []
    assert last_turn(main, a).prompt.startswith(
        f"[from Session {b.id}]"
    )  # a user message, never developer


def test_closing_a_window_cancels_only_a_turn_a_human_asked_for(main: MainHarness):
    human, other = main.create_session(), main.create_session()
    sender = main.create_session()
    main.hold_turns = True
    main.server = server(slots=2)
    reasons: dict[str, str | None] = {}  # as they are before Main stops
    main.run(
        MainCommandEntered(f"/open {human.id}"),
        MainCommandEntered(f"/open {other.id}"),
        InputReceived(human.id, "mío"),
        MainCommandEntered(f"/send {other.id} trabajo", origin=from_session(HUMAN, sender.id)),
        WindowClosed(human.id, returncode=0),
        WindowClosed(other.id, returncode=0),
        lambda: reasons.update({job.session_id: job.cancellation.reason for job in main.held}),
        settle=True,
    )
    assert reasons == {human.id: "window closed", other.id: None}


@pytest.mark.parametrize(
    ("exempt", "through_process", "answered"),
    [(True, False, True), (False, False, False), (True, True, False)],
)
def test_devastation_answers_only_a_chain_it_covers_whole(
    main: MainHarness, exempt: bool, through_process: bool, answered: bool
):
    a = main.create_session(exempt=exempt)
    origin: Origin = from_session(HUMAN, a.id)
    if through_process:  # a Proceso adherido never has the Exemption
        origin = from_session(from_process(origin, "rag"), a.id)
    main.devastation = True
    chat = main.run(MainCommandEntered("/reset", origin=origin))
    assert any("answered by --devastation" in line for line in chat) == answered
    assert ("confirm" in boxes(main)) != answered


def test_closing_a_window_drops_only_the_human_prompts_and_the_rest_goes_on(main: MainHarness):
    session, sender = main.create_session(), main.create_session()
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "mío"),
        InputReceived(session.id, "otro mío"),
        MainCommandEntered(f"/send {session.id} trabajo", origin=from_session(HUMAN, sender.id)),
        WindowClosed(session.id, returncode=0),
        TurnEnded(session.id, error=TurnCancelled(reason="window closed")),
        settle=True,
    )
    [dropped] = [e.fields for e in main.session_log(session) if e.event == "prompts_dropped"]
    assert dropped["texts"] == ["otro mío"]
    [first, then] = main.held
    assert first.cancellation.reason is not None
    assert then.request.input[-1] == {
        "role": "user",
        "content": f"[from Session {sender.id}]\ntrabajo",
    }


def test_a_command_its_human_rejected_is_kept_where_the_sessions_llm_reads(main: MainHarness):
    a = main.create_session(exempt=False)
    main.run(
        MainCommandEntered("/reset", origin=from_session(HUMAN, a.id)),
        BoxAnswered(BoxId("b1"), False),
        settle=True,
    )
    logged = [(e.event, e.fields.get("result")) for e in main.session_log(a)]
    assert logged[-2:] == [("confirmation_asked", None), ("command", "rejected")]
    assert all(
        e.fields["origin"] == ["human", f"session:{a.id}"]
        for e in main.main_log()
        if e.event in ("confirmation_asked", "command")
    )
