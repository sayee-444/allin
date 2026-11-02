"""The Function tool's process, confined for real (#100): these tests run on this kernel."""

import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolCall, ToolLauncher
from allin.core.errors import ToolNotStarted, ToolTimedOut

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

TOOLS = """
import os, socket, subprocess, sys, time
from pathlib import Path


def echo(text):
    return {"text": text, "cwd": os.getcwd()}


def attempt(paths):
    out = {}
    for label, path in paths.items():
        try:
            Path(path).read_bytes()
            out[label] = "read"
        except OSError as error:
            out[label] = type(error).__name__
    return out


def write(path):
    try:
        Path(path).write_text("x")
        return "written"
    except OSError as error:
        return type(error).__name__


def connect(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return "connected"
    except OSError as error:
        return type(error).__name__


def birth():
    try:
        os.close(os.open("/dev/tty", os.O_RDWR))
        controlling = True
    except OSError:
        controlling = False
    import json
    return {
        "env": sorted(os.environ),
        "ttys": [fd for fd in (0, 1, 2) if os.isatty(fd)],
        "controlling": controlling,
        "json": json.__file__,
        "sockets": [fd for fd in range(3, 256) if _kind(fd) == "socket"],
    }


def _kind(fd):
    import stat
    try:
        mode = os.fstat(fd).st_mode
    except OSError:
        return None
    return "socket" if stat.S_ISSOCK(mode) else "other"


def leave_grandchild(title):
    subprocess.Popen(
        ["setsid", "sleep", title],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return "left"


def hang(title):
    leave_grandchild(title)
    while True:
        pass


def use_tmp():
    tmp = os.environ["TMPDIR"]
    Path(tmp, "scratch.txt").write_text("x")
    return tmp


def shout():
    print("to stdout")
    print("to stderr", file=sys.stderr)
    return None


def fail():
    raise ValueError("bad input")


def filtered():
    import ctypes, errno, fcntl, termios
    out = {}
    try:
        socket.socket(socket.AF_UNIX).close()
        out["unix socket"] = "created"
    except OSError as error:
        out["unix socket"] = errno.errorcode[error.errno]
    pair = socket.socketpair()
    out["socketpair"] = "created"
    for end in pair:
        end.close()
    libc = ctypes.CDLL(None, use_errno=True)
    params = ctypes.create_string_buffer(120)
    created = libc.syscall(425, 1, params)  # io_uring_setup
    out["io_uring"] = "created" if created >= 0 else errno.errorcode[ctypes.get_errno()]
    for name, request in (("TIOCSTI", termios.TIOCSTI), ("TIOCLINUX", 0x541C)):
        try:
            fcntl.ioctl(0, request, b"x")  # on /dev/null: ENOTTY if the filter let it through
            out[name] = "done"
        except OSError as error:
            out[name] = errno.errorcode[error.errno]
    return out


def stop_warden_and_hang(title):
    # A child in the call's own process group, then the warden frozen: it cannot answer.
    subprocess.Popen(["sleep", title], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    os.kill(os.getppid(), 19)  # SIGSTOP
    while True:
        pass
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "var"
    (data / "state").mkdir(parents=True)
    (data / "state" / "main.json").write_text("{}")
    (data / "sessions").mkdir()
    return data


@pytest.fixture
def package(data_dir: Path) -> Path:
    folder = data_dir / "profiles" / "default" / "functions" / "probe"
    folder.mkdir(parents=True)
    (folder / "probe.py").write_text(TOOLS)
    return folder


@pytest.fixture
def workspace(data_dir: Path) -> Path:
    folder = data_dir / "workspaces" / "s1"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def launcher(data_dir: Path) -> Iterator[ToolLauncher]:
    launcher = ToolLauncher(data_dir)
    launcher.start()
    yield launcher
    launcher.stop()


def call(package: Path, workspace: Path, function: str, **arguments: object) -> ToolCall:
    return ToolCall(
        package=package,
        module="probe",
        function=function,
        arguments=arguments,  # pyright: ignore[reportArgumentType]  # JSON values in tests
        workspace=workspace,
    )


def alive(title: str) -> bool:
    pgrep = shutil.which("pgrep") or "/usr/bin/pgrep"
    found = subprocess.run([pgrep, "-f", "-x", f"sleep {title}"], capture_output=True, check=False)
    return bool(found.stdout.strip())


def unique_title() -> str:
    return f"60.{os.getpid()}{time.monotonic_ns() % 1_000_000}"


def test_a_function_runs_in_its_own_process_and_returns_its_result_from_the_workspace(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    outcome = launcher.run(call(package, workspace, "echo", text="hi"), timeout=20)
    assert outcome.value == {"text": "hi", "cwd": str(workspace)}
    assert outcome.error is None


def test_it_cannot_read_or_write_outside_the_minimum_reach_nor_open_the_network(
    launcher: ToolLauncher, package: Path, workspace: Path, data_dir: Path
):
    secrets = {"state": str(data_dir / "state" / "main.json"), "ssh": str(Path.home() / ".ssh")}
    read = launcher.run(call(package, workspace, "attempt", paths=secrets), timeout=20)
    assert read.value == {"state": "PermissionError", "ssh": "PermissionError"}
    wrote = launcher.run(
        call(package, workspace, "write", path=str(data_dir / "sessions" / "x")), timeout=20
    )
    assert wrote.value == "PermissionError"
    with socket.create_server(("127.0.0.1", 0)) as listening:
        port = listening.getsockname()[1]
        connected = launcher.run(call(package, workspace, "connect", port=port), timeout=20)
    assert connected.value == "PermissionError"
    mine = launcher.run(call(package, workspace, "write", path=str(workspace / "x")), timeout=20)
    assert mine.value == "written"


def test_it_is_born_without_a_terminal_mains_environment_or_the_workspace_on_its_path(
    launcher: ToolLauncher,
    package: Path,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/user/1000/ssh")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("ALLIN_TOKEN", "secret")
    (workspace / "json.py").write_text("raise SystemExit('planted json.py imported')\n")
    born = launcher.run(call(package, workspace, "birth"), timeout=20).value
    assert isinstance(born, dict)
    assert set(born["env"]) <= {"PATH", "LANG", "TMPDIR", "HOME"}  # pyright: ignore[reportArgumentType]
    assert born["ttys"] == [] and born["controlling"] is False
    assert born["sockets"] == [], "the function sees no channel: only the warden holds it"
    assert str(born["json"]).startswith(sys.base_prefix)


def test_an_orphaned_grandchild_dies_when_the_call_ends(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    title = unique_title()
    outcome = launcher.run(call(package, workspace, "leave_grandchild", title=title), timeout=20)
    assert outcome.value == "left"
    time.sleep(0.2)
    assert not alive(title)


def test_a_timeout_takes_the_call_and_its_descendants_down_with_a_clear_error(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    title = unique_title()
    started = time.monotonic()
    with pytest.raises(ToolTimedOut) as raised:
        launcher.run(call(package, workspace, "hang", title=title), timeout=1)
    assert raised.value.fields["seconds"] == 1
    assert time.monotonic() - started < 5
    time.sleep(0.2)
    assert not alive(title)


def test_the_calls_tmpdir_lives_in_the_data_folder_and_is_gone_when_it_ends(
    launcher: ToolLauncher, package: Path, workspace: Path, data_dir: Path
):
    tmp = launcher.run(call(package, workspace, "use_tmp"), timeout=20).value
    assert isinstance(tmp, str) and Path(tmp).is_relative_to(data_dir)
    assert not Path(tmp).exists()


def test_its_output_and_errors_come_back_as_data(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    shouted = launcher.run(call(package, workspace, "shout"), timeout=20)
    assert shouted.stdout == "to stdout\n" and shouted.stderr == "to stderr\n"
    failed = launcher.run(call(package, workspace, "fail"), timeout=20)
    assert failed.value is None
    assert failed.error == "ValueError: bad input"


STAND_IN_MAIN = """
import sys, threading
from pathlib import Path
from allin.confinement.launcher import ToolCall, ToolLauncher

data, package, workspace, title = map(Path, sys.argv[1:5])
launcher = ToolLauncher(data)
launcher.start()
call = ToolCall(package=package, module="probe", function="hang",
                arguments={"title": str(title)}, workspace=workspace)
threading.Thread(target=launcher.run, args=(call, 600), daemon=True).start()
print("running", flush=True)
threading.Event().wait()
"""


def test_killing_main_kills_the_tools_process_and_its_descendants(
    package: Path, workspace: Path, data_dir: Path
):
    title = unique_title()
    main = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(STAND_IN_MAIN), data_dir, package, workspace, title],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert main.stdout is not None
    assert main.stdout.readline() == "running\n"
    deadline = time.monotonic() + 10
    while not alive(title) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert alive(title), "the tool never started"
    main.kill()
    main.wait()
    deadline = time.monotonic() + 5
    while alive(title) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(title)


def test_the_filter_refuses_unix_sockets_io_uring_and_typing_into_a_terminal(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    refused = launcher.run(call(package, workspace, "filtered"), timeout=20).value
    assert refused == {
        "unix socket": "EPERM",
        "socketpair": "created",  # what multiprocessing needs
        "io_uring": "EPERM",
        "TIOCSTI": "EPERM",
        "TIOCLINUX": "EPERM",
    }


def test_main_kills_the_calls_process_group_when_its_warden_does_not_answer(
    package: Path, workspace: Path, data_dir: Path
):
    launcher = ToolLauncher(data_dir, grace=0.5)
    launcher.start()
    title = unique_title()
    try:
        with pytest.raises(ToolTimedOut):
            launcher.run(call(package, workspace, "stop_warden_and_hang", title=title), timeout=1)
    finally:
        launcher.stop()
    time.sleep(0.2)
    assert not alive(title)


def test_a_call_after_the_launcher_stopped_fails_instead_of_waiting_forever(
    package: Path, workspace: Path, data_dir: Path
):
    launcher = ToolLauncher(data_dir)
    launcher.start()
    launcher.stop()
    with pytest.raises(ToolNotStarted) as raised:
        launcher.run(call(package, workspace, "echo", text="late"), timeout=5)
    assert raised.value.fields == {"cause": "stopped"}


def test_starting_clears_the_tmpdirs_a_crashed_main_left_behind(data_dir: Path):
    left = data_dir / "tmp" / "call-0123456789abcdef"
    left.mkdir(parents=True)
    (left / "half-written").write_text("x")
    launcher = ToolLauncher(data_dir)
    launcher.start()
    launcher.stop()
    assert not left.exists()
