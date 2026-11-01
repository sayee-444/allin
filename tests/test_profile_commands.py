from pathlib import Path

import pytest

from allin.core.ids import ROOT_MAIN_ID, SessionId
from allin.core.settings import SessionSettings
from allin.main.events import MainCommandEntered
from allin.main.prompts import Prompt
from allin.main.sessions import SessionStore
from allin.main.state import MainState, MainStateStore
from tests.test_dispatcher import MainHarness, restored


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


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
