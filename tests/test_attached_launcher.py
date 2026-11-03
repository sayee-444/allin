"""A Proceso adherido's process (#113, ADR-0012, ADR-0016): started from an fproc by the Lanzador
de tools, confined for good with its Alcance and its shared file, with its own Carpeta de
trabajo as current folder; its methods are served one at a time until it is detached, it
crashes or Main dies, and it never starts again by itself."""

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import JsonValue

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import AttachedProcess, FprocStart, ToolLauncher
from allin.core.errors import (
    AttachedChanged,
    AttachedEnded,
    AttachFailed,
    ToolCancelled,
    ToolTimedOut,
)
from allin.core.ids import AttachedName, FprocName
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

    def ask(self, text: str) -> object:
        from allin.channel import command

        return [command(text), command(text + " again")]

    def slow_add(self, n: int, seconds: float) -> int:
        time.sleep(seconds)
        return self.add(n)

    def _hidden(self) -> int:
        return 1
'''

SHARED = '''
import threading
import time

from allin.decorators import commutative, concurrent, writes
from allin.fproc import Fproc


@concurrent(2)
class Shared(Fproc):
    """A text two Sessions share."""

    def __init__(self):
        self.text = ""
        self.lines = []
        self.running = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.left = []

    def read(self) -> str:
        return self.text

    @writes
    def write(self, text: str) -> str:
        self.text = text
        return text

    @commutative
    def append(self, line: str) -> int:
        self.lines.append(line)
        return len(self.lines)

    def edit_by_hand(self, text: str) -> None:
        """As the human would, in its own window: from a thread of its own."""

        def edit():
            self.text = text
            self.changed()

        hand = threading.Thread(target=edit)
        hand.start()
        hand.join()

    def hold(self, seconds: float) -> int:
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        time.sleep(seconds)
        with self.lock:
            self.running -= 1
        return self.peak

    def long(self, steps: int, pause: float) -> int:
        for step in range(steps):
            time.sleep(pause)
            self.progress(f"step {step + 1} of {steps}")
        return steps

    def patient(self, name: str) -> str:
        while not self.cancelled():
            time.sleep(0.05)
        self.left.append(name)
        return "left"

    def left_ones(self) -> list:
        return self.left

    def stubborn(self) -> None:
        time.sleep(600)
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
    (folder / "shared.py").write_text(SHARED)
    return folder


@pytest.fixture
def launcher(data_dir: Path) -> Iterator[ToolLauncher]:
    launcher = ToolLauncher(data_dir, grace=1.0)
    launcher.start()
    yield launcher
    launcher.stop()


def start(
    data_dir: Path,
    package: Path,
    file: Path | None = None,
    module: str = "counter",
    at_once: int | None = 1,
) -> FprocStart:
    workspace = data_dir / "attached" / "default" / "notes"
    workspace.mkdir(parents=True, exist_ok=True)
    return FprocStart(
        package, FprocName(module), AttachedName("notes"), workspace, file=file, at_once=at_once
    )


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


def test_a_method_runs_command_sessions_through_main_while_it_serves_a_call(
    attached: AttachedProcess,
):
    asked: list[str] = []

    def on_command(text: str) -> JsonValue:
        asked.append(text)
        return {"result": f"ran {text}"}

    answer = attached.call("ask", {"text": "@send ab hi"}, timeout=10, on_command=on_command)
    assert answer.value == [{"result": "ran @send ab hi"}, {"result": "ran @send ab hi again"}]
    assert asked == ["@send ab hi", "@send ab hi again"]
    refused = attached.call("ask", {"text": "@send ab hi"}, timeout=10).value
    assert isinstance(refused, list) and "error" in str(refused[0])


def test_a_call_whose_turn_was_cancelled_leaves_the_next_one_its_own_answer(
    attached: AttachedProcess,
):
    stopped = time.monotonic() + 0.3
    with pytest.raises(ToolCancelled):
        attached.call(
            "slow_add", {"n": 5, "seconds": 1}, timeout=10, stop=lambda: time.monotonic() > stopped
        )
    assert attached.call("add", {"n": 1}, timeout=10).value == 6


def test_waiting_for_another_call_to_end_can_be_cancelled(attached: AttachedProcess):
    first = threading.Thread(
        target=lambda: attached.call("slow_add", {"n": 1, "seconds": 2}, timeout=10)
    )
    first.start()
    time.sleep(0.3)  # the first call holds the process
    began = time.monotonic()
    with pytest.raises(ToolCancelled):
        attached.call("add", {"n": 1}, timeout=10, stop=lambda: time.monotonic() > began + 0.2)
    assert time.monotonic() - began < 1.5, "it did not wait for the first call to end"
    first.join()
    assert attached.call("add", {"n": 1}, timeout=10).value == 2, "nothing owed: never sent"


def test_a_slow_cancelled_call_does_not_spend_the_next_calls_time(attached: AttachedProcess):
    stopped = time.monotonic() + 0.2
    with pytest.raises(ToolCancelled):
        attached.call(
            "slow_add",
            {"n": 5, "seconds": 1.5},
            timeout=10,
            stop=lambda: time.monotonic() > stopped,
        )
    assert attached.call("add", {"n": 1}, timeout=1.2).value == 6


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


@pytest.fixture
def shared(launcher: ToolLauncher, data_dir: Path, package: Path) -> Iterator[AttachedProcess]:
    """``Shared``, two calls at a time as its ``@concurrent(2)`` says; a call past its timeout
    gets half a second to end."""
    process = launcher.attach(
        start(data_dir, package, module="shared", at_once=2), timeout=20, release=0.5
    )
    yield process
    process.detach()


def test_a_write_from_a_session_that_did_not_see_the_last_one_is_refused(
    shared: AttachedProcess,
):
    seen_a = shared.call("read", {}, timeout=10, seen=-1).revision
    seen_b = shared.call("read", {}, timeout=10, seen=-1).revision
    assert seen_a == seen_b == 0
    wrote = shared.call("write", {"text": "de A"}, timeout=10, seen=seen_a)
    assert (wrote.value, wrote.revision) == ("de A", 1)
    with pytest.raises(AttachedChanged) as refused:
        shared.call("write", {"text": "de B"}, timeout=10, seen=seen_b)
    assert refused.value.fields["revision"] == 1
    assert shared.call("read", {}, timeout=10).value == "de A", "B's write never ran"
    seen_b = shared.call("read", {}, timeout=10, seen=seen_b).revision
    assert shared.call("write", {"text": "de B"}, timeout=10, seen=seen_b).value == "de B"


def test_a_change_the_fproc_marks_also_asks_to_read_again(shared: AttachedProcess):
    seen = shared.call("read", {}, timeout=10, seen=-1).revision
    shared.call("edit_by_hand", {"text": "a mano"}, timeout=10)
    with pytest.raises(AttachedChanged):
        shared.call("write", {"text": "encima"}, timeout=10, seen=seen)


def test_a_commutative_write_is_never_refused_nor_counts_as_a_read(shared: AttachedProcess):
    shared.call("write", {"text": "x"}, timeout=10)  # a human's: nobody checks it
    appended = shared.call("append", {"line": "una"}, timeout=10, seen=0)
    assert (appended.value, appended.revision) == (1, None)


def test_concurrent_two_runs_two_calls_at_once_and_holds_the_third(shared: AttachedProcess):
    peaks: list[int] = []

    def hold() -> None:
        value = shared.call("hold", {"seconds": 0.6}, timeout=10).value
        assert isinstance(value, int)
        peaks.append(value)

    began = time.monotonic()
    holders = [threading.Thread(target=hold) for _ in range(3)]
    for holder in holders:
        holder.start()
    for holder in holders:
        holder.join()
    assert max(peaks) == 2, "never three at once"
    assert time.monotonic() - began >= 1.2, "the third waited for a free turn"


def test_a_call_that_tells_its_progress_does_not_time_out(shared: AttachedProcess):
    told: list[str] = []
    outcome = shared.call("long", {"steps": 5, "pause": 0.3}, timeout=0.6, on_progress=told.append)
    assert outcome.value == 5
    assert told == [f"step {n} of 5" for n in range(1, 6)]


def test_cancelling_a_call_reaches_its_method(shared: AttachedProcess):
    stopped = time.monotonic() + 0.3
    with pytest.raises(ToolCancelled):
        shared.call("patient", {"name": "a"}, timeout=10, stop=lambda: time.monotonic() > stopped)
    for _ in range(40):
        if shared.call("left_ones", {}, timeout=10).value == ["a"]:
            break
        time.sleep(0.05)
    assert shared.call("left_ones", {}, timeout=10).value == ["a"]


def test_a_call_that_does_not_end_after_its_timeout_takes_the_whole_process_down(
    shared: AttachedProcess,
):
    began = time.monotonic()
    with pytest.raises(ToolTimedOut):
        shared.call("stubborn", {}, timeout=0.3)
    assert time.monotonic() - began < 1, "the call fails at its timeout"
    assert shared.wait() == -signal.SIGKILL
    assert shared.hung is not None and "stubborn" in shared.hung
    with pytest.raises(AttachedEnded):
        shared.call("read", {}, timeout=10)


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
