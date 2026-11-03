"""A Proceso adherido's process (#113, ADR-0012, ADR-0016): started from an fproc by the Lanzador
de tools, confined for good with its Alcance and its shared file, with its own Carpeta de
trabajo as current folder; its methods are served one at a time until it is detached, it
crashes or Main dies, and it never starts again by itself."""

import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import AttachedProcess, FprocStart, ToolLauncher
from allin.core.errors import AttachedEnded, AttachFailed, ToolTimedOut
from tests.test_tool_launcher import alive, unique_title

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

COUNTER = '''
import os
import subprocess
import threading
import time
from pathlib import Path

from allin.fproc import Fproc


class Counter(Fproc):
    """Counts, and keeps the count in its Carpeta de trabajo."""

    def __init__(self):
        kept = Path("count")
        self.count = int(kept.read_text()) if kept.exists() else 0

    def add(self, n: int) -> int:
        self.count += n
        Path("count").write_text(str(self.count))
        return self.count

    def where(self) -> dict:
        return {"cwd": os.getcwd(), "name": self.name, "file": str(self.file)}

    def read_shared(self) -> str:
        return self.file.read_text()

    def write_shared(self, text: str) -> None:
        self.file.write_text(text)

    def peek(self, path: str) -> str:
        return Path(path).read_text()

    def spawn(self, title: str) -> None:
        subprocess.Popen(["sleep", title])

    def crash(self) -> None:
        os._exit(3)

    def crash_later(self) -> None:
        threading.Timer(0.2, os._exit, (4,)).start()

    def hang(self) -> None:
        time.sleep(600)

    def fail(self) -> None:
        raise ValueError("no")

    def _hidden(self) -> int:
        return 1
'''

BROKEN = """
from allin.fproc import Fproc


class Broken(Fproc):
    def __init__(self):
        raise RuntimeError("cannot start")
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "var"
    (data / "state").mkdir(parents=True)
    return data


@pytest.fixture
def package(data_dir: Path) -> Path:
    folder = data_dir / "profiles" / "default" / "functions" / "counting"
    folder.mkdir(parents=True)
    (folder / "counter.py").write_text(COUNTER)
    (folder / "broken.py").write_text(BROKEN)
    return folder


@pytest.fixture
def launcher(data_dir: Path) -> Iterator[ToolLauncher]:
    launcher = ToolLauncher(data_dir, grace=1.0)
    launcher.start()
    yield launcher
    launcher.stop()


def start(
    data_dir: Path, package: Path, file: Path | None = None, module: str = "counter"
) -> FprocStart:
    workspace = data_dir / "attached" / "default" / "notes"
    workspace.mkdir(parents=True, exist_ok=True)
    return FprocStart(package, module, "notes", workspace, file=file)


@pytest.fixture
def attached(launcher: ToolLauncher, data_dir: Path, package: Path) -> Iterator[AttachedProcess]:
    process = launcher.attach(start(data_dir, package), timeout=20)
    yield process
    process.detach()


def test_its_methods_keep_state_between_calls_in_its_own_carpeta_de_trabajo(
    attached: AttachedProcess, data_dir: Path
):
    assert attached.call("add", {"n": 2}, timeout=10).value == 2
    assert attached.call("add", {"n": 3}, timeout=10).value == 5
    where = attached.call("where", {}, timeout=10).value
    workspace = data_dir / "attached" / "default" / "notes"
    assert where == {"cwd": str(workspace), "name": "notes", "file": "None"}
    assert (workspace / "count").read_text() == "5"


def test_attached_again_it_finds_what_it_kept_in_its_carpeta_de_trabajo(
    launcher: ToolLauncher, data_dir: Path, package: Path
):
    first = launcher.attach(start(data_dir, package), timeout=20)
    first.call("add", {"n": 4}, timeout=10)
    first.detach()
    again = launcher.attach(start(data_dir, package), timeout=20)
    try:
        assert again.call("add", {"n": 1}, timeout=10).value == 5
    finally:
        again.detach()


def test_it_reads_and_writes_its_shared_file_and_nothing_else_outside_its_reach(
    launcher: ToolLauncher, data_dir: Path, package: Path, tmp_path: Path
):
    shared = tmp_path / "shared.txt"
    shared.write_text("hello")
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    process = launcher.attach(start(data_dir, package, file=shared), timeout=20)
    try:
        assert process.call("read_shared", {}, timeout=10).value == "hello"
        process.call("write_shared", {"text": "bye"}, timeout=10)
        assert shared.read_text() == "bye"
        refused = process.call("peek", {"path": str(secret)}, timeout=10)
        assert refused.error is not None and "PermissionError" in refused.error
    finally:
        process.detach()


def test_a_method_that_raises_or_does_not_exist_gives_an_error_and_it_goes_on(
    attached: AttachedProcess,
):
    failed = attached.call("fail", {}, timeout=10)
    assert failed.error == "ValueError: no"
    for missing in ("_hidden", "nothing", "__init__"):
        assert "NoSuchMethod" in str(attached.call(missing, {}, timeout=10).error)
    assert attached.call("add", {"n": 1}, timeout=10).value == 1


def test_a_class_that_cannot_be_made_fails_the_attachment(
    launcher: ToolLauncher, data_dir: Path, package: Path
):
    with pytest.raises(AttachFailed) as raised:
        launcher.attach(start(data_dir, package, module="broken"), timeout=20)
    assert raised.value.fields["error"] == "RuntimeError: cannot start"


def test_once_it_crashed_every_method_fails_and_it_never_starts_again(
    attached: AttachedProcess,
):
    with pytest.raises(AttachedEnded) as raised:
        attached.call("crash", {}, timeout=10)
    assert raised.value.fields["exit_code"] == 3
    assert attached.wait() == 3
    with pytest.raises(AttachedEnded):
        attached.call("add", {"n": 1}, timeout=10)


def test_a_crash_between_calls_tells_how_its_fproc_ended(attached: AttachedProcess):
    attached.call("crash_later", {}, timeout=10)
    assert attached.wait() == 4
    with pytest.raises(AttachedEnded) as raised:
        attached.call("add", {"n": 1}, timeout=10)
    assert raised.value.fields["exit_code"] == 4


def test_detaching_it_takes_its_descendants_down(attached: AttachedProcess):
    title = unique_title()
    attached.call("spawn", {"title": title}, timeout=10)
    assert alive(title)
    attached.detach()
    assert attached.wait() is not None
    assert not alive(title)


def test_a_call_past_its_timeout_takes_the_whole_process_down(attached: AttachedProcess):
    with pytest.raises(ToolTimedOut):
        attached.call("hang", {}, timeout=0.5)
    with pytest.raises(AttachedEnded):
        attached.call("add", {"n": 1}, timeout=10)


STAND_IN_MAIN = """
import sys, threading
from pathlib import Path
from allin.confinement.launcher import FprocStart, ToolLauncher

data_dir, package, title = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
launcher = ToolLauncher(data_dir)
launcher.start()
workspace = data_dir / "attached" / "default" / "notes"
workspace.mkdir(parents=True, exist_ok=True)
process = launcher.attach(FprocStart(package, "counter", "notes", workspace), timeout=20)
process.call("spawn", {"title": title}, timeout=10)
print(process.pid, flush=True)
threading.Event().wait()
"""


def test_killing_main_kills_the_attached_process_and_its_descendants(data_dir: Path, package: Path):
    title = unique_title()
    main = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(STAND_IN_MAIN), data_dir, package, title],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert main.stdout is not None
    warden = int(main.stdout.readline())
    assert alive(title)
    main.kill()
    main.wait()
    deadline = time.monotonic() + 5
    while (alive(title) or Path(f"/proc/{warden}").exists()) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(title)
    assert not Path(f"/proc/{warden}").exists() or _zombie(warden)


def _zombie(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] == "Z"
    except OSError:
        return True


def test_its_tmpdir_is_gone_once_it_ended(attached: AttachedProcess, data_dir: Path):
    assert list((data_dir / "tmp").glob("attached-*"))
    attached.detach()
    assert not list((data_dir / "tmp").glob("attached-*"))
    assert os.environ.get("TMPDIR") != str(data_dir / "tmp")
