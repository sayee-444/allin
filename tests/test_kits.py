"""``/kit export`` and ``/kit load``: Kits, first part (#39, ADR-0014)."""

import json
from pathlib import Path

import pytest

from allin.core.settings import MainSettings, SessionSettings
from allin.main.events import BoxAnswered, MainCommandEntered
from allin.main.portable import MANIFEST, _hash  # pyright: ignore[reportPrivateUsage]
from allin.main.profiles import Profile
from allin.main.settings_files import MainSettingsFiles
from allin.ui.screen import ShowBox
from tests.test_dispatcher import MainHarness

GPU_TOML = 'model = "/models/m.gguf"\ncontext = 8192\nport = 9101\nmmproj = "/models/p.gguf"\n'


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.slot_locks_dir.mkdir(parents=True, exist_ok=True)
    (harness.slot_locks_dir / "gpu.toml").write_text(GPU_TOML)
    (harness.sysprompts / "tutor.txt").write_text("Tutor $assistant_name.")
    harness.settings = MainSettings(
        session=SessionSettings(
            username="ana", assistant_name="Profe", sysprompt="tutor.txt", server="gpu"
        )
    )
    return harness


def command(text: str) -> MainCommandEntered:
    return MainCommandEntered(text)


def other(main: MainHarness) -> Profile:
    """A second Profile with Settings and Sysprompts of its own, the one Kits load into."""
    profile = main.profiles.create("otro", main.profile)
    (profile.sysprompts / "tutor.txt").unlink()
    MainSettingsFiles(profile.settings).save_current(MainSettings())
    return profile


def export(main: MainHarness, folder: Path, *pieces: str) -> list[str]:
    return main.run(command(f"/kit export {folder} {' '.join(pieces)}".rstrip()))


def load_in_other(main: MainHarness, folder: Path, options: str = "") -> list[str]:
    main.run(command("/profile switch otro"))
    return main.run(command(f"/kit load {folder} {options}".rstrip()))


def edit_manifest(folder: Path, change: dict[str, object]) -> None:
    manifest = json.loads((folder / MANIFEST).read_text())
    (folder / MANIFEST).write_text(json.dumps({**manifest, **change}))


def add_piece(folder: Path, kind: str, name: str, files: dict[str, bytes]) -> None:
    for path, data in files.items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(data)
    manifest = json.loads((folder / MANIFEST).read_text())
    piece = {"kind": kind, "name": name, "files": sorted(files), "sha256": _hash(files)}
    manifest["pieces"].append(piece)
    (folder / MANIFEST).write_text(json.dumps(manifest))


def test_a_kit_carries_its_sysprompts_settings_and_server_and_what_they_name(
    main: MainHarness, tmp_path: Path
):
    kit = tmp_path / "tutor"
    chat = export(main, kit, "settings")
    assert any("kit tutor 1 exported" in line for line in chat)
    manifest = json.loads((kit / MANIFEST).read_text())
    assert manifest["type"] == "kit" and manifest["format"] == 1
    pieces = {(p["kind"], p["name"]) for p in manifest["pieces"]}
    assert pieces == {
        ("settings", "main"),
        ("sysprompt", "tutor.txt"),
        ("sysprompt", "compact.txt"),
        ("server", "gpu"),
    } or pieces == {("settings", "main"), ("sysprompt", "tutor.txt"), ("server", "gpu")}
    settings = json.loads((kit / "settings.json").read_text())
    assert settings["session.assistant_name"] == "Profe"
    assert not {"session.exempt", "session.server", "session.username"} & set(settings)


def test_nothing_a_kit_holds_names_a_path_pid_fifo_lock_or_shortcut(
    main: MainHarness, tmp_path: Path
):
    kit = tmp_path / "tutor"
    export(main, kit)
    export(main, tmp_path / "with-server", "server:gpu")
    for folder in (kit, tmp_path / "with-server"):
        for path in folder.rglob("*"):
            if path.is_file():
                text = path.read_text()
                assert str(tmp_path) not in text and "/models" not in text, path
                assert "fifo" not in text.lower() and ".lock" not in text, path
                assert "shortcut" not in text and "port" not in text.replace("sysprompt", ""), path
    assert 'model = "m.gguf"' in (tmp_path / "with-server" / "servers" / "gpu.toml").read_text()


def test_loading_a_kit_copies_its_pieces_with_their_provenance(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "tutor"
    export(main, kit, "--version", "3")
    target = other(main)
    chat = load_in_other(main, kit, "--take settings")
    assert any("kit tutor 3 loaded" in line for line in chat)
    assert (target.sysprompts / "tutor.txt").read_text() == "Tutor $assistant_name."
    current = MainSettingsFiles(target.settings).load().current
    assert current.session.assistant_name == "Profe"
    assert current.session.sysprompt == "tutor.txt"
    assert current.session.server is None, "a Kit never changes the Enlace"
    provenance = json.loads((target.folder / "kits.json").read_text())["pieces"]
    assert provenance["sysprompt:tutor.txt"] == {"kit": "tutor", "version": "3"}
    assert provenance["settings"] == {"kit": "tutor", "version": "3"}


def test_a_conflict_left_unchosen_keeps_the_profiles_own(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "tutor"
    export(main, kit)
    target = other(main)
    (target.sysprompts / "tutor.txt").write_text("My own tutor.")
    chat = load_in_other(main, kit)
    assert (target.sysprompts / "tutor.txt").read_text() == "My own tutor."
    assert MainSettingsFiles(target.settings).load().current == MainSettings()
    assert any("kept the profile's own" in line and "sysprompt:tutor.txt" in line for line in chat)


def test_both_keeps_the_profiles_and_renames_the_kits(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "tutor"
    export(main, kit, "sysprompt:tutor.txt")
    target = other(main)
    (target.sysprompts / "tutor.txt").write_text("My own tutor.")
    load_in_other(main, kit, "--both sysprompt:tutor.txt")
    assert (target.sysprompts / "tutor.txt").read_text() == "My own tutor."
    assert (target.sysprompts / "tutor-tutor.txt").read_text() == "Tutor $assistant_name."


def test_settings_taken_with_both_sysprompts_kept_name_the_kits_copy(
    main: MainHarness, tmp_path: Path
):
    kit = tmp_path / "tutor"
    export(main, kit)
    target = other(main)
    (target.sysprompts / "tutor.txt").write_text("My own tutor.")
    load_in_other(main, kit, "--take settings --both sysprompt:tutor.txt")
    current = MainSettingsFiles(target.settings).load().current
    assert current.session.sysprompt == "tutor-tutor.txt"
    assert (target.sysprompts / "tutor.txt").read_text() == "My own tutor."


def test_settings_are_never_kept_twice(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "tutor"
    export(main, kit)
    other(main)
    chat = load_in_other(main, kit, "--both settings")
    assert any("kit_piece_unknown" in line for line in chat)


def test_a_load_cut_halfway_leaves_the_profile_as_it_was(
    main: MainHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    kit = tmp_path / "tutor"
    export(main, kit)
    target = other(main)
    (target.sysprompts / "tutor.txt").write_text("My own tutor.")
    before = {p.name: p.read_bytes() for p in target.folder.rglob("*") if p.is_file()}
    real_write = Path.write_bytes
    writes: list[Path] = []

    def failing_write(path: Path, data: bytes) -> int:
        writes.append(path)
        if path.name == "kits.json":  # the last one: the Sysprompt is already written
            raise OSError("disk full")
        return real_write(path, data)

    main.run(command("/profile switch otro"))
    monkeypatch.setattr(Path, "write_bytes", failing_write)
    chat = main.run(command(f"/kit load {kit} --take sysprompt:tutor.txt"))
    monkeypatch.undo()
    assert any("profile_storage_failed" in line for line in chat)
    assert len(writes) >= 2
    after = {p.name: p.read_bytes() for p in target.folder.rglob("*") if p.is_file()}
    assert {k: v for k, v in after.items() if k != "state.json"} == {
        k: v for k, v in before.items() if k != "state.json"
    }


def test_a_kit_whose_settings_name_a_missing_sysprompt_is_refused_whole(
    main: MainHarness, tmp_path: Path
):
    kit = tmp_path / "tutor"
    export(main, kit, "settings")
    manifest = json.loads((kit / MANIFEST).read_text())
    manifest["pieces"] = [p for p in manifest["pieces"] if p["name"] != "tutor.txt"]
    (kit / MANIFEST).write_text(json.dumps(manifest))
    (kit / "sysprompts" / "tutor.txt").unlink()
    target = other(main)
    chat = load_in_other(main, kit, "--take settings")
    assert any("kit_reference_missing: missing=['sysprompt:tutor.txt']" in line for line in chat)
    assert not (target.folder / "kits.json").exists()
    assert MainSettingsFiles(target.settings).load().current == MainSettings()


@pytest.mark.parametrize(
    ("path", "data", "reason"),
    [
        ("functions/tool/__pycache__/tool.cpython-314.pyc", b"\x00compiled", "compiled code"),
        ("sysprompts/x.pyc", b"compiled", "compiled code"),
        ("sysprompts/logo.png", b"\x89PNG\x00\x01", "binary files"),
    ],
)
def test_a_kit_with_compiled_code_or_a_binary_is_refused_whole(
    main: MainHarness, tmp_path: Path, path: str, data: bytes, reason: str
):
    kit = tmp_path / "tutor"
    export(main, kit)
    add_piece(kit, "sysprompt", "extra", {path: data})
    target = other(main)
    chat = load_in_other(main, kit)
    assert any(f"kit_rejected: reason='{reason}'" in line for line in chat)
    assert not (target.sysprompts / "tutor.txt").exists()


def test_a_kit_that_brings_shortcuts_is_refused(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "tutor"
    export(main, kit)
    add_piece(kit, "shortcut", "ctrl-x", {"shortcuts/ctrl-x.json": b'{"key": "ctrl-x"}'})
    target = other(main)
    chat = load_in_other(main, kit)
    assert any("kit_rejected" in line and "Atajos" in line for line in chat)
    assert not (target.sysprompts / "tutor.txt").exists()


def test_a_kits_server_creates_an_asleep_supervisor_once_accepted(
    main: MainHarness, tmp_path: Path
):
    kit = tmp_path / "tutor"
    export(main, kit, "server:gpu")
    (main.slot_locks_dir / "gpu.toml").rename(main.slot_locks_dir / "old.toml")
    other(main)
    main.run(command("/profile switch otro"))

    def accept() -> None:  # as a human answers the box, while Main runs
        [box] = [u for u in main.main_screen if isinstance(u, ShowBox)]
        assert box.box.title == "Kit Supervisor"
        assert not (main.slot_locks_dir / "gpu.toml").exists()
        inbox = main.servers.inbox
        assert inbox is not None
        inbox.put(BoxAnswered(box.box.box_id, True))

    main.run(command(f"/kit load {kit}"), accept, settle=True)
    created = (main.slot_locks_dir / "gpu.toml").read_text()
    assert 'model = "m.gguf"' in created and "context = 8192" in created and "port = 99" in created
    assert main.servers.woken == []
    assert (
        MainSettingsFiles(main.profiles.find("otro").settings).load().current.session.server is None
    )


def test_a_kits_server_never_changes_one_of_the_same_name(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "tutor"
    export(main, kit, "server:gpu")
    other(main)
    load_in_other(main, kit)
    assert not [u for u in main.main_screen if isinstance(u, ShowBox)]
    assert (main.slot_locks_dir / "gpu.toml").read_text() == GPU_TOML


@pytest.mark.parametrize(
    "toml",
    [
        'model = "m.gguf"\nport = 9200\n',
        'model = "m.gguf"\nmmproj = "p.gguf"\n',
        'model = "m.gguf"\nextra = ["--host", "0.0.0.0"]\n',
        'model = "/models/m.gguf"\n',
        'model = "m.gguf"\ncwd = "/tmp"\n',
    ],
)
def test_a_kits_server_that_names_a_file_or_opens_the_network_is_refused(
    main: MainHarness, tmp_path: Path, toml: str
):
    kit = tmp_path / "tutor"
    export(main, kit)
    add_piece(kit, "server", "cpu", {"servers/cpu.toml": toml.encode()})
    target = other(main)
    chat = load_in_other(main, kit)
    assert any("kit_rejected" in line and "cpu" in line for line in chat)
    assert not (target.sysprompts / "tutor.txt").exists()


def test_a_kit_of_an_unknown_format_is_refused_clearly(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "tutor"
    export(main, kit)
    edit_manifest(kit, {"format": 2})
    other(main)
    chat = load_in_other(main, kit)
    assert any("portable_format_unknown: format=2, supported=1" in line for line in chat)


def test_a_profile_copy_is_not_a_kit(main: MainHarness, tmp_path: Path):
    main.run(command(f"/profile export default {tmp_path / 'copy'}"))
    chat = main.run(command(f"/kit load {tmp_path / 'copy'}"))
    assert any("portable_invalid" in line and "a profile, not a kit" in line for line in chat)


def test_only_a_human_loads_or_exports_a_kit(main: MainHarness, tmp_path: Path):
    chat = main.run(MainCommandEntered(f"/kit export {tmp_path / 'k'}", origin="session"))
    assert any("human_only" in line for line in chat)
    assert not (tmp_path / "k").exists()


def test_a_kit_whose_pieces_the_profile_already_has_changes_nothing(
    main: MainHarness, tmp_path: Path
):
    kit = tmp_path / "tutor"
    export(main, kit)
    main.profiles.create("otro", main.profile)  # the same Settings and Sysprompts
    chat = load_in_other(main, kit)
    assert any("kit tutor 1 loaded: nothing new" in line for line in chat)
    assert not any("kept" in line for line in chat)
