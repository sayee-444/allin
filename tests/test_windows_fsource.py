"""The fsource ``windows`` and its Command sessions (#122, ADR-0020): the LLM lists the Sessions,
sends its own and those it created to the background, and brings one into view without
displacing anyone; only a human closes a Window."""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import JsonValue

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.ids import BoxId, PieceId
from allin.core.origin import HUMAN, Origin, from_session
from allin.main.boxes import ATTENTION_TITLE
from allin.main.descriptions import Descriptions
from allin.main.events import BoxAnswered, InputReceived, MainCommandEntered, OrganizerConnected
from allin.main.quarantine import PieceRef, Quarantine
from allin.main.session_commands import BUILT_IN
from allin.main.sessions import Session
from allin.ui.boxes import Box
from allin.ui.organizer_view import row_state
from allin.ui.screen import HideBox, ShowBox
from tests.llm_fakes import FakeLlm, full_reply, tool_call
from tests.test_background import with_max
from tests.test_dispatcher import MainHarness
from tests.test_organizer import last_rows
from tests.test_session_commands import outputs


def command(text: str) -> MainCommandEntered:
    return MainCommandEntered(text)


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


# Typed by a human in a Window


def test_close_typed_in_a_window_closes_its_own(main: MainHarness):
    session = main.create_session()
    main.run(command(f"/open {session.id}"), InputReceived(session.id, "@close"), settle=True)
    saved = main.state.saved[-1]
    assert saved.windows == {} and saved.background == []


def test_bg_typed_in_a_window_sends_its_own_to_the_background(main: MainHarness):
    session = main.create_session()
    main.run(command(f"/open {session.id}"), InputReceived(session.id, "@bg"), settle=True)
    saved = main.state.saved[-1]
    assert saved.windows == {} and saved.background == [session.id]


def test_windows_typed_in_a_window_writes_the_list_in_mains_chat(main: MainHarness):
    session = main.create_session()
    chat = main.run(command(f"/open {session.id}"), InputReceived(session.id, "@windows"))
    at = chat.index("open sessions: 1")
    assert chat[at + 1] == f"  {session.id} — in view · server base_url"


# From a Session's LLM


def of(session: Session) -> Origin:
    """What a Session's tool runs, during a Turn a human asked for."""
    return from_session(HUMAN, session.id)


def results(main: MainHarness, text: str) -> list[JsonValue]:
    """What Main's Log says each run of the Command main ``text`` came to."""
    return [e.fields.get("result") for e in main.main_log() if e.fields.get("command") == text]


def test_a_session_sends_its_own_to_the_background_and_one_it_created(main: MainHarness):
    a = main.create_session()
    main.run(
        command(f"/open {a.id}"),
        MainCommandEntered("/new", of(a)),
        settle=True,
    )
    [child] = [sid for sid in main.sessions.ids() if sid != a.id]
    main.run(
        MainCommandEntered(f"/bg {child}", of(a)),
        MainCommandEntered(f"/bg {a.id}", of(a)),
        settle=True,
    )
    assert main.state.saved[-1].background == [child, a.id]
    assert main.state.saved[-1].windows == {}


def test_a_session_never_sends_another_to_the_background(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(command(f"/open {b.id}"), MainCommandEntered(f"/bg {b.id}", of(a)), settle=True)
    assert main.state.saved[-1].windows.keys() == {b.id}
    [result] = results(main, f"/bg {b.id}")
    assert isinstance(result, str) and "not_its_session" in result
    assert a.id in result


def test_a_session_never_closes_a_window_not_even_its_own(main: MainHarness):
    a = main.create_session()
    main.run(command(f"/open {a.id}"), MainCommandEntered(f"/close {a.id}", of(a)), settle=True)
    assert main.state.saved[-1].windows.keys() == {a.id}
    [result] = results(main, f"/close {a.id}")
    assert isinstance(result, str) and "human_only" in result


FIRST_BOX = BoxId("b1")  # the first box a Main shows


def attention_boxes(main: MainHarness) -> list[Box]:
    """The Notices asking for attention still on Main's Chat."""
    shown: dict[str, Box] = {}
    for update in main.main_screen:
        match update:
            case ShowBox(box=box) if box.title.endswith(ATTENTION_TITLE):
                shown[box.box_id] = box
            case HideBox(box_id=box_id):
                shown.pop(box_id, None)
            case _:
                pass
    return list(shown.values())


def test_with_room_a_session_brings_another_into_view(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(command(f"/open {a.id}"), MainCommandEntered(f"/open {b.id}", of(a)), settle=True)
    assert main.state.saved[-1].windows.keys() == {a.id, b.id}
    assert attention_boxes(main) == []


def test_without_room_it_stays_in_the_background_asking_for_attention(main: MainHarness):
    with_max(main, 1)
    a, b = main.create_session(), main.create_session()
    main.run(
        command(f"/open {a.id}"),
        command("/windows"),
        OrganizerConnected(),
        MainCommandEntered(f"/open {b.id}", of(a)),
        settle=True,
    )
    assert main.state.saved[-1].windows.keys() == {a.id}  # nobody displaced
    assert main.state.saved[-1].background == [b.id]
    [box] = attention_boxes(main)
    assert box.title.startswith(f"[{b.id}]") and f"session:{a.id}" in box.text
    [row] = [r for r in last_rows(main.organizers[0]) if r.session_id == b.id]
    assert row.attention and "asks for attention" in row_state(row)


def test_the_mark_goes_once_the_human_brings_it(main: MainHarness):
    with_max(main, 1)
    a, b = main.create_session(), main.create_session()
    main.run(
        command(f"/open {a.id}"),
        MainCommandEntered(f"/open {b.id}", of(a)),
        command(f"/open {b.id}"),
        settle=True,
    )
    assert main.state.saved[-1].windows.keys() == {b.id}
    assert attention_boxes(main) == []


def test_the_mark_goes_once_the_human_dismisses_it(main: MainHarness):
    with_max(main, 1)
    a, b = main.create_session(), main.create_session()
    main.run(
        command(f"/open {a.id}"),
        MainCommandEntered(f"/open {b.id}", of(a)),
        command("/windows"),
        OrganizerConnected(),
        BoxAnswered(FIRST_BOX, None),  # dismissed in Main's Chat
        settle=True,
    )
    assert attention_boxes(main) == []
    assert [u.box.box_id for u in main.main_screen if isinstance(u, ShowBox)] == [FIRST_BOX]
    [row] = [r for r in last_rows(main.organizers[0]) if r.session_id == b.id]
    assert not row.attention and row.place == "background"


# The fsource, confined as Main runs it; the LLM is fake.

INSTALLED = Path(__file__).parents[1] / "functions" / "windows"
WINDOWS = PieceId("function:windows")


@pytest.fixture
def tools(main: MainHarness, tmp_path: Path) -> Iterator[MainHarness]:
    """Main with the installation's fsource ``windows`` in its Profile, approved."""
    if abi_version() < 6:
        pytest.skip("needs Landlock ABI 6 (kernel 6.12)")
    main.describer = ToolLauncher(tmp_path)
    main.describer.start()
    main.threaded_turns = True
    shutil.copytree(INSTALLED, main.profile.folder / "functions" / "windows")
    descriptions = Descriptions(main.profile.folder.parents[1], main.describer, main.confinement)
    pieces = Quarantine(main.profile.folder, descriptions)
    ref = PieceRef("function", "windows")
    pieces.approve(ref, pieces.piece(ref).hash)
    yield main
    main.describer.stop()


def with_fsource(main: MainHarness) -> Session:
    """A Session as ``/new`` makes one: the fsource and the default Command sessions."""
    created = main.sessions.save_functions(main.create_session(), (WINDOWS,))
    return main.sessions.save_commands(created, BUILT_IN.root)


def test_the_llm_lists_the_sessions_with_the_fsource(tools: MainHarness):
    a, b = with_fsource(tools), tools.create_session()
    tools.llm = FakeLlm(tool_call("list_windows", "{}"), full_reply("", "listo"))
    tools.run(command(f"/open {a.id}"), InputReceived(a.id, "¿qué hay abierto?"), settle=True)
    [output] = outputs(tools.llm.requests[1])
    rows = {row["session_id"]: row for row in output["state"]["sessions"]}
    assert rows[a.id]["place"] == "view" and rows[a.id]["running"]
    assert rows[b.id]["place"] == "closed"


def test_the_fsource_sends_its_own_to_the_background_but_not_another(tools: MainHarness):
    a, b = with_fsource(tools), tools.create_session()
    tools.llm = FakeLlm(
        tool_call("background_window", json.dumps({"sid": b.id})),
        tool_call("background_window", "{}"),
        full_reply("", "listo"),
    )
    tools.run(
        command(f"/open {a.id}"),
        command(f"/open {b.id}"),
        InputReceived(a.id, "despeja"),
        settle=True,
    )
    [refused] = outputs(tools.llm.requests[1])
    assert "not_its_session" in refused["error"]
    assert outputs(tools.llm.requests[2])[-1] == {"result": "ok"}
    assert tools.state.saved[-1].windows.keys() == {b.id}
    assert tools.state.saved[-1].background == [a.id]


def test_the_fsource_brings_a_session_and_hears_whether_it_fit(tools: MainHarness):
    with_max(tools, 1)
    a, b = with_fsource(tools), tools.create_session()
    tools.llm = FakeLlm(tool_call("bring_window", json.dumps({"sid": b.id})), full_reply("", "ok"))
    tools.run(command(f"/open {a.id}"), InputReceived(a.id, "trae a B"), settle=True)
    [output] = outputs(tools.llm.requests[1])
    assert output == {"result": "ok", "state": {"place": "background", "attention": True}}
    assert len(attention_boxes(tools)) == 1


def test_no_tool_of_the_fsource_closes_a_window():
    names = {path.stem for path in INSTALLED.glob("*.py")}
    assert names == {"list_windows", "bring_window", "background_window"}


def test_leaving_the_profile_drops_the_mark_of_its_sessions(main: MainHarness):
    with_max(main, 1)
    a, b = main.create_session(), main.create_session()
    main.profiles.create("otro", main.profile)
    main.run(
        command(f"/open {a.id}"),
        MainCommandEntered(f"/open {b.id}", of(a)),
        command("/profile switch otro"),
        settle=True,
    )
    assert attention_boxes(main) == []


def test_a_session_of_another_profile_is_out_of_its_reach(main: MainHarness):
    a = main.create_session()
    main.sessions_join_profile = False
    other = main.create_session()  # in no list of the active Profile
    main.run(MainCommandEntered(f"/open {other.id}", of(a)), settle=True)
    [result] = results(main, f"/open {other.id}")
    assert isinstance(result, str) and "outside_profile" in result
    assert main.windows(other.id) == []
