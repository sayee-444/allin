"""``/piece`` and ``/session workspace``: the human's side of Quarantine and Approval
(ADR-0013, ADR-0016, #104). Functions are described for real on this kernel."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.confinement.reach import install_root
from allin.core.origin import Origin
from allin.main.descriptions import Descriptions
from allin.main.events import MainCommandEntered
from allin.main.quarantine import Quarantine
from tests.test_dispatcher import MainHarness

needs_landlock = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6")

RLO = chr(0x202E)  # written with chr(), so this file holds none
FETCH = f'''
from allin.decorators import reach


@reach(read=["home/Documents"], net=[443])
def fetch(url: str) -> str:
    """Fetch a page."""
    return url  # the comment hides{RLO} nothing on the screen
'''
DRAW = '''
from allin.decorators import reach


@reach(display=True)
def draw() -> None:
    """Draw."""
'''


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    main = MainHarness(tmp_path)
    main.describer = ToolLauncher(tmp_path)
    main.describer.start()
    yield main
    main.describer.stop()


def package(main: MainHarness, name: str, source: str) -> Path:
    folder = main.profile.folder / "functions" / name
    folder.mkdir(parents=True)
    (folder / f"{name}.py").write_text(source)
    return folder


def offered(main: MainHarness) -> list[str]:
    assert main.describer is not None
    data_dir = main.profile.folder.parents[1]
    descriptions = Descriptions(data_dir, main.describer, main.confinement)
    return [f.description.module for f in Quarantine(main.profile.folder, descriptions).offered()]


def run_as(main: MainHarness, origin: Origin, text: str) -> list[str]:
    return main.run(MainCommandEntered(text, origin=origin))


@needs_landlock
def test_list_shows_each_piece_with_its_state_and_who_wrote_it(main: MainHarness):
    package(main, "fetch", FETCH)
    (main.sysprompts / "mine.txt").write_text("Mine.")
    chat = main.run(MainCommandEntered("/piece"))
    states = {
        line.split()[0]: line.split(maxsplit=1)[1]
        for line in chat
        if line.startswith(("function:", "sysprompt:"))
    }
    assert states == {
        "function:fetch": "in quarantine · written by you",
        "sysprompt:default.txt": "approved · written by you",  # as AllIn installs it
        "sysprompt:mine.txt": "in quarantine · written by you",
    }


@needs_landlock
def test_the_screen_shows_the_reach_first_and_marks_hidden_characters(main: MainHarness):
    package(main, "fetch", FETCH)
    chat = main.run(MainCommandEntered("/piece show function:fetch"))
    reach_at = chat.index("  ! read home/Documents")
    code_at = chat.index("code:")
    assert chat.index("  ! network TCP 443 · UDP open") < code_at
    assert reach_at < code_at
    assert any("hides<U+202E> nothing" in line for line in chat[code_at:])


@needs_landlock
def test_a_piece_is_approved_only_after_its_screen_and_then_offered(main: MainHarness):
    package(main, "fetch", FETCH)
    refused = main.run(MainCommandEntered("/piece approve function:fetch"))
    assert any("piece_not_shown" in line for line in refused)
    assert offered(main) == []
    main.run(
        MainCommandEntered("/piece show function:fetch"),
        MainCommandEntered("/piece approve function:fetch --reach fetch:read= --confirm fetch"),
    )
    assert offered(main) == ["fetch"]
    log = main.main_log_path.read_text()
    assert '"piece_approved"' in log and '"confirm":["fetch"]' in log.replace(" ", "")


@needs_landlock
def test_neither_another_origin_nor_y_nor_devastation_approves(main: MainHarness):
    package(main, "fetch", FETCH)
    main.devastation = True
    chat = run_as(main, "session", "/piece approve --all")
    assert any("human_only" in line for line in chat)
    chat = main.run(
        MainCommandEntered("/piece show function:fetch"),
        MainCommandEntered("/piece approve function:fetch -y"),
    )
    assert any("invalid_arguments" in line for line in chat)
    assert offered(main) == []


@needs_landlock
def test_approve_all_says_what_it_left_out(main: MainHarness):
    package(main, "fetch", FETCH)
    package(main, "draw", DRAW)
    chat = main.run(MainCommandEntered("/piece approve --all"))
    assert any("approved 1: function:fetch" in line for line in chat)
    assert "  left out function:draw: draw acts as you; /piece show function:draw" in chat


def test_only_a_human_points_the_workspace_and_never_into_allin(
    main: MainHarness, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
):
    session = main.create_session()
    repo = tmp_path_factory.mktemp("repo")  # out of the data folder, which is tmp_path
    refused = run_as(main, "session", f"/session workspace {session.id} {repo}")
    assert any("human_only" in line for line in refused)
    assert main.sessions.workspace(session.id) == tmp_path / "workspaces" / session.id
    main.run(MainCommandEntered(f"/session workspace {session.id} {repo}"))
    assert main.sessions.workspace(session.id) == repo.resolve()
    for inside in (tmp_path / "sessions", install_root() / "src"):
        chat = main.run(MainCommandEntered(f"/session workspace {session.id} {inside}"))
        assert any("workspace_refused" in line and "inside AllIn" in line for line in chat)
    shown = main.run(MainCommandEntered(f"/session {session.id}"))
    assert f"[{session.id}] workspace={repo.resolve()}" in shown
    main.run(MainCommandEntered(f"/session workspace {session.id} default"))
    assert main.sessions.workspace(session.id) == tmp_path / "workspaces" / session.id


def test_main_says_on_start_what_waits_for_an_approval(main: MainHarness):
    (main.sysprompts / "mine.txt").write_text("Mine.")
    chat = main.run()
    assert "1 piece(s) in quarantine or rejected: /piece to see them" in chat
