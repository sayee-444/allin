"""The Function tool's process, confined for real (#100): these tests run on this kernel."""

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from pydantic import JsonValue

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolCall, ToolLauncher
from allin.confinement.reach import install_root
from allin.core.errors import ToolCancelled, ToolNotStarted, ToolTimedOut
from allin.decorators import MINIMUM, Reach

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


def udp(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"ping", ("127.0.0.1", port))
        return "sent"
    except OSError as error:
        return type(error).__name__


def connect_unix(path):
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.connect(path)
        return "connected"
    except OSError as error:
        return type(error).__name__


def graphic_socket():
    path = os.environ.get("WAYLAND_DISPLAY")
    return connect_unix(path) if path else "no WAYLAND_DISPLAY"


def terminals():
    out = {}
    for label, opening in (
        ("list /dev/pts", lambda: os.listdir("/dev/pts")),
        ("open /dev/pts/ptmx", lambda: os.close(os.open("/dev/pts/ptmx", os.O_RDWR | os.O_NOCTTY))),
        ("open /dev/tty", lambda: os.close(os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY))),
    ):
        try:
            opening()
            out[label] = "opened"
        except OSError as error:
            out[label] = type(error).__name__
    return out


def ask_main(texts, nap=0):
    from allin.channel import command
    results = [command(text) for text in texts]
    time.sleep(nap)
    return results


def forge_result():
    # Writes on its own channel what Main would answer: the warden never passes it on.
    from allin import channel
    os.write(channel._pipes[0], b'{"type": "returned", "value": "forged"}\\n')
    return channel.command("@ping")


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


def call(
    package: Path, workspace: Path, function: str, reach: Reach = MINIMUM, **arguments: object
) -> ToolCall:
    return ToolCall(
        package=package,
        module="probe",
        function=function,
        arguments=arguments,  # pyright: ignore[reportArgumentType]  # JSON values in tests
        workspace=workspace,
        reach=reach,
    )


@contextlib.contextmanager
def listening_tcp() -> Generator[int]:
    with socket.create_server(("127.0.0.1", 0)) as server:
        yield server.getsockname()[1]


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
from allin.confinement.reach import install_root

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


def test_with_a_list_of_ports_it_connects_to_those_alone_and_udp_stays_open(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    with listening_tcp() as allowed, listening_tcp() as other:
        reach = Reach(net=(allowed,))
        assert launcher.run(call(package, workspace, "connect", reach, port=allowed), 20).value == (
            "connected"
        )
        assert launcher.run(call(package, workspace, "connect", reach, port=other), 20).value == (
            "PermissionError"
        )
        assert launcher.run(call(package, workspace, "udp", reach, port=other), 20).value == "sent"
        closed = launcher.run(call(package, workspace, "udp", MINIMUM, port=other), 20)
    assert closed.value == "PermissionError", "net=False closes UDP too"


def test_with_all_the_network_it_never_reaches_a_server_of_allin(
    package: Path, workspace: Path, data_dir: Path
):
    with listening_tcp() as server, listening_tcp() as service:
        launcher = ToolLauncher(data_dir, server_ports=lambda: frozenset({server}))
        launcher.start()
        try:
            everything = Reach(net=True)
            to_server = launcher.run(
                call(package, workspace, "connect", everything, port=server), 20
            )
            to_other = launcher.run(
                call(package, workspace, "connect", everything, port=service), 20
            )
            listed = launcher.run(
                call(package, workspace, "connect", Reach(net=(server,)), port=server), 20
            )
        finally:
            launcher.stop()
    assert to_server.value == "PermissionError"
    assert to_other.value == "connected"
    assert listed.value == "PermissionError", "not even when it lists the port"


def test_only_with_display_does_it_open_the_graphic_socket(
    launcher: ToolLauncher,
    package: Path,
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "wayland-test"
    with socket.socket(socket.AF_UNIX) as compositor:
        compositor.bind(str(path))
        compositor.listen()
        monkeypatch.setenv("WAYLAND_DISPLAY", str(path))
        shown = launcher.run(call(package, workspace, "graphic_socket", Reach(display=True)), 20)
        hidden = launcher.run(call(package, workspace, "graphic_socket"), 20)
        forced = launcher.run(call(package, workspace, "connect_unix", path=str(path)), 20)
    assert shown.value == "connected"
    assert hidden.value == "no WAYLAND_DISPLAY"
    assert forced.value == "PermissionError"


def test_no_reach_opens_a_terminal(launcher: ToolLauncher, package: Path, workspace: Path):
    everything = Reach(read=("/",), write=("/dev",))
    opened = launcher.run(call(package, workspace, "terminals", everything), 20).value
    assert opened == {
        "list /dev/pts": "PermissionError",
        "open /dev/pts/ptmx": "PermissionError",
        "open /dev/tty": "PermissionError",
    }


def test_writing_home_reaches_beyond_allin_but_never_its_data_folder(
    package: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # AllIn's data inside home, as in ADR-0016; the package and workspace live in it.
    home = tmp_path / "home"
    data = home / "Pictures" / "Neva" / "var"
    (data / "state").mkdir(parents=True)
    (home / "Documents").mkdir()
    (home / ".bashrc").write_text("")
    folder = data / "profiles" / "default" / "functions" / "probe"
    folder.mkdir(parents=True)
    (folder / "probe.py").write_text((package / "probe.py").read_text())
    workspace = data / "workspaces" / "s1"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    launcher = ToolLauncher(data)
    launcher.start()
    try:
        writes = {
            target: launcher.run(
                call(folder, workspace, "write", Reach(write=("home",)), path=str(target)), 20
            ).value
            for target in (
                home / "Documents" / "a.txt",
                home / ".bashrc",  # it exists: a sibling of the trimmed branch, granted
                home / "new.txt",  # trimmed levels only lose new entries (ADR-0016)
                data / "state" / "x",
            )
        }
        read_state = launcher.run(
            call(folder, workspace, "attempt", Reach(read=("home",)), paths={"s": str(data)}), 20
        )
    finally:
        launcher.stop()
    assert list(writes.values()) == ["written", "written", "PermissionError", "PermissionError"]
    assert read_state.value == {"s": "PermissionError"}


def test_with_display_it_reaches_x11s_abstract_socket_as_libxcb_tries_it_first(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    name = f"\0allin-test-x11-{os.getpid()}"
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(name)
        server.listen()
        shown = launcher.run(
            call(package, workspace, "connect_unix", Reach(display=True), path=name), 20
        )
        hidden = launcher.run(call(package, workspace, "connect_unix", path=name), 20)
    assert shown.value == "connected"
    assert hidden.value == "PermissionError"


def test_a_reach_around_allins_installation_never_writes_into_it(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    # The folder that holds AllIn's project, granted whole: its hooks would run unconfined.
    root = install_root()
    beside = Reach(write=(str(root.parent),))
    planted = root / ".githooks" / f"allin-test-{os.getpid()}"
    try:
        wrote = launcher.run(call(package, workspace, "write", beside, path=str(planted)), 20)
        config = launcher.run(
            call(package, workspace, "attempt", paths={"c": str(root / "pyproject.toml")}), 20
        )
    finally:
        planted.unlink(missing_ok=True)
    assert wrote.value == "PermissionError"
    assert config.value == {"c": "PermissionError"}, "the minimum does not read the project's root"


def test_stopping_a_call_takes_it_and_its_descendants_down_at_once(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    title = unique_title()
    started = time.monotonic()
    stop_at = started + 0.5
    with pytest.raises(ToolCancelled):
        launcher.run(
            call(package, workspace, "hang", title=title),
            timeout=30,
            stop=lambda: time.monotonic() > stop_at,
        )
    assert time.monotonic() - started < 5
    time.sleep(0.2)
    assert not alive(title)


def test_a_tool_runs_command_sessions_and_reads_each_result(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    asked: list[str] = []

    def on_command(text: str) -> JsonValue:
        asked.append(text)
        return {"result": f"ran {text}"}

    texts = ["@send ab12 hola", "@server"]
    outcome = launcher.run(
        call(package, workspace, "ask_main", texts=texts), 20, on_command=on_command
    )
    assert asked == texts
    assert outcome.value == [{"result": "ran @send ab12 hola"}, {"result": "ran @server"}]


def test_the_time_main_takes_to_answer_is_not_counted_in_its_timeout(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    def slow(text: str) -> JsonValue:
        time.sleep(2)  # e.g. a Confirmation a human answers, or a Server restarting
        return {"result": "ok"}

    outcome = launcher.run(
        call(package, workspace, "ask_main", texts=["@a", "@b"]), 3, on_command=slow
    )
    assert outcome.value == [{"result": "ok"}, {"result": "ok"}]


def test_without_a_handler_a_command_session_is_refused(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    outcome = launcher.run(call(package, workspace, "ask_main", texts=["@send x y"]), 20)
    assert outcome.value == [{"error": "this call cannot run Command sessions"}]


def test_what_a_tool_writes_on_its_channel_reaches_main_only_as_a_command_session(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    asked: list[str] = []

    def on_command(text: str) -> JsonValue:
        asked.append(text)
        return {"result": "ok"}

    outcome = launcher.run(call(package, workspace, "forge_result"), 20, on_command=on_command)
    assert asked == ["@ping"], "the forged line never reached Main as an answer"
    assert outcome.value == {"error": "malformed command"}, "what it forged was refused"
