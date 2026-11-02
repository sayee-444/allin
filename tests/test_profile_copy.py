"""``/profile export`` and ``/profile import``: a Profile's copy (#39, ADR-0010)."""

import json
from pathlib import Path

import pytest

from allin.core.ids import SessionId
from allin.main.events import DraftReceived, InputReceived, MainCommandEntered, WindowClosed
from allin.main.portable import MANIFEST
from allin.main.state import MainStateStore
from tests.llm_fakes import FakeLlm, full_reply
from tests.test_dispatcher import MainHarness

GPU_TOML = 'model = "/models/big/m.gguf"\nport = 9101\ncwd = "/models"\nextra = ["--lora", "/models/l.gguf"]\n'


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.slot_locks_dir.mkdir(parents=True, exist_ok=True)
    (harness.slot_locks_dir / "gpu.toml").write_text(GPU_TOML)
    return harness


def command(text: str) -> MainCommandEntered:
    return MainCommandEntered(text)


def folder_files(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def test_a_copy_imported_with_another_name_has_the_same_sessions_and_pieces(
    main: MainHarness, tmp_path: Path
):
    a = main.create_session()  # on Main's Server, which the harness says is ready
    (main.sysprompts / "mine.txt").write_text("Mine.")
    main.llm = FakeLlm(full_reply("hm", "Hola"))
    main.run(
        command(f"/open {a.id}"),
        InputReceived(a.id, "hola"),
        DraftReceived(a.id, ("typing",)),
        settle=True,
    )
    out = tmp_path / "copy"
    chat = main.run(command(f"/profile export default {out}"))
    assert any("profile default exported: 1 sessions" in line for line in chat)
    # The copy holds the Session as it is, and its id is taken here: the import renews it.
    chat = main.run(command(f"/profile import {out} mudanza"))
    imported = main.profiles.find("mudanza")
    [new_id] = main.profiles.sessions(imported)
    assert new_id != a.id
    assert any(f"new ids: {a.id}→{new_id}" in line for line in chat)
    # As they were when exported: Main logged the Window reopening since.
    exported, new_files = folder_files(out / "sessions" / a.id), folder_files(main.root / new_id)
    assert exported["conversation.json"] == new_files["conversation.json"]
    assert exported["log.jsonl"] == new_files["log.jsonl"]
    assert b"hola" in new_files["conversation.json"]
    assert json.loads(new_files["session.json"])["id"] == new_id
    assert (imported.sysprompts / "mine.txt").read_text() == "Mine."
    assert MainStateStore(imported.state).load().windows == {}
    assert not imported.state.exists()  # no Windows, queue or Drafts


def test_a_copy_into_a_new_data_dir_keeps_its_session_ids(main: MainHarness, tmp_path: Path):
    a = main.create_session()
    main.run(command(f"/open {a.id}"), WindowClosed(a.id, returncode=0), settle=True)
    main.run(command(f"/profile export default {tmp_path / 'copy'}"))
    other = MainHarness(tmp_path / "other")
    other.run(command(f"/profile import {tmp_path / 'copy'} mudanza"))
    assert other.profiles.sessions(other.profiles.find("mudanza")) == [a.id]
    assert (other.root / a.id / "conversation.json").read_bytes() == (
        main.root / a.id / "conversation.json"
    ).read_bytes()


def test_importing_with_a_name_that_exists_is_an_error_and_touches_nothing(
    main: MainHarness, tmp_path: Path
):
    main.create_session()
    main.run(command(f"/profile export default {tmp_path / 'copy'}"))
    before = folder_files(tmp_path / "profiles") | folder_files(main.root)
    chat = main.run(command(f"/profile import {tmp_path / 'copy'} default"))
    assert any("profile_exists" in line for line in chat)
    after = folder_files(tmp_path / "profiles") | folder_files(main.root)
    assert {k: v for k, v in after.items() if "state.json" not in k and "log.jsonl" not in k} == {
        k: v for k, v in before.items() if "state.json" not in k and "log.jsonl" not in k
    }
    assert main.profiles.names() == ["default"]


def test_nothing_the_copy_holds_names_a_path_pid_fifo_or_lock(main: MainHarness, tmp_path: Path):
    a = main.create_session(server="gpu")
    main.profiles.add_session(main.profile, a.id)
    main.run(command(f"/open {a.id}"), settle=True)
    out = tmp_path / "copy"
    main.run(command(f"/profile export default {out}"))
    files = folder_files(out)
    assert "state.json" not in " ".join(files)
    for path, data in files.items():
        if path.startswith("sessions/"):
            continue  # what the Conversation and Log hold travels as it is
        text = data.decode()
        assert str(tmp_path) not in text, path
        assert "/models" not in text, path
        assert "fifo" not in text.lower() and ".lock" not in text, path
    server = files["servers/gpu.toml"].decode()
    assert 'model = "m.gguf"' in server and "cwd" not in server and '"l.gguf"' in server


def test_a_copy_of_an_unknown_format_is_refused_clearly(main: MainHarness, tmp_path: Path):
    out = tmp_path / "copy"
    main.run(command(f"/profile export default {out}"))
    manifest = json.loads((out / MANIFEST).read_text())
    (out / MANIFEST).write_text(json.dumps({**manifest, "format": 99}))
    chat = main.run(command(f"/profile import {out} nuevo"))
    assert any("portable_format_unknown: format=99, supported=1" in line for line in chat)
    assert main.profiles.names() == ["default"]


def test_a_copy_whose_file_changed_is_refused(main: MainHarness, tmp_path: Path):
    a = main.create_session()
    main.profiles.add_session(main.profile, a.id)
    out = tmp_path / "copy"
    main.run(command(f"/profile export default {out}"))
    (out / "sessions" / a.id / "log.jsonl").write_text("changed\n")
    chat = main.run(command(f"/profile import {out} nuevo"))
    assert any("portable_invalid" in line for line in chat)
    assert main.profiles.names() == ["default"]


def test_exporting_into_a_folder_with_something_in_it_is_an_error(
    main: MainHarness, tmp_path: Path
):
    out = tmp_path / "copy"
    out.mkdir()
    (out / "mine.txt").write_text("not AllIn's")
    chat = main.run(command(f"/profile export default {out}"))
    assert any("export_folder_not_empty" in line for line in chat)
    assert folder_files(out) == {"mine.txt": b"not AllIn's"}


def test_an_imported_supervisor_never_changes_one_of_the_same_name(
    main: MainHarness, tmp_path: Path
):
    a = main.create_session(server="gpu")
    main.profiles.add_session(main.profile, SessionId(a.id))
    main.run(command(f"/profile export default {tmp_path / 'copy'}"))
    main.run(command(f"/profile import {tmp_path / 'copy'} nuevo"))
    assert (main.slot_locks_dir / "gpu.toml").read_text() == GPU_TOML
