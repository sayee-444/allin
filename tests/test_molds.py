"""Moldes and Crear (#112, ADR-0011, ADR-0013, ADR-0015): a Session born from an approved
Molde gets its Sysprompt, Ajustes, functions, list, initial history and task, never more than
whoever creates it has; and a Session creates Sysprompts and Moldes only through a human's box,
or in quarantine when ``--devastation`` answered for it."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.errors import ProfileStorageFailed
from allin.core.ids import BoxId, CommandName, MainId, PieceId
from allin.core.origin import HUMAN, Link, Origin
from allin.main.descriptions import Descriptions
from allin.main.events import BoxAnswered, InputReceived, MainCommandEntered
from allin.main.quarantine import PieceRef, Quarantine
from allin.main.sessions import MoldVersion, Session
from allin.ui.screen import ShowBox
from tests.test_dispatcher import MainHarness

HELPER = PieceId("function:helper")
BOSS = PieceId("function:boss")
FIRST_BOX = BoxId("b1")
CODER = """
sysprompt = "coder.txt"
functions = ["function:helper"]
commands = ["send", "server"]
task = "Resume el proyecto."

[settings]
title = "coder"
exempt = true
sampling = { temperature = 0.2 }

[[history]]
role = "user"
content = "Contesta siempre en castellano."

[[history]]
role = "assistant"
content = "De acuerdo."
"""
PACKAGES = {
    "helper": '''
def helper() -> str:
    """Help."""
    return "ok"
''',
    "boss": '''
from allin.channel import command
from allin.decorators import reach


@reach(commands=["grant"])
def boss(sid: str) -> object:
    """Declares Otorgar."""
    return command(f"@grant {sid} @send")
''',
}


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    """Main with real descriptions, ``helper`` and ``boss`` approved, and the Sysprompt
    ``coder.txt`` approved."""
    if abi_version() < 6:
        pytest.skip("needs Landlock ABI 6 (kernel 6.12)")
    harness = MainHarness(tmp_path)
    harness.hold_turns = True
    harness.describer = ToolLauncher(tmp_path)
    harness.describer.start()
    for name, source in PACKAGES.items():
        folder = harness.profile.folder / "functions" / name
        folder.mkdir(parents=True)
        (folder / f"{name}.py").write_text(source)
    (harness.profile.folder / "sysprompts" / "coder.txt").write_text("You code, $assistant_name.")
    for ref in (
        PieceRef("function", "helper"),
        PieceRef("function", "boss"),
        PieceRef("sysprompt", "coder.txt"),
    ):
        approve(harness, ref)
    yield harness
    harness.describer.stop()


def pieces(main: MainHarness) -> Quarantine:
    assert main.describer is not None
    descriptions = Descriptions(main.profile.folder.parents[1], main.describer, main.confinement)
    return Quarantine(main.profile.folder, descriptions)


def approve(main: MainHarness, ref: PieceRef) -> None:
    quarantine = pieces(main)
    quarantine.approve(ref, quarantine.piece(ref).hash)


def mold(main: MainHarness, name: str, text: str, *, approved: bool = True) -> None:
    folder = main.profile.folder / "molds"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.toml").write_text(text)
    if approved:
        approve(main, PieceRef("mold", name))


def from_session(session: Session) -> Origin:
    return Origin(chain=(*HUMAN.chain, Link("session", session.id)))


def born(main: MainHarness, before: set[str]) -> Session:
    [new] = [sid for sid in main.sessions.ids() if sid not in before]
    return main.sessions.load(new)


def results(main: MainHarness, session: Session) -> list[object]:
    return [e.fields.get("result") for e in main.session_log(session) if e.event == "command"]


def confirms(main: MainHarness) -> list[ShowBox]:
    return [u for u in main.main_screen if isinstance(u, ShowBox) and u.box.kind == "confirm"]


# Moldes


def test_a_mold_gives_its_recipe_and_sends_its_task(main: MainHarness):
    mold(main, "coder", CODER)
    main.run(MainCommandEntered("/new --bg --mold=coder"), settle=True)
    session = born(main, set())
    assert session.settings.sysprompt == "coder.txt" and session.settings.title == "coder"
    assert session.settings.sampling.temperature == 0.2
    assert session.functions == (HELPER,)
    assert session.commands == (CommandName("send"), CommandName("server"))
    [job] = main.held
    first = job.request.input
    assert first[0] == {"role": "developer", "content": "You code, AllIn."}
    assert first[1:3] == [
        {"role": "user", "content": "Contesta siempre en castellano."},
        {"role": "assistant", "content": "De acuerdo."},
    ], "the history opens the Conversation"
    assert first[-1]["content"] == "Resume el proyecto."
    [sent] = [e.fields for e in main.session_log(session) if e.event == "prompt"]
    assert sent["origin"] == ["human"]
    recipe = main.sessions.load_recipe(session)
    assert recipe is not None and recipe.functions == (HELPER,)
    approved = pieces(main).piece(PieceRef("mold", "coder")).hash
    assert recipe.mold == MoldVersion(piece=PieceId("mold:coder"), hash=approved)
    assert recipe.history[0]["content"] == "Contesta siempre en castellano."


def test_one_in_quarantine_is_never_used(main: MainHarness):
    mold(main, "coder", CODER, approved=False)
    chat = main.run(MainCommandEntered("/new --bg --mold=coder"), settle=True)
    assert list(main.sessions.ids()) == []
    assert any("piece_in_quarantine" in line for line in chat)


@pytest.mark.parametrize(
    ("text", "why"),
    [('[settings]\ncolour = "red"', "colour"), ("task = 3", "task"), ("[[history", "")],
)
def test_one_that_is_no_mold_says_why(main: MainHarness, text: str, why: str):
    mold(main, "bad", text)
    chat = main.run(MainCommandEntered("/new --bg --mold=bad"), settle=True)
    assert list(main.sessions.ids()) == []
    [error] = [line for line in chat if "mold_invalid" in line]
    assert why in error


def test_it_never_brings_otorgar(main: MainHarness):
    mold(main, "boss", 'functions = ["function:boss", "function:helper"]\ncommands = ["grant"]')
    main.run(MainCommandEntered("/new --bg --mold=boss"), settle=True)
    session = born(main, set())
    assert session.functions == (HELPER,) and session.commands == ()
    titles = [u.box.title for u in main.main_screen if isinstance(u, ShowBox)]
    assert titles.count("Otorgar not given") == 2


@pytest.mark.parametrize("name", ["", "../coder", "a\x00b"])
def test_a_name_that_is_no_mold_name_never_reaches_the_disk(main: MainHarness, name: str):
    chat = main.run(MainCommandEntered(f"/new --bg --mold={name}"), settle=True)
    assert list(main.sessions.ids()) == []
    assert any("invalid_arguments" in line and "--mold=<name>" in line for line in chat)


def test_what_it_names_must_be_approved(main: MainHarness):
    (main.profile.folder / "functions" / "pending").mkdir()
    (main.profile.folder / "functions" / "pending" / "pending.py").write_text(
        PACKAGES["helper"].replace("helper", "pending")
    )
    mold(main, "eager", 'functions = ["function:pending"]')
    mold(main, "nameless", 'sysprompt = "nope.txt"')
    chat = main.run(
        MainCommandEntered("/new --bg --mold=eager"),
        MainCommandEntered("/new --bg --mold=nameless"),
        settle=True,
    )
    assert list(main.sessions.ids()) == []
    assert any("piece_in_quarantine" in line and "function:pending" in line for line in chat)
    assert any("sysprompt_invalid" in line or "piece_" in line for line in chat[1:])


def test_without_sources_not_even_the_molds(main: MainHarness):
    mold(main, "coder", CODER)
    main.run(MainCommandEntered("/new --bg -sf=off --mold=coder"), settle=True)
    assert born(main, set()).functions == ()


def test_a_session_passes_on_only_what_it_has(main: MainHarness):
    mold(main, "coder", CODER)
    creator = main.create_session()
    creator = main.sessions.save_commands(creator, (CommandName("send"),))  # no functions
    main.run(MainCommandEntered("/new --bg --mold=coder", origin=from_session(creator)))
    session = born(main, {creator.id})
    assert session.functions == () and session.commands == (CommandName("send"),)
    [sent] = [e.fields for e in main.session_log(session) if e.event == "prompt"]
    assert sent["origin"] == ["human", f"session:{creator.id}"]
    assert sent["text"] == f"[from Session {creator.id}]\nResume el proyecto."


def test_its_task_goes_only_as_the_creator_may_send(main: MainHarness):
    mold(main, "coder", CODER)
    mute = main.create_session()
    mute = main.sessions.save_commands(mute, ())
    main.sessions_join_profile = False
    outside = main.sessions.save_commands(main.create_session(), (CommandName("send"),))
    main.run(
        MainCommandEntered("/new --bg --mold=coder", origin=from_session(mute)),
        MainCommandEntered("/new --bg --mold=coder", origin=from_session(outside)),
        settle=True,
    )
    assert set(main.sessions.ids()) == {mute.id, outside.id}, "nothing created"
    assert str(results(main, mute)[0]).startswith("error session_command_not_listed")
    assert str(results(main, outside)[0]).startswith("error outside_profile")


def test_a_session_without_exemption_creates_sessions_without_it(main: MainHarness):
    mold(main, "coder", CODER)
    creator = main.create_session(exempt=False)
    creator = main.sessions.save_commands(creator, (CommandName("send"),))
    main.run(MainCommandEntered("/new --bg --mold=coder", origin=from_session(creator)))
    child = born(main, {creator.id})
    assert child.settings.exempt is False
    main.run(MainCommandEntered("/new --bg --mold=coder"), settle=True)  # a human's keeps it
    assert born(main, {creator.id, child.id}).settings.exempt is True


def test_only_its_own_mains_sessions_pass_on_the_exemption(main: MainHarness):
    mold(main, "coder", CODER)
    foreign = main.create_session(owner=MainId("m-other"))
    foreign = main.sessions.save_commands(foreign, (CommandName("send"),))
    main.run(MainCommandEntered("/new --bg --mold=coder", origin=from_session(foreign)))
    child = born(main, {foreign.id})
    assert child.settings.exempt is False and child.functions == ()


def test_create_is_not_in_the_default_list(main: MainHarness):
    main.run(MainCommandEntered("/new --bg"), settle=True)
    assert CommandName("create") not in born(main, set()).commands


# Crear


@pytest.mark.parametrize(
    "typed", ["/create sysprompt:mine.txt {}", "@create sysprompt:mine.txt\n{}"]
)
def test_a_human_creates_it_approved(main: MainHarness, typed: str):
    text = typed.format("Hola\nmundo")
    session = main.create_session()
    event = MainCommandEntered(text) if text.startswith("/") else InputReceived(session.id, text)
    main.run(event, settle=True)
    piece = pieces(main).piece(PieceRef("sysprompt", "mine.txt"))
    assert piece.state == "approved" and piece.provenance.by == "user"
    assert piece.path.read_text() == "Hola\nmundo", "no newline before it"
    assert confirms(main) == []


def test_a_human_rewriting_it_makes_it_theirs(main: MainHarness):
    a = main.create_session()
    main.devastation = True
    main.run(MainCommandEntered("/create sysprompt:mine.txt hola", origin=from_session(a)))
    main.run(MainCommandEntered("/create sysprompt:mine.txt adiós"))
    piece = pieces(main).piece(PieceRef("sysprompt", "mine.txt"))
    assert piece.provenance.by == "user" and piece.state == "approved"


def test_a_damaged_record_of_creators_is_an_error_not_the_users(main: MainHarness):
    a = main.create_session()
    main.devastation = True
    main.run(MainCommandEntered("/create sysprompt:mine.txt hola", origin=from_session(a)))
    (main.profile.folder / "created.json").write_text("{roto")
    with pytest.raises(ProfileStorageFailed):
        pieces(main).piece(PieceRef("sysprompt", "mine.txt"))


def test_from_a_session_one_box_confirms_and_approves(main: MainHarness):
    a = main.create_session()
    main.run(
        MainCommandEntered(f"/create mold:coder {CODER}", origin=from_session(a)),
        BoxAnswered(FIRST_BOX, True),
        settle=True,
    )
    [box] = confirms(main)
    assert "Resume el proyecto." in box.box.text, "the human sees all of it"
    piece = pieces(main).piece(PieceRef("mold", "coder"))
    assert piece.state == "approved"
    assert piece.provenance.by == "session" and piece.provenance.who == a.id
    assert results(main, a) == ["ok"]


def test_a_no_creates_nothing(main: MainHarness):
    a = main.create_session()
    main.run(
        MainCommandEntered("/create sysprompt:mine.txt hola", origin=from_session(a)),
        BoxAnswered(FIRST_BOX, False),
        settle=True,
    )
    assert not (main.profile.folder / "sysprompts" / "mine.txt").exists()
    assert results(main, a) == ["rejected"]


def test_with_devastation_it_waits_in_quarantine(main: MainHarness):
    a = main.create_session()
    main.devastation = True
    main.run(MainCommandEntered("/create sysprompt:mine.txt hola", origin=from_session(a)))
    assert confirms(main) == []
    quarantine = pieces(main)
    piece = quarantine.piece(PieceRef("sysprompt", "mine.txt"))
    assert piece.state == "quarantined"
    assert piece.provenance.by == "session" and piece.provenance.who == a.id
    assert results(main, a) == ["ok"]
    assert "sysprompt:mine.txt" in quarantine.approve_own().left_out, "never in bulk"


def test_what_is_no_piece_is_refused_before_asking(main: MainHarness):
    a = main.create_session()
    main.run(
        MainCommandEntered("/create mold:bad task = 3", origin=from_session(a)),
        MainCommandEntered("/create sysprompt:bad.txt Hola $nadie", origin=from_session(a)),
        MainCommandEntered("/create function:x hola", origin=from_session(a)),
        settle=True,
    )
    assert confirms(main) == []
    first, second, third = results(main, a)
    assert str(first).startswith("error mold_invalid")
    assert str(second).startswith("error sysprompt_invalid")
    assert str(third).startswith("error invalid_arguments")


def test_a_human_reads_and_approves_a_mold_with_piece(main: MainHarness):
    mold(main, "coder", CODER, approved=False)
    chat = main.run(
        MainCommandEntered("/piece show mold:coder"),
        MainCommandEntered("/piece approve mold:coder"),
        MainCommandEntered("/piece"),
    )
    assert any('task = "Resume el proyecto."' in line for line in chat), "it shows its text"
    assert pieces(main).piece(PieceRef("mold", "coder")).state == "approved"
