import re
from pathlib import Path

import pytest

from allin.core.errors import SessionNotFound, SessionStorageFailed, SessionUnreadable
from allin.core.ids import MainId
from allin.core.settings import SessionSettings
from allin.main.conversation import Conversation
from allin.main.sessions import SessionStore

OWNER = MainId("root")
SETTINGS = SessionSettings(username="ana")


def test_a_new_session_gets_a_six_character_id_and_its_own_folder(tmp_path: Path):
    session = SessionStore(tmp_path).create(OWNER, SETTINGS, "sysprompt")
    assert re.fullmatch(r"[a-z0-9]{6}", session.id)
    assert (tmp_path / session.id).is_dir()


def test_an_id_whose_folder_already_exists_is_never_reused(tmp_path: Path):
    (tmp_path / "aaaaaa").mkdir()
    ids = iter(["aaaaaa", "bbbbbb"])
    session = SessionStore(tmp_path, new_id=lambda: next(ids)).create(OWNER, SETTINGS, "sysprompt")
    assert session.id == "bbbbbb"
    assert list((tmp_path / "aaaaaa").iterdir()) == []


def test_a_session_is_loaded_back_with_its_metadata(tmp_path: Path):
    created = SessionStore(tmp_path).create(OWNER, SETTINGS, "sysprompt")
    loaded = SessionStore(tmp_path).load(created.id)
    assert loaded == created
    assert loaded.owner == OWNER and loaded.created_by == OWNER


def test_loading_an_unknown_session_fails(tmp_path: Path):
    with pytest.raises(SessionNotFound):
        SessionStore(tmp_path).load("zzzzzz")


@pytest.mark.parametrize("session_id", ["../x", "", "a/b", "ABCDEF"])
def test_a_session_id_that_is_not_one_is_never_looked_up_on_disk(tmp_path: Path, session_id: str):
    (tmp_path.parent / "x").mkdir(exist_ok=True)
    with pytest.raises(SessionNotFound):
        SessionStore(tmp_path).load(session_id)


def test_a_session_with_broken_metadata_cannot_be_loaded(tmp_path: Path):
    session = SessionStore(tmp_path).create(OWNER, SETTINGS, "sysprompt")
    (tmp_path / session.id / "session.json").write_text("{not json")
    with pytest.raises(SessionUnreadable):
        SessionStore(tmp_path).load(session.id)


def test_a_session_folder_without_metadata_cannot_be_loaded(tmp_path: Path):
    (tmp_path / "aaaaaa").mkdir()
    with pytest.raises(SessionUnreadable):
        SessionStore(tmp_path).load("aaaaaa")


def test_a_session_that_cannot_be_written_fails_with_an_allin_error(tmp_path: Path):
    (tmp_path / "sessions").write_text("not a folder")
    with pytest.raises(SessionStorageFailed):
        SessionStore(tmp_path / "sessions").create(OWNER, SETTINGS, "sysprompt")


def test_a_new_session_keeps_its_settings_and_starts_its_conversation_with_the_sysprompt(
    tmp_path: Path,
):
    store = SessionStore(tmp_path)
    session = store.create(OWNER, SETTINGS, "You are AllIn.")
    assert store.load(session.id).settings == SETTINGS
    assert store.load_conversation(session) == Conversation(sysprompt="You are AllIn.")
