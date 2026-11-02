from pathlib import Path

import pytest

from allin.core.ids import ROOT_MAIN_ID, SessionId
from allin.core.settings import SessionSettings
from allin.main.events import MainCommandEntered, WindowClosed
from allin.main.prompts import Prompt
from allin.main.sessions import SessionStore
from allin.main.state import MainState, MainStateStore
from allin.ui.screen import ShowBox
from tests.pieces import approve_sysprompt
from tests.test_dispatcher import MainHarness, restored


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.sessions_join_profile = False  # these tests make each Profile's list themselves
    return harness


def test_new_puts_the_session_in_the_active_profiles_list(main: MainHarness):
    main.run(MainCommandEntered("/new"))
    [(session_id, _)] = main.opened
    assert main.profiles.sessions(main.profile) == [session_id]


def test_open_puts_a_session_of_another_profile_in_the_active_ones_list(main: MainHarness):
    session = main.create_session()
    assert main.profiles.sessions(main.profile) == []
    main.run(MainCommandEntered(f"/open {session.id}"))
    assert main.profiles.sessions(main.profile) == [session.id]


def test_profile_lists_the_profiles_and_marks_the_active_one(main: MainHarness):
    main.profiles.create("tesis-2026", main.profile)
    chat = main.run(MainCommandEntered("/profile"))
    assert "default (active)" in chat
    assert "tesis-2026" in chat


def test_profile_new_creates_a_profile_with_the_active_ones_sysprompts(main: MainHarness):
    (main.sysprompts / "default.txt").write_text("Mine, $assistant_name.")
    approve_sysprompt(main.sysprompts, "default.txt")
    chat = main.run(MainCommandEntered("/profile new tesis-2026"))
    assert main.profiles.names() == ["default", "tesis-2026"]
    created = main.profiles.open("tesis-2026")
    assert (created.sysprompts / "default.txt").read_text() == "Mine, $assistant_name."
    assert any("profile tesis-2026 created" in line for line in chat)


def test_profile_new_without_a_name_generates_one(main: MainHarness):
    main.run(MainCommandEntered("/profile new"))
    [_, generated] = sorted(main.profiles.names(), key=lambda name: name != "default")
    assert len(generated) == 8


@pytest.mark.parametrize(
    ("command", "code"),
    [
        ("/profile new X!", "profile_name_invalid"),
        ("/profile new default", "profile_exists"),
        ("/profile new a b", "invalid_arguments"),
        ("/profile rename x", "invalid_arguments"),
    ],
)
def test_a_wrong_profile_command_is_an_error_and_creates_nothing(
    main: MainHarness, command: str, code: str
):
    chat = main.run(MainCommandEntered(command))
    assert any(code in line for line in chat)
    assert main.profiles.names() == ["default"]


def test_a_new_session_is_born_from_the_active_profiles_sysprompts(main: MainHarness):
    (main.seed / "default.txt").write_text("Seed only.")  # the installation's no longer count
    (main.sysprompts / "default.txt").write_text("Profile: $assistant_name and $username.")
    approve_sysprompt(main.sysprompts, "default.txt")
    main.run(MainCommandEntered("/new"))
    [(session_id, _)] = main.opened
    conversation = main.sessions.load_conversation(main.sessions.load(session_id))
    assert conversation is not None
    assert conversation.sysprompt == "Profile: AllIn and ana."


A = SessionId("aaaaaa")
B = SessionId("bbbbbb")


def test_a_data_dir_from_before_profiles_starts_as_default_without_losing_anything(
    tmp_path: Path,
):
    sessions = SessionStore(tmp_path / "sessions", iter([A, B]).__next__)
    for _ in range(2):
        sessions.create(ROOT_MAIN_ID, SessionSettings(), "I am AllIn.")
    MainStateStore(tmp_path / "state" / "root.json").save(
        MainState(
            windows={A: 4242, B: 4243},
            drafts={A: ["typing"]},
            queue=[Prompt(B, "b1")],
            running=[Prompt(A, "a1")],
        )
    )
    main = MainHarness(tmp_path)  # starts Main, which migrates the data dir
    main.hold_turns = True
    main.run()
    [window_a], [_] = main.windows(A), main.windows(B)
    assert restored(window_a) == ["a1", "typing"]
    assert [job.request.input[-1]["content"] for job in main.held] == ["b1"]
    assert main.profile.name == "default"
    assert sorted(main.profiles.sessions(main.profile)) == [A, B]


def test_after_the_migration_a_new_session_is_born_as_before(main: MainHarness):
    main.run(MainCommandEntered("/new"))
    [(session_id, _)] = main.opened
    conversation = main.sessions.load_conversation(main.sessions.load(session_id))
    assert conversation is not None
    # The installation's template, now a copy in the Profile's folder, with the same Settings.
    assert conversation.sysprompt == "I am AllIn; you are ana."
    assert (main.profile.sysprompts / "default.txt").is_file()


def session_folder(main: MainHarness, session_id: SessionId) -> dict[str, bytes]:
    folder = main.root / session_id
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def test_share_and_unshare_change_only_the_lists(main: MainHarness):
    session = main.create_session()
    tesis = main.profiles.create("tesis", main.profile)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        WindowClosed(session.id, returncode=0),
        settle=True,
    )
    before = session_folder(main, session.id)
    main.run(MainCommandEntered(f"/profile share {session.id} tesis"))
    assert main.profiles.sessions(tesis) == [session.id]
    assert main.profiles.sessions(main.profile) == [session.id]
    main.run(MainCommandEntered(f"/profile unshare {session.id}"))
    assert main.profiles.sessions(main.profile) == []
    main.run(MainCommandEntered(f"/profile unshare {session.id} tesis"))
    assert main.profiles.sessions(tesis) == []
    assert session_folder(main, session.id) == before


def test_a_session_in_no_profile_can_be_opened_again(main: MainHarness):
    session = main.create_session()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        WindowClosed(session.id, returncode=0),
        MainCommandEntered(f"/profile unshare {session.id}"),
        settle=True,
    )
    assert main.profiles.sessions(main.profile) == []
    chat = main.run(MainCommandEntered(f"/open {session.id}"))
    assert any(f"/open {session.id}: ok" in line for line in chat)
    assert main.profiles.sessions(main.profile) == [session.id]


@pytest.mark.parametrize(
    ("command", "code"),
    [
        ("/profile share zzzzzz tesis", "session_not_found"),
        ("/profile share {sid} nadie", "profile_not_found"),
        ("/profile unshare {sid} nadie", "profile_not_found"),
        ("/profile share {sid}", "invalid_arguments"),
    ],
)
def test_sharing_with_what_does_not_exist_is_an_error_and_changes_nothing(
    main: MainHarness, command: str, code: str
):
    session = main.create_session()
    tesis = main.profiles.create("tesis", main.profile)
    chat = main.run(MainCommandEntered(command.format(sid=session.id)))
    assert any(code in line for line in chat)
    assert main.profiles.sessions(tesis) == []
    assert main.profiles.sessions(main.profile) == []


def test_opening_another_profiles_session_tells_whose_it_is_and_adds_it(main: MainHarness):
    session = main.create_session()
    tesis = main.profiles.create("tesis", main.profile)
    main.profiles.add_session(tesis, session.id)
    main.run(MainCommandEntered(f"/open {session.id}"), settle=True)
    [notice] = [update for update in main.main_screen if isinstance(update, ShowBox)]
    assert notice.box.kind == "notice"
    assert f"{session.id} belongs to profile tesis" in notice.box.text
    assert "added to default too" in notice.box.text
    assert main.profiles.sessions(main.profile) == [session.id]


def test_opening_a_session_of_no_profile_or_the_active_one_tells_nothing(main: MainHarness):
    alone = main.create_session()
    main.run(MainCommandEntered(f"/open {alone.id}"), settle=True)
    main.run(MainCommandEntered(f"/open {alone.id}"), settle=True)  # already the active one's
    assert not [update for update in main.main_screen if isinstance(update, ShowBox)]


def test_session_and_profile_show_which_profiles_each_session_belongs_to(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    tesis = main.profiles.create("tesis", main.profile)
    main.profiles.add_session(main.profile, a.id)
    main.profiles.add_session(main.profile, b.id)
    main.profiles.add_session(tesis, a.id)
    chat = main.run(MainCommandEntered(f"/session {a.id}"), MainCommandEntered("/profile"))
    assert f"[{a.id}] profiles=default, tesis" in chat
    assert f"default (active): {a.id}, {b.id}" in chat
    assert f"tesis: {a.id}" in chat


def test_after_sharing_a_switch_keeps_the_shared_window_open(main: MainHarness):
    session = main.create_session()
    main.profiles.create("tesis", main.profile)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/profile share {session.id} tesis"),
        MainCommandEntered("/profile switch tesis"),
        settle=True,
    )
    closed = [
        entry
        for entry in main.session_log(session)
        if entry.event == "window_closed" and entry.fields.get("reason") == "profile switched"
    ]
    assert closed == []
    assert main.profiles.open(None).name == "tesis"
