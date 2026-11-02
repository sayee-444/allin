"""Otorgar (#111, ADR-0025): the Command session that gives any Session of the Profile what
exists and is approved there. Only a human gives it, ``/new`` never brings it, it never gives
itself, each use asks first and is logged with its whole chain, and the Session's Window marks
it while it has it."""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.ids import BoxId, CommandName, PieceId
from allin.core.origin import HUMAN, Link, Origin
from allin.ipc.messages import BoxShow, GrantHeld
from allin.main.descriptions import Descriptions
from allin.main.events import BoxAnswered, InputReceived, MainCommandEntered
from allin.main.quarantine import PieceRef, Quarantine
from allin.main.sessions import Session
from allin.ui.marks import GRANT_MARK
from allin.ui.screen import ShowBox, TitleMark
from allin.window.__main__ import MainListener, WindowRecord
from tests.llm_fakes import FakeLlm, full_reply, tool_call
from tests.test_dispatcher import MainHarness

BOSS = PieceId("function:boss")  # declares Otorgar
HELPER = PieceId("function:helper")
FIRST_BOX = BoxId("b1")
PACKAGES = {
    "boss": {
        "give": '''
from allin.channel import command
from allin.decorators import reach


@reach(commands=["grant"])
def give(sid: str, what: str) -> object:
    """Give a Session functions or Command sessions."""
    return command(f"@grant {sid} {what}")
''',
    },
    "helper": {
        "helper": '''
def helper() -> str:
    """Help."""
    return "ok"
''',
    },
    "rogue": {
        "take": '''
from allin.channel import command
from allin.decorators import reach


@reach(commands=["grant"])
def take(sid: str) -> object:
    """Declares Otorgar, not approved."""
    return command(f"@grant {sid} @send")
''',
    },
    "pending": {
        "pending": '''
def pending() -> str:
    """Not approved yet."""
    return "ok"
''',
    },
}


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    """Main with real descriptions: ``boss`` and ``helper`` approved, ``pending`` not."""
    if abi_version() < 6:
        pytest.skip("needs Landlock ABI 6 (kernel 6.12)")
    harness = MainHarness(tmp_path)
    harness.describer = ToolLauncher(tmp_path)
    harness.describer.start()
    harness.threaded_turns = True
    for name, modules in PACKAGES.items():
        folder = harness.profile.folder / "functions" / name
        folder.mkdir(parents=True)
        for module, source in modules.items():
            (folder / f"{module}.py").write_text(source)
    descriptions = Descriptions(
        harness.profile.folder.parents[1], harness.describer, harness.confinement
    )
    pieces = Quarantine(harness.profile.folder, descriptions)
    for name in ("boss", "helper"):
        ref = PieceRef("function", name)
        pieces.approve(ref, pieces.piece(ref).hash)
    yield harness
    harness.describer.stop()


def from_session(session: Session) -> Origin:
    return Origin(chain=(*HUMAN.chain, Link("session", session.id)))


def granter(main: MainHarness) -> Session:
    """A Session a human gave Otorgar, as ``/func add`` does."""
    session = main.create_session()
    session = main.sessions.save_functions(session, (BOSS,))
    return main.sessions.save_commands(session, (CommandName("grant"),))


def results(main: MainHarness, session: Session) -> list[object]:
    return [e.fields.get("result") for e in main.session_log(session) if e.event == "command"]


def confirms(main: MainHarness) -> list[ShowBox]:
    return [u for u in main.main_screen if isinstance(u, ShowBox) and u.box.kind == "confirm"]


def test_new_never_brings_it_even_if_named(main: MainHarness):
    main.run(MainCommandEntered("/new --bg -cs=grant,send"), settle=True)
    [session] = [main.sessions.load(sid) for sid in main.sessions.ids()]
    assert BOSS not in session.functions and HELPER in session.functions
    assert session.commands == (CommandName("send"),)
    notices = [u.box.title for u in main.main_screen if isinstance(u, ShowBox)]
    assert "Otorgar not given" in notices


def test_a_human_giving_its_fsource_gives_otorgar_with_a_notice_and_a_mark(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/func add {session.id} {BOSS}"),
        MainCommandEntered(f"/func remove {session.id} {BOSS}"),
        settle=True,
    )
    [window] = main.windows(session.id)
    held = [m.held for m in window.sent if isinstance(m, GrantHeld)]
    assert held[-2:] == [True, False], "marked when given, unmarked when taken away"
    notices = [m.title for m in window.sent if isinstance(m, BoxShow)]
    assert notices == ["Otorgar given"]
    assert main.sessions.load(session.id).commands == ()


def test_only_a_human_gives_it(main: MainHarness):
    a, b = granter(main), main.create_session()
    main.run(MainCommandEntered(f"/func add {b.id} {BOSS}", origin=from_session(a)), settle=True)
    assert BOSS not in main.sessions.load(b.id).functions
    [result] = results(main, a)
    assert str(result).startswith("error human_only")


def test_each_use_asks_first_and_is_logged_with_its_chain(main: MainHarness):
    a, b = granter(main), main.create_session()
    main.run(
        MainCommandEntered(f"/grant {b.id} {HELPER} @send", origin=from_session(a)),
        BoxAnswered(FIRST_BOX, True),
        settle=True,
    )
    [box] = confirms(main)
    assert f"/grant {b.id}" in box.box.text
    assert f"asked by human > session:{a.id}" in box.box.text, "the human sees who asks"
    given = main.sessions.load(b.id)
    assert HELPER in given.functions and CommandName("send") in given.commands
    [logged] = [e.fields for e in main.session_log(a) if e.event == "command"]
    assert logged["result"] == "ok" and logged["origin"] == ["human", f"session:{a.id}"]
    received = [e.fields for e in main.session_log(b) if e.event.endswith("_added")]
    assert [r["origin"] for r in received] == [["human", f"session:{a.id}"]] * 2
    assert [n.text for n in given.notifications] == [
        f"{HELPER} was given to you: its tools are yours",
        "@send was given to you: your tools may run it",
    ]


def test_a_no_gives_nothing(main: MainHarness):
    a, b = granter(main), main.create_session()
    main.sessions.save_functions(b, ())
    main.run(
        MainCommandEntered(f"/grant {b.id} {HELPER}", origin=from_session(a)),
        BoxAnswered(FIRST_BOX, False),
        settle=True,
    )
    assert main.sessions.load(b.id).functions == ()
    assert results(main, a) == ["rejected"]


@pytest.mark.parametrize("what", ["@grant", str(BOSS)])
def test_it_never_gives_otorgar(main: MainHarness, what: str):
    a, b = granter(main), main.create_session()
    main.run(MainCommandEntered(f"/grant {b.id} {what}", origin=from_session(a)), settle=True)
    assert CommandName("grant") not in main.sessions.load(b.id).commands
    [result] = results(main, a)
    assert str(result).startswith("error grant_not_givable")
    assert confirms(main) == [], "refused before a human is asked"


@pytest.mark.parametrize("what", ["exempt", "-y", "--devastation"])
def test_it_gives_no_exemption_nor_modes(main: MainHarness, what: str):
    a, b = granter(main), main.create_session(exempt=False)
    main.run(MainCommandEntered(f"/grant {b.id} {what}", origin=from_session(a)), settle=True)
    assert main.sessions.load(b.id).settings.exempt is False
    [result] = results(main, a)
    assert "invalid_arguments" in str(result) and "/grant <sid>" in str(result)


def test_it_gives_only_what_is_approved_in_the_profile(main: MainHarness):
    a, b = granter(main), main.create_session()
    main.run(
        MainCommandEntered(f"/grant {b.id} function:pending", origin=from_session(a)),
        MainCommandEntered(f"/grant {b.id} function:nope", origin=from_session(a)),
        settle=True,
    )
    first, second = results(main, a)
    assert str(first).startswith("error piece_in_quarantine")
    assert str(second).startswith("error piece_not_found")
    assert main.sessions.load(b.id).functions == (), "as it was"
    assert confirms(main) == []


def test_only_to_a_session_of_the_active_profile(main: MainHarness):
    a = granter(main)
    main.sessions_join_profile = False
    other = main.create_session()
    main.run(
        MainCommandEntered(f"/grant {other.id} @send", origin=from_session(a)),
        BoxAnswered(FIRST_BOX, True),
        settle=True,
    )
    assert main.sessions.load(other.id).commands == ()
    [result] = results(main, a)
    assert str(result).startswith("error outside_profile")


def test_devastation_answers_it_only_if_it_covers_the_receiver_too(main: MainHarness):
    a = granter(main)
    covered, uncovered = main.create_session(), main.create_session(exempt=False)
    main.devastation = True
    main.run(
        MainCommandEntered(f"/grant {covered.id} @send", origin=from_session(a)),
        MainCommandEntered(f"/grant {uncovered.id} @send", origin=from_session(a)),
        settle=True,
    )
    assert CommandName("send") in main.sessions.load(covered.id).commands
    assert main.sessions.load(uncovered.id).commands == ()
    [box] = confirms(main)
    assert uncovered.id in box.box.text


def test_a_tool_gives_through_otorgar(main: MainHarness):
    # --devastation covers both, so the Confirmation is answered and the Turn goes on.
    a, b = granter(main), main.create_session()
    main.devastation = True
    main.llm = FakeLlm(
        tool_call("give", json.dumps({"sid": b.id, "what": "@send"})), full_reply("", "dado")
    )
    main.run(InputReceived(a.id, "dale @send a B"), settle=True)
    assert CommandName("send") in main.sessions.load(b.id).commands
    [logged] = [e.fields for e in main.session_log(a) if e.event == "command"]
    assert logged["command"] == f"/grant {b.id} @send"
    assert logged["origin"] == ["human", f"session:{a.id}"]


def test_the_window_marks_it_on_its_border(tmp_path: Path):
    shown: list[object] = []
    listener = MainListener(shown.append, WindowRecord(tmp_path / "window.jsonl"))
    listener.on_message(GrantHeld(held=True))
    [*_, last] = [update for update in shown if isinstance(update, TitleMark)]
    assert GRANT_MARK in last.text
    listener.on_message(GrantHeld(held=False))
    [*_, last] = [update for update in shown if isinstance(update, TitleMark)]
    assert GRANT_MARK not in last.text


def test_its_approval_says_so(main: MainHarness):
    chat = main.run(MainCommandEntered(f"/piece show {BOSS}"))
    assert any("commands grant: declares Otorgar" in line for line in chat)


def test_its_fsource_in_quarantine_does_not_bring_it(main: MainHarness):
    session = main.create_session()
    main.run(MainCommandEntered(f"/func add {session.id} function:rogue"), settle=True)
    assert CommandName("grant") not in main.sessions.load(session.id).commands


def test_it_leaves_with_its_fsource_even_once_gone_from_the_profile(main: MainHarness):
    session = granter(main)
    shutil.rmtree(main.profile.folder / "functions" / "boss")
    main.run(MainCommandEntered(f"/func remove {session.id} {BOSS}"), settle=True)
    assert main.sessions.load(session.id).commands == ()
