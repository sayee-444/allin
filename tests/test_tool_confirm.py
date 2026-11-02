"""``@confirm`` on Function tools (ADR-0009, ADR-0013, #107): before a delicate call runs, a
human answers yes or no, unless ``-y`` on a human's Prompt or ``--devastation`` covering its
whole chain answers it. The tools run for real on this kernel; the LLM is fake."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.ids import BoxId, PieceId
from allin.core.origin import HUMAN, Link, Origin
from allin.main.conversation import Item
from allin.main.descriptions import Descriptions
from allin.main.events import BoxAnswered, InputReceived, MainCommandEntered
from allin.main.quarantine import Adjustments, PieceRef, Quarantine
from allin.main.sessions import Session
from allin.main.tool_steps import REFUSED
from allin.ui.boxes import Box
from allin.ui.screen import ShowBox
from tests.llm_fakes import FakeLlm, full_reply, tool_call
from tests.test_dispatcher import MainHarness, Until

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

FIRST_BOX = BoxId("b1")
AFTER_NOTICE = BoxId("b2")  # the Confirmation after the box that says --devastation is on
VOICE = PieceId("function:voice")
TOOLS = {
    "shout": '''
from allin.decorators import confirm


@confirm
def shout(text: str) -> str:
    """Say it louder."""
    return text.upper()
''',
    "whisper": '''
def whisper(text: str) -> str:
    """Say it softer."""
    return text.lower()
''',
}


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    main = MainHarness(tmp_path)
    main.describer = ToolLauncher(tmp_path)
    main.describer.start()
    main.threaded_turns = True
    install(main)
    yield main
    main.describer.stop()


def install(main: MainHarness, adjustments: Adjustments | None = None) -> None:
    """The ``voice`` package in the Profile, approved: ``shout`` declares ``@confirm``."""
    folder = main.profile.folder / "functions" / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    for tool, source in TOOLS.items():
        (folder / f"{tool}.py").write_text(source)
    assert main.describer is not None
    descriptions = Descriptions(main.profile.folder.parents[1], main.describer, main.confinement)
    pieces = Quarantine(main.profile.folder, descriptions)
    ref = PieceRef("function", "voice")
    pieces.approve(ref, pieces.piece(ref).hash, adjustments)


def with_voice(main: MainHarness, exempt: bool = True) -> Session:
    """A Session of Main with the ``voice`` package."""
    session = main.create_session(exempt=exempt)
    return main.sessions.save_functions(session, (VOICE,))


def asked(main: MainHarness) -> list[Box]:
    """The Confirmations shown in Main's Chat."""
    shown = [update.box for update in main.main_screen if isinstance(update, ShowBox)]
    return [box for box in shown if box.kind == "confirm"]


def box_shown(main: MainHarness) -> Until:
    return Until(lambda: bool(asked(main)))


def outputs(request: dict[str, Any]) -> list[str]:
    items: list[Item] = request["input"]
    return [item["output"] for item in items if item.get("type") == "function_call_output"]


def shout() -> FakeLlm:
    return FakeLlm(tool_call("shout", json.dumps({"text": "hola"})), full_reply("", "listo"))


def test_a_tool_with_confirm_asks_and_runs_on_yes(main: MainHarness):
    session = with_voice(main)
    main.llm = shout()
    main.run(
        InputReceived(session.id, "grita"),
        box_shown(main),
        BoxAnswered(FIRST_BOX, True),
        settle=True,
    )
    [box] = asked(main)
    assert "shout" in box.title and '"hola"' in box.text
    assert outputs(main.llm.requests[1]) == ['"HOLA"']


def test_on_no_it_does_not_run_and_the_llm_reads_the_refusal(main: MainHarness):
    session = with_voice(main)
    main.llm = shout()
    main.run(
        InputReceived(session.id, "grita"),
        box_shown(main),
        BoxAnswered(FIRST_BOX, False),
        settle=True,
    )
    assert outputs(main.llm.requests[1]) == [REFUSED]
    events = [e.event for e in main.session_log(session)]
    assert "confirmation_answered" in events and events[-1] == "turn_finished"


def test_a_tool_without_it_does_not_ask(main: MainHarness):
    session = with_voice(main)
    main.llm = FakeLlm(tool_call("whisper", json.dumps({"text": "HOLA"})), full_reply("", "ok"))
    main.run(InputReceived(session.id, "susurra"), settle=True)
    assert asked(main) == []
    assert outputs(main.llm.requests[1]) == ['"hola"']


def test_the_approval_can_add_it_to_a_tool_that_does_not_declare_it(main: MainHarness):
    install(main, Adjustments(confirm=("whisper",)))
    session = with_voice(main)
    main.llm = FakeLlm(tool_call("whisper", json.dumps({"text": "HOLA"})), full_reply("", "ok"))
    main.run(
        InputReceived(session.id, "susurra"),
        box_shown(main),
        BoxAnswered(FIRST_BOX, True),
        settle=True,
    )
    assert len(asked(main)) == 1


def test_while_it_waits_its_turn_has_no_slot(main: MainHarness):
    # It goes on as a Continuation once answered, as any Confirmation (test_confirmation.py).
    a, b = with_voice(main), main.create_session()
    main.llm = FakeLlm(
        tool_call("shout", json.dumps({"text": "a"})),
        full_reply("", "b"),  # B runs on the only slot while A waits for its answer
        full_reply("", "a"),
    )
    main.run(
        InputReceived(a.id, "grita"),
        box_shown(main),
        InputReceived(b.id, "tú"),
        Until(lambda: "turn_finished" in [e.event for e in main.main_log()]),
        BoxAnswered(FIRST_BOX, True),
        settle=True,
    )
    finished = [e.fields["sid"] for e in main.main_log() if e.event == "turn_finished"]
    assert finished == [b.id, a.id]


def test_y_from_a_human_answers_it_for_that_turn_only(main: MainHarness):
    session = with_voice(main)
    call = tool_call("shout", json.dumps({"text": "hola"}))
    main.llm = FakeLlm(call, full_reply("", "uno"), call, full_reply("", "dos"))
    main.run(MainCommandEntered(f"/send -y {session.id} grita"), settle=True)
    assert asked(main) == []
    assert outputs(main.llm.requests[1]) == ['"HOLA"']
    main.run(
        MainCommandEntered(f"/send {session.id} otra vez"),
        box_shown(main),
        BoxAnswered(FIRST_BOX, False),
        settle=True,
    )
    assert len(asked(main)) == 1, "the next Prompt, without -y, asks"


def test_y_from_a_session_is_an_error(main: MainHarness):
    session = with_voice(main)
    other = main.create_session()
    origin = Origin(chain=(Link("session", other.id),))
    chat = main.run(MainCommandEntered(f"/send -y {session.id} grita", origin=origin))
    assert any("error human_only" in line for line in chat)
    assert main.llm.requests == []


def test_devastation_answers_it_for_a_session_with_the_exemption(main: MainHarness):
    session = with_voice(main, exempt=True)
    main.llm = shout()
    main.run(MainCommandEntered("/devastation on"), InputReceived(session.id, "grita"), settle=True)
    assert asked(main) == []
    answered = [
        e.fields for e in main.session_log(session) if e.event == "confirmation_auto_answered"
    ]
    assert [fields["by"] for fields in answered] == ["--devastation"]


@pytest.mark.parametrize(
    "through",
    [
        pytest.param("session", id="session-without-exemption"),
        pytest.param("process", id="proceso-adherido"),
    ],
)
def test_devastation_does_not_answer_a_chain_it_does_not_cover(main: MainHarness, through: str):
    session = with_voice(main, exempt=True)
    sender = main.create_session(exempt=False)
    link = Link("session", sender.id) if through == "session" else Link("process", "bot")
    origin = Origin(chain=(*HUMAN.chain, link))
    main.llm = shout()
    main.run(
        MainCommandEntered("/devastation on"),
        MainCommandEntered(f"/send {session.id} grita", origin=origin),
        box_shown(main),
        BoxAnswered(AFTER_NOTICE, True),
        settle=True,
    )
    assert len(asked(main)) == 1


def test_devastation_does_not_answer_it_for_a_session_without_the_exemption(main: MainHarness):
    session = with_voice(main, exempt=False)
    main.llm = shout()
    main.run(
        MainCommandEntered("/devastation on"),
        InputReceived(session.id, "grita"),
        box_shown(main),
        BoxAnswered(AFTER_NOTICE, True),
        settle=True,
    )
    assert len(asked(main)) == 1
