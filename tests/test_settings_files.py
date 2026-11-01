import json
from pathlib import Path

import pytest

from allin.core.errors import SettingsFileInvalid, SettingsStorageFailed
from allin.core.settings import MainSettings, SessionSettings
from allin.main.settings_files import MainSettingsFiles

ANA = MainSettings(session=SessionSettings(username="ana"))


def test_without_files_both_versions_are_the_code_defaults(tmp_path: Path):
    loaded = MainSettingsFiles(tmp_path).load()
    assert loaded.current == loaded.default == MainSettings()
    assert loaded.problems == []


def test_each_version_is_saved_to_its_own_file_and_read_back(tmp_path: Path):
    files = MainSettingsFiles(tmp_path)
    files.save_current(ANA.model_copy(update={"parallel": 2}))
    files.save_default(ANA)
    loaded = MainSettingsFiles(tmp_path).load()
    assert loaded.current.parallel == 2
    assert loaded.default == ANA


def test_without_a_current_file_the_current_settings_are_the_defaults(tmp_path: Path):
    files = MainSettingsFiles(tmp_path)
    files.save_default(ANA)
    assert files.load().current == ANA


def test_a_current_file_with_a_bad_key_says_which_and_uses_the_defaults(tmp_path: Path):
    files = MainSettingsFiles(tmp_path)
    files.save_default(ANA)
    broken = ANA.model_dump()
    broken["session"]["sampling"]["temperature"] = "hot"
    (tmp_path / "current.json").write_text(json.dumps(broken))
    loaded = files.load()
    assert loaded.current == ANA
    [problem] = loaded.problems
    assert isinstance(problem, SettingsFileInvalid)
    assert problem.fields["key"] == "session.sampling.temperature"
    assert problem.fields["file"] == "current.json"


def test_an_unknown_key_in_a_file_is_a_problem_too(tmp_path: Path):
    (tmp_path / "default.json").write_text('{"paralel": 2}')
    loaded = MainSettingsFiles(tmp_path).load()
    assert loaded.default == MainSettings()
    assert [problem.fields["key"] for problem in loaded.problems] == ["paralel"]


def test_a_file_that_is_not_json_is_a_problem_without_a_key(tmp_path: Path):
    (tmp_path / "current.json").write_text("{not json")
    [problem] = MainSettingsFiles(tmp_path).load().problems
    assert problem.fields["key"] is None


def test_a_broken_file_is_left_as_it_was(tmp_path: Path):
    (tmp_path / "current.json").write_text("{not json")
    MainSettingsFiles(tmp_path).load()
    assert (tmp_path / "current.json").read_text() == "{not json"


def test_a_file_that_cannot_be_written_is_an_error(tmp_path: Path):
    (tmp_path / "blocked").write_text("")
    with pytest.raises(SettingsStorageFailed):
        MainSettingsFiles(tmp_path / "blocked").save_current(ANA)
