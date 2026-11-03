"""The Subperfil of an fproc as a human sees and sets it (#123, ADR-0026): its Exención, under
which ``--devastation`` answers only what stays inside it; its mark on the border; its Sessions
folded under the instance in the Organizer; and ``/subprofile``. The processes run for real on
this kernel; the LLM is fake."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import JsonValue

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.ids import AttachedName, BoxId, CommandName, SessionId
from allin.core.origin import HUMAN, Link, Origin
from allin.ipc.messages import Coverage, OrganizerRow
from allin.main.descriptions import Descriptions
from allin.main.events import (
    BoxAnswered,
    MainCommandEntered,
    MainEvent,
    SessionCommandAsked,
    SessionCommandResult,
    WindowConnected,
)
from allin.main.quarantine import PieceRef, Quarantine
from allin.ui.marks import DEVASTATION_INSIDE_MARK
from allin.ui.organizer_view import OrganizerView, windows_list
from allin.ui.screen import TitleMark
from allin.window.__main__ import MainListener, WindowRecord
from tests.llm_fakes import FakeLlm, full_reply, tool_call
from tests.test_attached import from_session
from tests.test_dispatcher import TURNS_ENDED, MainHarness
from tests.test_fproc_initiative import (
    GUIDE,
    LONER,
    act,
    enter,
    guide_heard,
    heard_state,
    hearing,
    subprofile_sessions,
)
from tests.test_tool_confirm import asked, box_shown

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

AFTER_NOTICE = BoxId("b2")  # the first Confirmation, after the box that says --devastation

INSIDE = '''
from allin.decorators import confirm, reach


@confirm
@reach(write=["work"])
def inside() -> str:
    """Writes in its Carpeta de trabajo alone."""
    return "inside"
'''

OUTSIDE = '''
from allin.decorators import confirm, reach


@confirm
@reach(read=["home"])
def outside() -> str:
    """Reads the human's home."""
    return "outside"
'''

WORKER = """
functions = ["function:inside", "function:outside"]
commands = ["send"]

[settings]
title = "worker"
"""


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    """Main with the approved fproc ``guide``, whose Molde ``helper`` gives its Sessions two
    tools that ask a Confirmation: ``inside`` and ``outside``."""
    harness = MainHarness(tmp_path)
    harness.describer = ToolLauncher(tmp_path, grace=1.0)
    harness.describer.start()
    harness.threaded_turns = True
    functions = harness.profile.folder / "functions"
    (functions / "guides" / "molds").mkdir(parents=True)
    (functions / "guides" / "guide.py").write_text(GUIDE)
    (functions / "guides" / "rival.py").write_text(GUIDE.replace("class Guide", "class Rival"))
    (functions / "guides" / "loner.py").write_text(LONER)
    (functions / "guides" / "molds" / "helper.toml").write_text(WORKER)
    for name, source in (("inside", INSIDE), ("outside", OUTSIDE)):
        (functions / name).mkdir()
        (functions / name / f"{name}.py").write_text(source)
    descriptions = Descriptions(tmp_path, harness.describer, harness.confinement)
    pieces = Quarantine(harness.profile.folder, descriptions)
    for package in ("guides", "inside", "outside"):
        ref = PieceRef("function", package)
        pieces.approve(ref, pieces.piece(ref).hash)
    yield harness
    harness.describer.stop()


def arrives(main: MainHarness, event: Callable[[], MainEvent]) -> Callable[[], None]:
    """A step: the event ``event`` makes, known only once the run is under way, comes in."""

    def step() -> None:
        inbox = main.servers.inbox
        assert inbox is not None
        inbox.put(event())

    return step


def answered_by(main: MainHarness, sid: str) -> list[JsonValue]:
    """Who answered each Confirmation of the Session by itself."""
    log = main.session_log(main.sessions.load(sid))
    return [e.fields["by"] for e in log if e.event == "confirmation_auto_answered"]


def test_with_the_exemption_devastation_answers_what_stays_inside_and_asks_the_rest(
    main: MainHarness,
):
    main.llm = FakeLlm(
        full_reply("", "hola, Juan"),
        tool_call("inside", "{}"),
        tool_call("outside", "{}", "call_2"),
        full_reply("", "hecho"),
    )
    heard: list[JsonValue] = []
    main.run(
        MainCommandEntered("/devastation on"),
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        MainCommandEntered("/subprofile guide exempt=on"),
        lambda: heard.extend(guide_heard(main)),
        act(main, lambda: f"@send {heard_state(heard, 0)} hazlo"),
        box_shown(main),
        BoxAnswered(AFTER_NOTICE, True),
        TURNS_ENDED,
        settle=True,
    )
    sid = heard_state(heard, 0)
    assert answered_by(main, sid) == ["--devastation inside"]
    [question] = asked(main)
    assert question.title == f"[{sid}] run outside?", "what reaches out of it asks the human"


def test_without_the_exemption_the_fproc_in_its_chain_asks_the_human(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"), tool_call("inside", "{}"), full_reply("", "ok"))
    heard: list[JsonValue] = []
    main.run(
        MainCommandEntered("/devastation on"),
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)),
        act(main, lambda: f"@send {heard_state(heard, 0)} hazlo"),
        box_shown(main),
        BoxAnswered(AFTER_NOTICE, True),
        TURNS_ENDED,
        settle=True,
    )
    sid = heard_state(heard, 0)
    assert answered_by(main, sid) == []
    assert [box.title for box in asked(main)] == [f"[{sid}] run inside?"]


def test_the_border_of_its_sessions_says_devastation_reaches_inside_only(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    main.run(
        MainCommandEntered("/devastation on"),
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        MainCommandEntered("/subprofile guide exempt=on"),
        enter(main, lambda: f"/open {subprofile_sessions(main)[0]}"),
        arrives(main, lambda: WindowConnected(SessionId(subprofile_sessions(main)[0]))),
        MainCommandEntered("/subprofile guide exempt=off"),
        settle=True,
    )
    [window] = main.windows(subprofile_sessions(main)[0])
    marks = [m for m in window.sent if isinstance(m, Coverage)]
    assert marks[0] == Coverage(covered=False, inside=True)
    assert marks[-1] == Coverage(covered=False, inside=False)


def test_subprofile_lists_them_and_sets_exemption_and_turns_only_for_a_human(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    a = main.create_session()
    chat = main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        MainCommandEntered("/subprofile guide exempt=on turns=2"),
        MainCommandEntered("/subprofile"),
        MainCommandEntered("/subprofile guide turns=0"),
        MainCommandEntered("/subprofile nobody exempt=on"),
        MainCommandEntered("/subprofile guide exempt=off", from_session(a)),
        settle=True,
    )
    listed = chat[chat.index("subprofiles: 1") + 1]
    assert listed == "  guide: 1 session · exempt · turns 2"
    assert any("/subprofile guide turns=0: error invalid_arguments" in line for line in chat)
    assert any("subprofile_not_found" in line for line in chat)
    assert any("human_only" in line for line in chat)
    record = (main.profile.folder / "subprofiles" / "guide.json").read_text()
    assert '"exempt": true' in record and '"turns": 2' in record


def test_the_organizer_folds_its_sessions_under_the_instance(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    a = main.create_session()
    main.profiles.add_session(main.profile, a.id)
    chat = main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        MainCommandEntered("/subprofile guide exempt=on"),
        MainCommandEntered("/windows list"),
        settle=True,
    )
    [sid] = subprofile_sessions(main)
    at = chat.index("subprofile guide, exempt: 1")
    assert chat[at + 1].startswith(f"  {sid} worker — in the background")


def test_a_view_folds_each_subprofile_apart_and_unfolds_it_on_its_own():
    rows = [
        OrganizerRow(session_id="a1", title="a1", place="view"),
        OrganizerRow(
            session_id="g1", title="g1", place="background", subprofile=AttachedName("guide")
        ),
        OrganizerRow(session_id="g2", title="g2", place="closed", subprofile=AttachedName("guide")),
        OrganizerRow(session_id="c1", title="c1", place="closed"),
    ]
    view = OrganizerView()
    view.set_rows(rows)
    texts = [line.text.strip() for line in view.lines(100)]
    assert "▸ Subprofile guide (2)" in texts
    assert not any(text.startswith("g1") for text in texts)
    view.key("KEY_DOWN")  # the fold of the closed ones
    view.key("KEY_DOWN")  # the fold of guide's
    view.key("\n")
    texts = [line.text.strip() for line in view.lines(100)]
    assert any(text.startswith("g1 ") for text in texts) and any(t.startswith("g2 ") for t in texts)
    assert not any(text.startswith("c1 ") for text in texts), "the closed fold stays folded"
    assert "subprofile guide: 2" in windows_list(rows)


def test_a_session_lists_only_the_sessions_of_its_own_space(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    a = main.create_session()
    a = main.sessions.save_commands(a, (CommandName("windows"),))
    main.profiles.add_session(main.profile, a.id)
    results: list[SessionCommandResult] = []

    def listing(origin: Origin) -> SessionCommandAsked:
        def answer(result: SessionCommandResult) -> bool:
            results.append(result)
            return True

        return SessionCommandAsked(a.id, "@windows", origin, answer)

    main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        listing(from_session(a)),
        settle=True,
    )
    [sid] = subprofile_sessions(main)
    [result] = results
    assert isinstance(result.state, dict)
    sessions = result.state["sessions"]
    assert isinstance(sessions, list)
    listed = [row["session_id"] for row in sessions if isinstance(row, dict)]
    assert sid not in listed and a.id in listed


def test_a_renamed_session_stays_in_its_subprofile(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        enter(main, lambda: f"/session id {subprofile_sessions(main)[0]} bot1"),
        settle=True,
    )
    assert subprofile_sessions(main) == ["bot1"]


def test_a_window_marks_on_its_border_that_devastation_answers_only_inside(tmp_path: Path):
    shown: list[object] = []
    listener = MainListener(shown.append, WindowRecord(tmp_path / "window.jsonl"))
    listener.on_message(Coverage(covered=False, inside=True))
    [*_, last] = [update for update in shown if isinstance(update, TitleMark)]
    assert last.text == DEVASTATION_INSIDE_MARK
    listener.on_message(Coverage(covered=False))
    [*_, last] = [update for update in shown if isinstance(update, TitleMark)]
    assert last.text == ""


def test_its_sessions_create_no_session_and_attach_no_process(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    before = set(main.sessions.ids())

    def inside() -> Origin:
        sid = subprofile_sessions(main)[0]
        return Origin(chain=(*HUMAN.chain, Link("process", "guide"), Link("session", sid)))

    chat = main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        enter(main, lambda: "/new", inside),
        enter(main, lambda: "/attach guide second", inside),
        settle=True,
    )
    assert len([line for line in chat if "made_outside_subprofile" in line]) == 2
    assert set(main.sessions.ids()) - before == set(subprofile_sessions(main))
    assert not any(line.startswith("second (guide) attached") for line in chat)


# The boundary holds whoever acts, and whatever comes between.


def test_an_fproc_without_a_subprofile_acts_on_no_session_of_the_profile(main: MainHarness):
    a = main.create_session()
    main.profiles.add_session(main.profile, a.id)
    heard: list[JsonValue] = []
    chat = main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered("/attach loner"),
        act(main, "@new helper", "loner"),
        hearing(main, 1, "loner"),
        lambda: heard.extend(guide_heard(main, "loner")),
        MainCommandEntered(
            f"/close {a.id}", Origin(chain=(*HUMAN.chain, Link("process", "loner")))
        ),
        settle=True,
    )
    assert "no_subprofile" in str(heard[0])
    assert any(f"/close {a.id}: error human_only" in line for line in chat)
    assert main.state.saved[-1].windows.keys() == {a.id}


def test_what_acts_for_a_session_of_the_subprofile_stays_in_it(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    a = main.create_session()
    main.profiles.add_session(main.profile, a.id)

    def through_loner() -> Origin:
        sid = subprofile_sessions(main)[0]
        links = (Link("process", "guide"), Link("session", sid), Link("process", "loner"))
        return Origin(chain=(*HUMAN.chain, *links))

    chat = main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        enter(main, lambda: f"/send {a.id} desde dentro", through_loner),
        settle=True,
    )
    assert any("outside_subprofile" in line for line in chat), "a method of another fproc"
    mine = main.sessions.load_conversation(a)
    assert mine is None or not mine.turns


def test_another_fproc_attached_with_its_name_never_gets_its_subprofile(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    heard: list[JsonValue] = []
    chat = main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        lambda: heard.extend(guide_heard(main)),
        MainCommandEntered("/detach guide"),
        MainCommandEntered("/attach rival guide"),
        MainCommandEntered("/attach loner guide"),
        act(main, lambda: f"@send {heard_state(heard, 0)} hola"),
        hearing(main, 1),
        lambda: heard.extend(guide_heard(main)),
        settle=True,
    )
    assert any("/attach rival guide: error subprofile_of_another" in line for line in chat)
    assert "no_subprofile" in str(heard[2]), "the loner named guide reaches none of them"


def test_switching_profile_freezes_the_subprofile_and_coming_back_finds_it(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    main.profiles.create("tesis", main.profile)
    chat = main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        MainCommandEntered("/profile switch tesis"),
        MainCommandEntered("/subprofile"),
        MainCommandEntered("/profile switch default"),
        MainCommandEntered("/subprofile"),
        settle=True,
    )
    first = chat.index("subprofiles: 0")
    assert chat[chat.index("subprofiles: 1", first) + 1] == "  guide: 1 session · turns 1"


def test_a_human_opens_one_of_its_sessions_and_it_stays_out_of_the_profile(main: MainHarness):
    main.llm = FakeLlm(full_reply("", "hola"))
    main.run(
        MainCommandEntered("/attach guide"),
        hearing(main, 2),
        TURNS_ENDED,
        enter(main, lambda: f"/open {subprofile_sessions(main)[0]}"),
        settle=True,
    )
    [sid] = subprofile_sessions(main)
    assert len(main.windows(sid)) == 1
    assert sid not in main.profiles.sessions(main.profile)
