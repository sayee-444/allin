from pathlib import Path

import pytest

from allin.core.errors import MainStateUnreadable
from allin.main.prompts import Prompt
from allin.main.state import MainState, MainStateStore

STATE = MainState(
    windows={"bbbbbb": 4242, "aaaaaa": 4243},
    drafts={"aaaaaa": "half\nwritten"},
    queue=[Prompt("aaaaaa", "a2"), Prompt("bbbbbb", "b1"), Prompt("aaaaaa", "a3")],
    running=[Prompt("aaaaaa", "a1")],
)


def test_a_saved_state_is_loaded_back_the_same(tmp_path: Path):
    store = MainStateStore(tmp_path / "state" / "root.json")
    store.save(STATE)
    assert MainStateStore(tmp_path / "state" / "root.json").load() == STATE


def test_a_main_that_never_saved_starts_with_nothing(tmp_path: Path):
    assert MainStateStore(tmp_path / "root.json").load() == MainState()


def test_a_broken_state_cannot_be_loaded(tmp_path: Path):
    (tmp_path / "root.json").write_text("{not json")
    with pytest.raises(MainStateUnreadable):
        MainStateStore(tmp_path / "root.json").load()
