"""Function tools in a Turn (ADR-0004, ADR-0005, #106): the LLM calls a tool, Main runs it
confined without the Turn's slot, and the next Step reads what it gave back. The tools run for
real on this kernel; the LLM is fake."""

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.errors import TurnCancelled
from allin.ipc.messages import ChatAppend
from allin.main.chat_rebuild import rebuild_chat
from allin.main.conversation import Item, Step, Turn
from allin.main.descriptions import Descriptions
from allin.main.events import InputReceived, MainCommandEntered, ToolsEnded, ToolsStarted, TurnEnded
from allin.main.executors import TurnJob
from allin.main.quarantine import PieceKind, PieceRef, Quarantine
from allin.main.sessions import Session
from allin.main.turn import Cancellation
from tests.llm_fakes import DroppedStream, FakeLlm, full_reply, reasoning, tool_call
from tests.origins import A_SESSION
from tests.test_dispatcher import TURNS_ENDED, MainHarness, server_ready

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

TOOLS = {
    "shout": '''
def shout(text: str) -> str:
    """Say it louder."""
    return text.upper()
''',
    "nap": '''
import time


def nap(seconds: float) -> str:
    """Sleep a while."""
    time.sleep(seconds)
    return "rested"
''',
    "broken": '''
def broken() -> str:
    """Fail."""
    raise ValueError("it broke")
''',
}


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    main = MainHarness(tmp_path)
    main.describer = ToolLauncher(tmp_path)
    main.describer.start()
    main.threaded_turns = True
    yield main
    main.describer.stop()


def install(main: MainHarness, kind: PieceKind = "function", name: str = "voice") -> None:
    """A package of tools in the Profile, approved, one module per tool."""
    folder = main.profile.folder / ("functions" if kind == "function" else "agents") / name
    folder.mkdir(parents=True)
    for tool, source in TOOLS.items():
        (folder / f"{tool}.py").write_text(source)
    assert main.describer is not None
    descriptions = Descriptions(main.profile.folder.parents[1], main.describer, main.confinement)
    pieces = Quarantine(main.profile.folder, descriptions)
    ref = PieceRef(kind, name)
    pieces.approve(ref, pieces.piece(ref).hash)


def new_session(main: MainHarness, *options: str) -> Session:
    before = set(main.sessions.ids())
    main.run(MainCommandEntered(" ".join(["/new", "--bg", *options])), settle=True)
    [sid] = set(main.sessions.ids()) - before
    return main.sessions.load(sid)


def last_turn(main: MainHarness, session: Session) -> Turn:
    conversation = main.sessions.load_conversation(main.sessions.load(session.id))
    assert conversation is not None
    return conversation.turns[-1]


def rebuild_chat_of(main: MainHarness, session: Session) -> list[ChatAppend]:
    """What its Window shows when it is opened again."""
    loaded = main.sessions.load(session.id)
    conversation = main.sessions.load_conversation(loaded)
    assert conversation is not None
    return rebuild_chat(conversation, main.session_log(session), loaded.settings)


def outputs(request: dict[str, Any]) -> list[str]:
    """What the tools gave back, as a Step's input carries it."""
    items: list[Item] = request["input"]
    return [item["output"] for item in items if item.get("type") == "function_call_output"]


def test_the_llm_calls_an_fsource_and_reads_its_output_in_the_same_turn(main: MainHarness):
    install(main)
    session = new_session(main)
    main.llm = FakeLlm(
        tool_call("shout", json.dumps({"text": "hola"})), full_reply("", "HOLA dijo")
    )
    main.run(InputReceived(session.id, "grita hola"), settle=True)
    first, second = main.llm.requests
    assert {tool["name"] for tool in first["tools"]} == {"shout", "nap", "broken"}
    assert outputs(second) == ['"HOLA"']
    turn = last_turn(main, session)
    assert [len(step.results) for step in turn.steps] == [1, 0]
    called = [e.fields for e in main.session_log(session) if e.event == "tools_called"]
    assert called == [{"tools": ["shout"]}]


def test_a_tool_that_fails_gives_its_error_to_the_llm_and_the_turn_goes_on(main: MainHarness):
    install(main)
    session = new_session(main)
    main.llm = FakeLlm(tool_call("broken", "{}"), full_reply("", "se rompió"))
    main.run(InputReceived(session.id, "rompe"), settle=True)
    [output] = outputs(main.llm.requests[1])
    assert "it broke" in output and output.startswith("error")
    assert len(last_turn(main, session).steps) == 2


def test_arguments_that_are_not_json_and_a_tool_it_does_not_have_are_errors_it_reads(
    main: MainHarness,
):
    install(main)
    session = new_session(main)
    main.llm = FakeLlm(
        tool_call("shout", "{not json"), tool_call("fly", "{}", "call_2"), full_reply("", "ok")
    )
    main.run(InputReceived(session.id, "x"), settle=True)
    assert outputs(main.llm.requests[2]) == [
        "error: its arguments are not a JSON object",
        "error: there is no tool named 'fly'",
    ]


def test_while_its_tool_runs_the_turn_has_no_slot_and_another_sessions_step_starts(
    main: MainHarness,
):
    install(main)
    a, b = new_session(main), new_session(main)
    main.llm = FakeLlm(
        tool_call("nap", json.dumps({"seconds": 1.5})), full_reply("", "b"), full_reply("", "a")
    )
    main.run(InputReceived(a.id, "duerme"), InputReceived(b.id, "tú"))
    finished = [e.fields["sid"] for e in main.main_log() if e.event == "turn_finished"]
    assert finished == [b.id, a.id]  # one slot: B ran while A's tool slept
    assert outputs(main.llm.requests[2]) == ['"rested"']


def test_a_timeout_takes_the_tool_down_and_tells_the_llm(main: MainHarness):
    install(main)
    main.run(MainCommandEntered("/set session.tools.timeout=1"), settle=True)
    session = new_session(main)
    main.llm = FakeLlm(tool_call("nap", json.dumps({"seconds": 30})), full_reply("", "tarde"))
    main.run(InputReceived(session.id, "duerme"), settle=True)
    [output] = outputs(main.llm.requests[1])
    assert output.startswith("error: tool_timed_out")


def test_cutting_the_turn_takes_its_running_tool_down(main: MainHarness):
    install(main)
    session = new_session(main)
    main.llm = FakeLlm(tool_call("nap", json.dumps({"seconds": 30})))
    started = time.monotonic()
    main.run(
        InputReceived(session.id, "duerme"),
        lambda: time.sleep(0.5),  # the tool is running by now
        InputReceived(session.id, "", interrupt=True),  # Shift+Enter
        settle=True,
    )
    assert time.monotonic() - started < 10  # not the 30 s of its nap
    turn = last_turn(main, session)
    assert turn.cut == "interrupted" and len(turn.steps) == 1


def test_a_fall_after_its_tools_ran_repeats_only_the_half_done_step(main: MainHarness):
    install(main)
    session = new_session(main)
    main.llm = FakeLlm(
        tool_call("shout", json.dumps({"text": "una vez"})),
        DroppedStream([]),  # the Server falls in the second Step
        full_reply("", "listo"),
    )
    main.run(InputReceived(session.id, "grita"), settle=True)
    main.server_up = True
    main.run(settle=True)
    shouted = [e for e in main.session_log(session) if e.event == "tools_called"]
    assert len(shouted) == 1  # its tool did not run again
    assert outputs(main.llm.requests[-1]) == ['"UNA VEZ"']
    assert [len(s.results) for s in last_turn(main, session).steps] == [1, 0]


def test_new_inherits_the_fsources_unless_sf_off_and_takes_the_fagents_it_is_given(
    main: MainHarness,
):
    install(main)
    install(main, "agent", "helper")
    assert new_session(main).functions == ("function:voice",)
    assert new_session(main, "-sf=off").functions == ()
    assert new_session(main, "-sf=off", "-fa=helper").functions == ("agent:helper",)


def test_func_adds_and_removes_and_only_a_human_adds(main: MainHarness):
    install(main, "agent", "helper")
    session = new_session(main, "-sf=off")
    chat = main.run(MainCommandEntered(f"/func add {session.id} agent:helper", origin=A_SESSION))
    assert any("human_only" in line for line in chat)
    main.run(MainCommandEntered(f"/func add {session.id} agent:helper"), settle=True)
    assert main.sessions.load(session.id).functions == ("agent:helper",)
    chat = main.run(MainCommandEntered(f"/func list {session.id}"))
    assert f"[{session.id}] agent:helper  approved" in chat
    main.run(MainCommandEntered(f"/func remove {session.id} agent:helper", origin=A_SESSION))
    assert main.sessions.load(session.id).functions == ()


def went_on(job: TurnJob) -> bool:
    """Whether Main let the held Turn's next Step start after its tools."""
    cancellation = Cancellation()
    timer = threading.Timer(0.2, cancellation.cancel, args=("test",))
    timer.start()
    job.tools.connect(job.session_id, cancellation, lambda _: None, job.confirmation)
    try:
        job.tools.run(Step(output=[]))
    except TurnCancelled:
        return False
    finally:
        timer.cancel()
    return True


def test_the_step_after_its_tools_goes_on_before_the_new_prompts_of_its_main(main: MainHarness):
    a, b, c = main.create_session(), main.create_session(), main.create_session()
    main.threaded_turns, main.hold_turns = False, True
    main.run(
        InputReceived(a.id, "hi"),
        ToolsStarted(a.id, ("nap",)),  # A's slot is free while its tool runs
        InputReceived(b.id, "hi"),
        InputReceived(c.id, "hi"),  # waits: B holds the only slot
        ToolsEnded(a.id, 10),  # A waits too, but ahead of C
        TurnEnded(b.id, steps=[]),  # B's slot goes to A, not to C
        settle=True,
    )
    assert [job.session_id for job in main.held] == [a.id, b.id], "C still waits"
    assert went_on(main.held[0])


def test_a_turn_cancelled_while_it_waits_for_a_slot_after_its_tools_is_not_taken_up_again(
    main: MainHarness,
):
    a, b = main.create_session(), main.create_session()
    main.threaded_turns, main.hold_turns = False, True
    main.run(
        InputReceived(a.id, "hi"),
        ToolsStarted(a.id, ("nap",)),
        InputReceived(b.id, "hi"),  # B takes the only slot
        ToolsEnded(a.id, 10),
        InputReceived(a.id, "", interrupt=True),  # A is cut while it waits
        TurnEnded(b.id, steps=[]),
        settle=True,
    )
    assert not went_on(main.held[0])


def test_a_fall_in_each_of_two_steps_does_not_fail_the_turn(main: MainHarness):
    install(main)
    session = new_session(main)
    main.llm = FakeLlm(
        DroppedStream([reasoning("…")]),  # falls in its first Step
        tool_call("shout", json.dumps({"text": "a"})),
        DroppedStream([reasoning("…")]),  # and in its second one
        full_reply("", "listo"),
    )
    main.run(
        InputReceived(session.id, "grita"),
        TURNS_ENDED,
        server_ready(main.server),  # the fall paused the queue
        TURNS_ENDED,
        server_ready(main.server),
        settle=True,
    )  # all in one Main: a fall in a Step counts only for that Step
    turn = last_turn(main, session)
    assert [len(step.results) for step in turn.steps] == [1, 0]
    events = [e.event for e in main.session_log(session)]
    assert events.count("turn_suspended") == 2 and events[-1] == "turn_finished"
    assert len(main.llm.requests) == 4  # each half-done Step once more, nothing else


def test_a_tool_that_failed_shows_in_the_window_also_when_it_is_opened_again(main: MainHarness):
    install(main)
    session = new_session(main)
    main.llm = FakeLlm(tool_call("broken", "{}"), full_reply("", "vaya"))
    main.run(InputReceived(session.id, "rompe"), settle=True)
    failed = [e.fields for e in main.session_log(session) if e.event == "tool_failed"]
    assert [f["tool"] for f in failed] == ["broken"]
    shown = [m.text for m in rebuild_chat_of(main, session) if m.label == "tools"]
    assert shown[0] == "tools called: broken"
    assert shown[1].startswith("broken failed: error: ") and "it broke" in shown[1]
