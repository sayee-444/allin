"""Attaching and detaching Procesos adheridos (#113, ADR-0012, ADR-0013, ADR-0016): ``/attach``
starts one from an approved fproc, with a name and its own Carpeta de trabajo that outlives
it, and ``/detach`` lets it go. Main never starts one again by itself: one that crashes gives
a Notice and its methods fail. A shared file a Session asks for from outside its Carpeta de
trabajo needs a human's Approval; switching Profile, or Main stopping, detaches them; and a
new Main closes any a dead one left alive."""

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.core.errors import AttachedEnded, AttachedNotFound
from allin.core.ids import CommandName
from allin.core.origin import HUMAN, Link, Origin
from allin.main import attached
from allin.main.descriptions import Descriptions
from allin.main.events import BoxAnswered, InputReceived, MainCommandEntered
from allin.main.quarantine import PieceRef, Quarantine
from allin.main.sessions import Session
from allin.ui.screen import ShowBox
from tests.test_attached_launcher import BROKEN, COUNTER
from tests.test_dispatcher import MainHarness

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

GRANTER = '''
from allin.decorators import reach
from allin.fproc import Fproc


@reach(commands=["grant"])
class Granter(Fproc):
    def nothing(self) -> None:
        """Declares Otorgar."""
'''


@pytest.fixture
def main(tmp_path: Path) -> Iterator[MainHarness]:
    """Main with real descriptions and an approved package of fprocs, ``counting``."""
    harness = MainHarness(tmp_path)
    harness.describer = ToolLauncher(tmp_path, grace=1.0)
    harness.describer.start()
    folder = harness.profile.folder / "functions" / "counting"
    folder.mkdir(parents=True)
    for module, source in {"counter": COUNTER, "broken": BROKEN, "granter": GRANTER}.items():
        (folder / f"{module}.py").write_text(source)
    approve(harness, harness.describer)
    yield harness
    harness.describer.stop()


def approve(main: MainHarness, describer: ToolLauncher) -> None:
    descriptions = Descriptions(main.profile.folder.parents[1], describer, main.confinement)
    pieces = Quarantine(main.profile.folder, descriptions)
    ref = PieceRef("function", "counting")
    pieces.approve(ref, pieces.piece(ref).hash)


def from_session(session: Session) -> Origin:
    return Origin(chain=(*HUMAN.chain, Link("session", session.id)))


def notices(main: MainHarness) -> list[str]:
    return [
        u.box.title for u in main.main_screen if isinstance(u, ShowBox) and u.box.kind == "notice"
    ]


def confirms(main: MainHarness) -> list[ShowBox]:
    return [u for u in main.main_screen if isinstance(u, ShowBox) and u.box.kind == "confirm"]


def events(main: MainHarness, name: str) -> list[dict[str, object]]:
    return [dict(e.fields) for e in main.main_log() if e.event == name]


def records(main: MainHarness) -> list[dict[str, object]]:
    return json.loads((main.profiles.data_dir / "state" / "attached.json").read_text())["processes"]


def crash(main: MainHarness) -> None:
    """Crash ``notes`` and wait for Main to hear it from the thread that watches it."""
    with pytest.raises(AttachedEnded):
        main.dispatcher.attached.attached_fproc("notes").process.call("crash", {}, timeout=10)
    for thread in threading.enumerate():  # it tells Main as it ends
        if thread.name == "attached-notes":
            thread.join(5)


def dropped(_: object) -> None:
    """A step's value is dropped: a step gives none."""


def alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rpartition(")")[2].split()[0] != "Z"


def test_attach_starts_it_with_its_name_and_folder_and_detach_lets_it_go(main: MainHarness):
    pids: list[int] = []
    wheres: list[object] = []
    kept: list[list[str]] = []

    def look() -> None:
        process = main.dispatcher.attached.attached_fproc("notes").process
        pids.append(process.pid)
        wheres.append(process.call("where", {}, timeout=10).value)
        kept.append([str(r["name"]) for r in records(main)])

    chat = main.run(
        MainCommandEntered("/attach counter notes"),
        look,
        MainCommandEntered("/attach"),
        MainCommandEntered("/detach notes"),
        settle=True,
    )
    folder = main.profiles.data_dir / "attached" / "default" / "notes"
    assert wheres == [{"cwd": str(folder), "name": "notes", "file": "None"}]
    assert kept == [["notes"]]
    assert any(line.startswith("notes: counter, pid ") for line in chat)
    assert any("notes detached" in line for line in chat)
    assert not alive(pids[0])
    assert records(main) == []


def test_its_name_is_its_fprocs_by_default_and_one_running_cannot_be_attached_twice(
    main: MainHarness,
):
    chat = main.run(
        MainCommandEntered("/attach counter"),
        MainCommandEntered("/attach counter"),
        settle=True,
    )
    assert any("counter (counter) attached" in line for line in chat)
    assert any("attached_name_taken" in line for line in chat)


def test_attached_again_with_the_same_name_it_gets_its_folder_back(main: MainHarness):
    counts: list[object] = []

    def add() -> None:
        process = main.dispatcher.attached.attached_fproc("notes").process
        counts.append(process.call("add", {"n": 2}, timeout=10).value)

    main.run(
        MainCommandEntered("/attach counter notes"),
        add,
        MainCommandEntered("/detach notes"),
        MainCommandEntered("/attach counter notes"),
        add,
        settle=True,
    )
    assert counts == [2, 4]


def test_one_that_crashes_gives_a_notice_never_starts_again_and_its_methods_fail(
    main: MainHarness,
):
    crashed: list[str] = []

    def methods_fail() -> None:
        try:
            main.dispatcher.attached.attached_fproc("notes").process.call("add", {"n": 1}, 10)
        except AttachedEnded as error:
            crashed.append(error.code)

    chat = main.run(
        MainCommandEntered("/attach counter notes"),
        lambda: crash(main),
        methods_fail,
        MainCommandEntered("/attach"),
        settle=True,
    )
    assert crashed == ["attached_ended"]
    assert notices(main) == ["notes crashed"]
    assert events(main, "attached_crashed")[0]["exit_code"] == 3
    assert len(events(main, "attached")) == 1  # never started again
    assert any("notes: counter, crashed, exit code" in line for line in chat)


def test_one_that_crashed_comes_back_only_when_attached_again(main: MainHarness):
    main.run(
        MainCommandEntered("/attach counter notes"),
        lambda: crash(main),
        MainCommandEntered("/attach counter notes"),
        lambda: dropped(
            main.dispatcher.attached.attached_fproc("notes").process.call("add", {"n": 1}, 10)
        ),
        settle=True,
    )
    assert len(events(main, "attached")) == 2


def test_only_a_human_attaches_again_one_that_crashed_and_its_notice_says_how(
    main: MainHarness, user_folder: Path
):
    shared = user_folder / "notes.txt"
    shared.write_text("hello")
    session = main.create_session()
    attach = f"/attach counter notes --to={session.id} --file={shared}"
    chat = main.run(
        MainCommandEntered(attach),
        lambda: crash(main),
        MainCommandEntered(attach, from_session(session)),
        settle=True,
    )
    assert any("human_only" in line for line in chat)
    assert len(events(main, "attached")) == 1
    [notice] = [
        u.box for u in main.main_screen if isinstance(u, ShowBox) and u.box.kind == "notice"
    ]
    assert attach in notice.text


def test_an_fproc_that_is_not_approved_or_cannot_start_is_not_attached(main: MainHarness):
    chat = main.run(
        MainCommandEntered("/attach nothing"),
        MainCommandEntered("/attach broken"),
        settle=True,
    )
    assert any("fproc_not_offered" in line for line in chat)
    assert any("attach_failed" in line and "cannot start" in line for line in chat)
    assert records(main) == []


@pytest.fixture
def user_folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A folder of the user's, out of AllIn's data folder."""
    return tmp_path_factory.mktemp("user")


def test_a_session_attaching_a_file_from_outside_its_folder_needs_a_humans_approval(
    main: MainHarness, user_folder: Path
):
    outside = user_folder / "outside.txt"
    outside.write_text("hello")
    session = main.create_session()
    attach = f"/attach counter notes --to={session.id} --file={outside}"
    main.run(MainCommandEntered(attach, from_session(session)), settle=True)
    [box] = confirms(main)
    assert str(outside) in box.box.text and session.id in box.box.text
    assert events(main, "attached") == []
    main.run(
        MainCommandEntered(attach, from_session(session)),
        BoxAnswered(confirms(main)[-1].box.box_id, False),
        settle=True,
    )
    assert events(main, "attached") == []
    assert any("shared_file_not_approved" in str(e.get("result")) for e in events(main, "command"))
    main.run(
        MainCommandEntered(attach, from_session(session)),
        BoxAnswered(confirms(main)[-1].box.box_id, True),
        settle=True,
    )
    [attached] = events(main, "attached")
    assert attached["file"] == str(outside.resolve())


def test_a_file_in_the_sessions_folder_or_a_humans_needs_no_approval(
    main: MainHarness, user_folder: Path
):
    session = main.create_session()
    workspace = main.sessions.workspace(session.id)
    workspace.mkdir(parents=True)
    (workspace / "mine.txt").write_text("mine")
    outside = user_folder / "outside.txt"
    outside.write_text("hello")
    main.run(
        MainCommandEntered(
            f"/attach counter own --to={session.id} --file=mine.txt", from_session(session)
        ),
        MainCommandEntered(f"/attach counter theirs --file={outside}"),
        settle=True,
    )
    assert confirms(main) == []
    files = {e["name"]: e["file"] for e in events(main, "attached")}
    assert files == {"own": str((workspace / "mine.txt").resolve()), "theirs": str(outside)}


def test_a_file_in_a_sessions_folder_is_no_allins_own_with_the_data_inside_the_installation(
    main: MainHarness, monkeypatch: pytest.MonkeyPatch
):
    """As with ``./var``, the default: the data folder lies inside the installation."""
    monkeypatch.setattr(attached, "install_root", main.profiles.data_dir.resolve)
    session = main.create_session()
    workspace = main.sessions.workspace(session.id)
    workspace.mkdir(parents=True)
    (workspace / "mine.txt").write_text("mine")
    chat = main.run(MainCommandEntered(f"/attach counter --file={workspace / 'mine.txt'}"))
    assert [e["name"] for e in events(main, "attached")] == ["counter"], chat


def test_a_shared_file_is_never_allins_own(main: MainHarness):
    state = main.profiles.data_dir / "state" / "attached.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"processes": []}')
    chat = main.run(MainCommandEntered(f"/attach counter --file={state}"), settle=True)
    assert any("shared_file_refused" in line and "AllIn's own" in line for line in chat)


def test_a_session_gives_its_methods_only_to_itself_and_never_attaches_otorgar(
    main: MainHarness,
):
    session, other = main.create_session(), main.create_session()
    chat = main.run(
        MainCommandEntered(f"/attach counter --to={other.id}", from_session(session)),
        MainCommandEntered(f"/attach granter --to={session.id}", from_session(session)),
        MainCommandEntered("/attach granter"),
        settle=True,
    )
    assert sum("human_only" in line for line in chat) == 2
    assert [e["name"] for e in events(main, "attached")] == ["granter"]


def test_attach_typed_in_a_window_gives_its_methods_to_that_session(main: MainHarness):
    session = main.sessions.save_commands(main.create_session(), (CommandName("attach"),))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "@attach counter notes"),
        settle=True,
    )
    [attached] = events(main, "attached")
    assert attached["to"] == [session.id] and attached["origin"] == ["human"]


def test_switching_profile_detaches_the_outgoing_profiles(main: MainHarness):
    main.profiles.create("tesis", main.profile)
    pids: list[int] = []

    def gone() -> None:
        with pytest.raises(AttachedNotFound):
            main.dispatcher.attached.attached_fproc("notes")

    main.run(
        MainCommandEntered("/attach counter notes"),
        lambda: pids.append(main.dispatcher.attached.attached_fproc("notes").process.pid),
        MainCommandEntered("/profile switch tesis"),
        gone,
        settle=True,
    )
    [detached] = events(main, "detached")
    assert detached["reason"] == "profile switched"
    assert not alive(pids[0])


def test_main_stopping_detaches_them_all(main: MainHarness):
    pids: list[int] = []
    main.run(
        MainCommandEntered("/attach counter notes"),
        lambda: pids.append(main.dispatcher.attached.attached_fproc("notes").process.pid),
        settle=True,
    )
    assert not alive(pids[0])
    assert records(main) == []
    assert events(main, "detached")[0]["reason"] == "main stopped"


def test_a_new_main_closes_a_warden_a_dead_one_left_alive_but_nothing_else(
    main: MainHarness,
):
    dead_main = 999_999
    warden = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)  # from allin.confinement.warden import main",
            "code",
            str(dead_main),
            "3",
        ],
        start_new_session=True,
    )
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        state = main.profiles.data_dir / "state" / "attached.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        left = [
            {"name": "notes", "profile": "default", "pid": warden.pid, "main_pid": dead_main},
            {"name": "other", "profile": "default", "pid": stranger.pid, "main_pid": dead_main},
        ]
        state.write_text(json.dumps({"processes": left}))
        main.run()
        assert warden.wait(timeout=5) is not None
        assert stranger.poll() is None
        lost = {e["name"]: e["closed"] for e in events(main, "attached_lost")}
        assert lost == {"notes": True, "other": False}
        assert notices(main) == ["Procesos adheridos lost"]
        assert records(main) == []
    finally:
        stranger.kill()
        warden.kill()


def test_approving_its_fproc_with_another_reach_starts_it_again_with_it(main: MainHarness):
    pids: list[int] = []

    def widen() -> None:
        source = (main.profile.folder / "functions" / "counting" / "counter.py").read_text()
        source = source.replace(
            "class Counter(Fproc):", '@reach(read=["home"])\nclass Counter(Fproc):'
        )
        source = "from allin.decorators import reach\n" + source
        (main.profile.folder / "functions" / "counting" / "counter.py").write_text(source)

    main.run(
        MainCommandEntered("/attach counter notes"),
        lambda: pids.append(main.dispatcher.attached.attached_fproc("notes").process.pid),
        widen,
        MainCommandEntered("/piece show function:counting"),
        MainCommandEntered("/piece approve function:counting"),
        lambda: pids.append(main.dispatcher.attached.attached_fproc("notes").process.pid),
        settle=True,
    )
    first, again = events(main, "attached")
    assert again["restarted"] is True and first["restarted"] is False
    assert pids[0] != pids[1] and not alive(pids[0])
