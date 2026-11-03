"""Consultar (#118): a Session reads, and only reads, what it has within reach: the queue, the
Subscriptions and Encargos, the Procesos adheridos, the Servers and Supervisors."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.ids import CommandName, PieceId
from allin.main.descriptions import Descriptions
from allin.main.events import InputReceived, MainCommandEntered, Tick
from allin.main.quarantine import PieceRef, Quarantine
from allin.main.sessions import Session
from tests.llm_fakes import FakeLlm, full_reply, tool_call
from tests.test_dispatcher import MainHarness
from tests.test_session_commands import outputs
from tests.test_subscriptions import from_session

LOOKER = PieceId("function:looker")
LOOK = '''
from allin.channel import command
from allin.decorators import reach


@reach(commands=["status"])
def look(part: str) -> object:
    """What this Session has within reach."""
    return command(f"@status {part}".strip())
'''


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


@pytest.fixture
def tools(main: MainHarness, tmp_path: Path) -> Iterator[MainHarness]:
    """Main with the real confined ``looker`` package, approved."""
    if abi_version() < 6:
        pytest.skip("needs Landlock ABI 6 (kernel 6.12)")
    main.describer = ToolLauncher(tmp_path)
    main.describer.start()
    main.threaded_turns = True
    folder = main.profile.folder / "functions" / "looker"
    folder.mkdir(parents=True)
    (folder / "look.py").write_text(LOOK)
    descriptions = Descriptions(main.profile.folder.parents[1], main.describer, main.confinement)
    pieces = Quarantine(main.profile.folder, descriptions)
    ref = PieceRef("function", "looker")
    pieces.approve(ref, pieces.piece(ref).hash)
    yield main
    main.describer.stop()


def looker(main: MainHarness) -> Session:
    created = main.sessions.save_functions(main.create_session(), (LOOKER,))
    return main.sessions.save_commands(created, (CommandName("status"),))


def read_state(main: MainHarness, a: Session, part: str = "") -> dict[str, Any]:
    main.llm = FakeLlm(tool_call("look", json.dumps({"part": part})), full_reply("", "visto"))
    main.run(InputReceived(a.id, "mira"), settle=True)
    [answer] = [o for r in main.llm.requests for o in outputs(r)]
    assert answer["result"] == "ok", answer
    return answer["state"]


def test_a_tool_reads_the_queue_and_the_servers(tools: MainHarness):
    a = looker(tools)
    state = read_state(tools, a)
    assert {"session": a.id, "running": True, "queued": 0} in state["queue"]
    [served] = state["servers"]
    assert served["state"] == "ready" and served["slots"] == 1 and served["busy"] == 1
    assert served["model"] == "m.gguf"
    assert state["supervisors"] == []


def test_it_reads_only_the_part_asked_for(tools: MainHarness):
    a = looker(tools)
    assert set(read_state(tools, a, "queue")) == {"queue"}


def test_a_human_reads_it_in_mains_chat_and_an_unknown_part_is_refused(main: MainHarness):
    main.hold_turns = True  # b's Turn runs until the test ends it
    a, b = main.create_session(), main.create_session()
    chat = main.run(
        MainCommandEntered(f"/subscribe {a.id} timer:60"),
        MainCommandEntered(f"/errand {a.id} --to={b.id} trabaja"),
        MainCommandEntered(f"/status {a.id}"),
        MainCommandEntered(f"/status {a.id} everything"),
        settle=True,
    )
    shown = "\n".join(chat)
    assert f"{b.id}: running" in shown
    assert "timer:60" in shown and f"errand:{b.id}" in shown and "0 of 1 answered" in shown
    assert "error invalid_arguments" in shown


def test_it_shows_nothing_of_another_profile(main: MainHarness):
    a, b, c = (main.create_session() for _ in range(3))
    other = main.profiles.create("tesis", main.profile)
    main.profiles.add_session(other, c.id)
    main.profiles.remove_session(main.profile, c.id)
    main.llm = FakeLlm(full_reply("", "hola"))
    chat = main.run(
        MainCommandEntered("/profile switch tesis"),
        MainCommandEntered(f"/subscribe {c.id} timer:300"),
        MainCommandEntered(f"/send {c.id} hola"),
        MainCommandEntered("/profile switch default"),
        MainCommandEntered(f"/subscribe {a.id} timer:60"),
        MainCommandEntered(f"/status {b.id}"),
        settle=True,
    )
    shown = "\n".join(chat[chat.index("/profile switch default: ok") :])
    assert "timer:60" in shown
    assert c.id not in shown and "timer:300" not in shown


def test_it_changes_nothing(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(
        MainCommandEntered(f"/errand {a.id} --to={b.id} trabaja"),
        MainCommandEntered(f"/subscribe {a.id} timer:60"),
        Tick(),
        settle=True,
    )
    state = main.profile.state
    before = state.read_bytes()
    sessions_before = [main.sessions.load(s.id) for s in (a, b)]
    main.run(
        MainCommandEntered(f"/status {a.id}"),
        MainCommandEntered(f"/status {b.id}", origin=from_session(b)),
        settle=True,
    )
    assert state.read_bytes() == before
    assert [main.sessions.load(s.id) for s in (a, b)] == sessions_before
