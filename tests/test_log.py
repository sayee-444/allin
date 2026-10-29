import json
from datetime import datetime
from pathlib import Path

import pytest

from allin.core.errors import LogUnreadable, LogWriteFailed
from allin.main.log import Log, read_log


def test_each_event_is_appended_as_one_json_line_with_its_date_and_time(tmp_path: Path):
    log = Log(tmp_path / "log.jsonl")
    log.append("turn_started", sid="aaaaaa")
    log.append("prompt", text="two\nlines")

    lines = (tmp_path / "log.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "turn_started" and first["sid"] == "aaaaaa"
    assert datetime.fromisoformat(first["ts"]).tzinfo is not None


def test_events_are_read_back_in_order_with_their_fields(tmp_path: Path):
    log = Log(tmp_path / "log.jsonl")
    log.append("prompt", text="hola")
    log.append("turn_finished", turn=0)
    entries = read_log(tmp_path / "log.jsonl")
    assert [(entry.event, entry.fields) for entry in entries] == [
        ("prompt", {"text": "hola"}),
        ("turn_finished", {"turn": 0}),
    ]


def test_a_log_is_only_appended_to_never_rewritten(tmp_path: Path):
    Log(tmp_path / "log.jsonl").append("first")
    Log(tmp_path / "log.jsonl").append("second")
    assert [entry.event for entry in read_log(tmp_path / "log.jsonl")] == ["first", "second"]


def test_a_missing_log_reads_as_empty(tmp_path: Path):
    assert read_log(tmp_path / "log.jsonl") == []


def test_a_line_cut_by_a_crash_is_skipped(tmp_path: Path):
    Log(tmp_path / "log.jsonl").append("whole")
    with open(tmp_path / "log.jsonl", "a") as file:
        file.write('{"ts": "2026-10-28T20:00:00+00:00", "ev')
    assert [entry.event for entry in read_log(tmp_path / "log.jsonl")] == ["whole"]


def test_a_log_that_cannot_be_written_fails_with_an_allin_error(tmp_path: Path):
    with pytest.raises(LogWriteFailed):
        Log(tmp_path / "missing" / "log.jsonl").append("lost")


def test_a_log_that_cannot_be_read_fails_with_an_allin_error(tmp_path: Path):
    (tmp_path / "log.jsonl").mkdir()
    with pytest.raises(LogUnreadable):
        read_log(tmp_path / "log.jsonl")
