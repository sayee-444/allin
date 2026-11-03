"""The first fproc AllIn's installation brings (#113): a shared text file with its cursor, read,
searched and written at it, whose cursor outlives the process in its Carpeta de trabajo."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import AttachedProcess, FprocStart, ToolLauncher
from allin.confinement.support import FULL_SUPPORT
from allin.core.errors import AttachFailed
from allin.main.descriptions import Descriptions

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

PACKAGE = Path(__file__).parents[1] / "functions" / "file"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "var"


@pytest.fixture
def launcher(data_dir: Path) -> Iterator[ToolLauncher]:
    launcher = ToolLauncher(data_dir, grace=1.0)
    launcher.start()
    yield launcher
    launcher.stop()


@pytest.fixture
def shared(tmp_path: Path) -> Path:
    path = tmp_path / "user" / "notes.txt"
    path.parent.mkdir()
    path.write_text("one\ntwo\nthree\nfour\n")
    return path


def attach(launcher: ToolLauncher, data_dir: Path, file: Path | None) -> AttachedProcess:
    workspace = data_dir / "attached" / "default" / "notes"
    workspace.mkdir(parents=True, exist_ok=True)
    return launcher.attach(FprocStart(PACKAGE, "shared_file", "notes", workspace, file=file), 20)


@pytest.fixture
def notes(launcher: ToolLauncher, data_dir: Path, shared: Path) -> Iterator[AttachedProcess]:
    process = attach(launcher, data_dir, shared)
    yield process
    process.detach()


def test_it_is_an_fproc_whose_methods_are_read_search_seek_and_write(
    launcher: ToolLauncher, data_dir: Path
):
    described = Descriptions(data_dir, launcher, FULL_SUPPORT).describe(
        PACKAGE, "shared_file", written_by_user=True
    )
    assert described.kind == "fproc"
    assert [tool.name for tool in described.tools] == ["read", "search", "seek", "write"]


def test_reading_moves_the_cursor_past_what_it_read(notes: AttachedProcess):
    first = notes.call("read", {"lines": 2}, timeout=10).value
    second = notes.call("read", {"lines": 5}, timeout=10).value
    assert first == {"from": 1, "lines": ["one", "two"], "end": False}
    assert second == {"from": 3, "lines": ["three", "four"], "end": True}


def test_searching_moves_the_cursor_to_the_next_line_that_holds_it_from_the_start_again(
    notes: AttachedProcess,
):
    notes.call("seek", {"line": 3}, timeout=10)
    assert notes.call("search", {"text": "o"}, timeout=10).value == {"line": 4, "text": "four"}
    notes.call("seek", {"line": 5}, timeout=10)
    assert notes.call("search", {"text": "tw"}, timeout=10).value == {"line": 2, "text": "two"}
    assert notes.call("search", {"text": "five"}, timeout=10).value == {"line": None}


def test_writing_inserts_whole_lines_at_the_cursor_in_the_file_itself(
    notes: AttachedProcess, shared: Path
):
    notes.call("seek", {"line": 2}, timeout=10)
    written = notes.call("write", {"text": "one and a half\nalmost two"}, timeout=10).value
    assert written == {"from": 2, "lines": 6}
    assert shared.read_text() == "one\none and a half\nalmost two\ntwo\nthree\nfour\n"
    assert notes.call("read", {"lines": 1}, timeout=10).value == {
        "from": 4,
        "lines": ["two"],
        "end": False,
    }


def test_attached_again_with_the_same_name_its_cursor_is_where_it_was(
    launcher: ToolLauncher, data_dir: Path, shared: Path
):
    first = attach(launcher, data_dir, shared)
    first.call("seek", {"line": 3}, timeout=10)
    first.detach()
    again = attach(launcher, data_dir, shared)
    try:
        assert again.call("read", {"lines": 1}, timeout=10).value == {
            "from": 3,
            "lines": ["three"],
            "end": False,
        }
    finally:
        again.detach()


def test_a_file_replaced_on_disk_is_told_as_one_to_attach_again(
    notes: AttachedProcess, shared: Path
):
    replacement = shared.with_suffix(".new")
    replacement.write_text("new\n")
    replacement.replace(shared)  # as an editor that saves anew does
    failed = notes.call("read", {}, timeout=10)
    assert failed.error is not None and "attach it again" in failed.error


def test_without_a_file_it_does_not_attach(launcher: ToolLauncher, data_dir: Path):
    with pytest.raises(AttachFailed) as raised:
        attach(launcher, data_dir, None)
    assert "--file=<path>" in str(raised.value.fields["error"])
