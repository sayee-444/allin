from pathlib import Path

import pytest

from allin.core.errors import (
    MainAlreadyRunning,
    ProfileExists,
    ProfileNameInvalid,
    ProfileNotFound,
)
from allin.core.ids import (
    DEFAULT_PROFILE,
    ROOT_MAIN_ID,
    SessionId,
    new_profile_name,
    parse_profile_name,
)
from allin.core.settings import MainSettings, SessionSettings
from allin.main.app import start_root
from allin.main.profiles import Profile, ProfileStore
from allin.main.prompts import Prompt
from allin.main.sessions import SessionStore
from allin.main.settings_files import MainSettingsFiles
from allin.main.state import MainState, MainStateStore


@pytest.mark.parametrize("name", ["tesis-2026", "abc", "A1-b2", "x" * 32, "default", "123"])
def test_a_profile_name_takes_letters_digits_and_hyphens_from_3_to_32(name: str):
    assert parse_profile_name(name) == name


@pytest.mark.parametrize(
    "name", ["X!", "ab", "x" * 33, "tesis_2026", "a b c", "../etc", ".default", "ñandú", ""]
)
def test_any_other_profile_name_is_invalid(name: str):
    assert parse_profile_name(name) is None


def test_a_generated_profile_name_is_8_characters_and_valid():
    name = new_profile_name()
    assert len(name) == 8 and parse_profile_name(name) == name
    assert new_profile_name() != name


A = SessionId("aaaaaa")
B = SessionId("bbbbbb")
STATE = MainState(
    windows={B: 4242, A: 4243},
    drafts={A: ["half\nwritten", "a0"]},
    queue=[Prompt(A, "a2"), Prompt(B, "b1")],
    running=[Prompt(A, "a1")],
)
SETTINGS = MainSettings(session=SessionSettings(assistant_name="Neva"))


def seed(tmp_path: Path) -> Path:
    """The Sysprompts the installation brings."""
    folder = tmp_path / "install" / "sysprompts"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "default.txt").write_text("I am $assistant_name; you are $username.")
    (folder / "allin.txt").write_text("AllIn.")
    return folder


def data_dir_before_profiles(tmp_path: Path) -> Path:
    """A data dir as Main left it before Profiles: its MainState, Settings and Sessions."""
    data_dir = tmp_path / "var"
    MainStateStore(data_dir / "state" / "root.json").save(STATE)
    MainSettingsFiles(data_dir / "settings").save_current(SETTINGS)
    sessions = SessionStore(data_dir / "sessions", iter([A, B]).__next__)
    for _ in range(2):
        sessions.create(ROOT_MAIN_ID, SessionSettings(), "I am AllIn.")
    return data_dir


def test_a_data_dir_from_before_profiles_becomes_the_default_profile(tmp_path: Path):
    data_dir = data_dir_before_profiles(tmp_path)
    profiles = ProfileStore(data_dir, seed(tmp_path))
    profiles.migrate()

    profile = profiles.open(None)
    assert profile.name == DEFAULT_PROFILE
    assert profile.folder == data_dir / "profiles" / "default"
    assert MainStateStore(profile.state).load() == STATE  # Windows, queue and Drafts
    assert MainSettingsFiles(profile.settings).load().current == SETTINGS
    assert sorted(profiles.sessions(profile)) == [A, B]
    assert {path.name for path in profile.sysprompts.iterdir()} == {"default.txt", "allin.txt"}
    assert not (data_dir / "state" / "root.json").exists()
    assert not (data_dir / "settings").exists()
    assert sorted(SessionStore(data_dir / "sessions").ids()) == [A, B]  # they stay there


def test_a_new_data_dir_starts_as_default_with_the_installations_sysprompts(tmp_path: Path):
    profiles = ProfileStore(tmp_path / "var", seed(tmp_path))
    profiles.migrate()
    profile = profiles.open(None)
    assert profile.name == DEFAULT_PROFILE
    assert (profile.sysprompts / "default.txt").read_text() == (
        "I am $assistant_name; you are $username."
    )
    assert profiles.sessions(profile) == []
    assert MainStateStore(profile.state).load() == MainState()


def test_the_migration_runs_only_the_first_time(tmp_path: Path):
    data_dir = data_dir_before_profiles(tmp_path)
    profiles = ProfileStore(data_dir, seed(tmp_path))
    profiles.migrate()
    profile = profiles.open(None)
    MainStateStore(profile.state).save(MainState())
    (profile.sysprompts / "default.txt").write_text("Changed.")
    profiles.migrate()
    assert MainStateStore(profile.state).load() == MainState()
    assert (profile.sysprompts / "default.txt").read_text() == "Changed."


def test_a_migration_cut_halfway_is_done_again_on_the_next_start(tmp_path: Path):
    data_dir = data_dir_before_profiles(tmp_path)
    (data_dir / "profiles.new" / "default").mkdir(parents=True)  # a Main stopped mid-migration
    profiles = ProfileStore(data_dir, seed(tmp_path))
    profiles.migrate()
    assert MainStateStore(profiles.open(None).state).load() == STATE
    assert not (data_dir / "profiles.new").exists()


def files_in(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*")) if folder.exists() else []


@pytest.mark.parametrize("name", ["noexiste", "X!", "../state"])
def test_naming_a_profile_that_does_not_exist_is_an_error_and_creates_nothing(
    tmp_path: Path, name: str
):
    data_dir = data_dir_before_profiles(tmp_path)
    before = files_in(data_dir)
    with pytest.raises(ProfileNotFound):
        ProfileStore(data_dir, seed(tmp_path)).check(name)
    assert files_in(data_dir) == before


def test_on_a_new_data_dir_only_default_can_be_named(tmp_path: Path):
    profiles = ProfileStore(tmp_path / "var", seed(tmp_path))
    assert profiles.check("default") == DEFAULT_PROFILE
    assert profiles.check(None) == DEFAULT_PROFILE
    with pytest.raises(ProfileNotFound):
        profiles.check("noexiste")
    assert not (tmp_path / "var").exists()


def test_a_profile_that_does_not_exist_is_never_opened(tmp_path: Path):
    profiles = ProfileStore(tmp_path / "var", seed(tmp_path))
    profiles.migrate()
    with pytest.raises(ProfileNotFound):
        profiles.open("noexiste")
    assert profiles.names() == [DEFAULT_PROFILE]


def migrated(tmp_path: Path) -> tuple[ProfileStore, Profile]:
    profiles = ProfileStore(data_dir_before_profiles(tmp_path), seed(tmp_path))
    profiles.migrate()
    return profiles, profiles.open(None)


def test_without_a_name_main_opens_the_last_profile_used(tmp_path: Path):
    profiles, default = migrated(tmp_path)
    profiles.create("tesis-2026", default)
    assert profiles.open("tesis-2026").name == "tesis-2026"
    assert profiles.check(None) == "tesis-2026"
    assert profiles.open(None).name == "tesis-2026"


def test_a_new_profile_copies_the_settings_and_sysprompts_but_no_sessions(tmp_path: Path):
    profiles, default = migrated(tmp_path)
    created = profiles.create("tesis-2026", default)
    assert created.folder == default.folder.parent / "tesis-2026"
    assert MainSettingsFiles(created.settings).load().current == SETTINGS
    assert (created.sysprompts / "allin.txt").read_text() == "AllIn."
    assert profiles.sessions(created) == []
    assert MainStateStore(created.state).load() == MainState()
    assert profiles.names() == ["default", "tesis-2026"]


def test_changing_a_sysprompt_in_one_profile_does_not_change_the_other(tmp_path: Path):
    profiles, default = migrated(tmp_path)
    created = profiles.create("tesis-2026", default)
    (created.sysprompts / "default.txt").write_text("Only in tesis.")
    assert (default.sysprompts / "default.txt").read_text() == (
        "I am $assistant_name; you are $username."
    )


def test_a_new_profile_without_a_name_gets_a_generated_one(tmp_path: Path):
    profiles, default = migrated(tmp_path)
    created = profiles.create(None, default)
    assert len(created.name) == 8
    assert profiles.names() == sorted(["default", created.name])


def test_a_new_profile_with_an_invalid_name_is_an_error(tmp_path: Path):
    profiles, default = migrated(tmp_path)
    with pytest.raises(ProfileNameInvalid):
        profiles.create("X!", default)
    assert profiles.names() == ["default"]
    assert [path.name for path in default.folder.parent.iterdir()] == ["default"]


def test_a_new_profile_with_a_name_taken_is_an_error_and_changes_nothing(tmp_path: Path):
    profiles, default = migrated(tmp_path)
    with pytest.raises(ProfileExists):
        profiles.create("default", default)
    assert sorted(profiles.sessions(default)) == [A, B]


def test_a_session_joins_a_profiles_list_once(tmp_path: Path):
    profiles, default = migrated(tmp_path)
    created = profiles.create("tesis-2026", default)
    profiles.add_session(created, B)
    profiles.add_session(created, A)
    profiles.add_session(created, B)
    assert profiles.sessions(created) == [B, A]
    assert sorted(profiles.sessions(default)) == [A, B]


def test_a_second_root_main_on_the_same_data_dir_fails_whatever_its_profile(tmp_path: Path):
    data_dir = data_dir_before_profiles(tmp_path)
    first, default = start_root(data_dir, seed(tmp_path), None)
    ProfileStore(data_dir, seed(tmp_path)).create("otro", default)
    with pytest.raises(MainAlreadyRunning) as raised:
        start_root(data_dir, seed(tmp_path), "otro")
    assert raised.value.fields["lock"] == str(data_dir / "state" / "main.lock")
    del first


def test_starting_with_a_profile_that_does_not_exist_creates_nothing(tmp_path: Path):
    with pytest.raises(ProfileNotFound):
        start_root(tmp_path / "var", seed(tmp_path), "noexiste")
    assert not (tmp_path / "var").exists()


def test_starting_main_migrates_and_opens_the_profile(tmp_path: Path):
    data_dir = data_dir_before_profiles(tmp_path)
    _, profile = start_root(data_dir, seed(tmp_path), "default")
    assert MainStateStore(profile.state).load() == STATE
