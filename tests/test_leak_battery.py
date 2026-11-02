"""The leak battery (ADR-0016, #103): a tool with the minimum Alcance tries every known way out
of the Encierro, and every attempt must fail. It runs in the suite and in the pre-commit hook,
so a change that reopens a way out does not pass.

The ways out, one test each:

- the session's D-Bus bus, from which ``systemd --user`` starts anything unconfined;
- X11, by its socket with a path and by its abstract socket;
- io_uring, which makes sockets without passing through the seccomp filter;
- a signal to Main;
- ``ptrace`` on Main, and what shares its access check (``process_vm_readv``,
  ``pidfd_getfd``, ``/proc/<main>/mem``);
- the terminal: opening it, and typing into it with ``TIOCSTI`` or ``TIOCLINUX``;
- the SSH agent;
- a prepared ``.pyc`` that does not match its source;
- the ports of AllIn's Servers.

Where a target could simply be missing, its test first reaches it with a wide Alcance (the
control), so a refusal is the Encierro's and not an empty socket's.
"""

import ctypes
import importlib.machinery
import importlib.util
import json
import os
import py_compile
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement import launcher as launcher_module
from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolCall, ToolLauncher
from allin.core.errors import PackageNotSource
from allin.decorators import MINIMUM, Reach

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

PROBE = """
import ctypes, errno, fcntl, os, signal, socket, termios


def _errno(error):
    return errno.errorcode.get(error.errno, str(error.errno))


def _syscall(number, *args):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    # As longs: a variadic int leaves the register's upper half undefined.
    result = libc.syscall(ctypes.c_long(number), *(ctypes.c_long(a) if isinstance(a, int) else a for a in args))
    return "done" if result >= 0 else errno.errorcode[ctypes.get_errno()]


def unix_connect(address):
    # A leading NUL names an abstract socket, as X11's and D-Bus's older ones.
    name = "\\0" + address[1:] if address.startswith("@") else address
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.connect(name)
        return "connected"
    except OSError as error:
        return _errno(error)


def environment(names):
    return {name: name in os.environ for name in names}


def io_uring():
    params = ctypes.create_string_buffer(120)
    # With a bad descriptor, enter and register give EBADF unless the filter stops them.
    return {
        "setup": _syscall(425, 1, params),
        "enter": _syscall(426, -1, 0, 0, 0, None, 0),
        "register": _syscall(427, -1, 0, None, 0),
    }


def signals(main):
    out = {}
    for label, sig in (("probe", 0), ("SIGWINCH", signal.SIGWINCH)):
        try:
            os.kill(main, sig)
            out[label] = "sent"
        except OSError as error:
            out[label] = _errno(error)
    try:
        pidfd = os.pidfd_open(main)
    except OSError as error:
        out["pidfd_send_signal"] = _errno(error)
    else:
        try:
            signal.pidfd_send_signal(pidfd, 0)
            out["pidfd_send_signal"] = "sent"
        except OSError as error:
            out["pidfd_send_signal"] = _errno(error)
        finally:
            os.close(pidfd)
    return out


def trace(main):
    out = {"PTRACE_SEIZE": _syscall(101, 0x4206, main, 0, 0)}
    if out["PTRACE_SEIZE"] == "done":
        _syscall(101, 17, main, 0, 0)  # PTRACE_DETACH: never leave Main traced
    buffer = ctypes.create_string_buffer(8)
    local = (ctypes.c_void_p * 2)(ctypes.addressof(buffer), 8)
    remote = (ctypes.c_void_p * 2)(ctypes.addressof(buffer), 8)  # the same address in Main
    out["process_vm_readv"] = _syscall(310, main, local, 1, remote, 1, 0)
    try:
        pidfd = os.pidfd_open(main)
        try:
            out["pidfd_getfd"] = _syscall(438, pidfd, 0, 0)
        finally:
            os.close(pidfd)
    except OSError as error:
        out["pidfd_getfd"] = _errno(error)
    for name in ("mem", "environ"):
        try:
            with open(f"/proc/{main}/{name}", "rb") as file:
                file.read(1)
            out[name] = "read"
        except OSError as error:
            out[name] = _errno(error)
    return out


def terminal(slave):
    out = {}
    for label, path in (("main's terminal", slave), ("/dev/tty", "/dev/tty")):
        try:
            os.close(os.open(path, os.O_RDWR | os.O_NOCTTY))
            out[label] = "opened"
        except OSError as error:
            out[label] = _errno(error)
    for name, request in (("TIOCSTI", termios.TIOCSTI), ("TIOCLINUX", 0x541C)):
        for fd in (0, 1, 2):  # no terminal among them: ENOTTY if the filter let it through
            try:
                fcntl.ioctl(fd, request, b"x")
                out[f"{name} {fd}"] = "done"
            except OSError as error:
                out[f"{name} {fd}"] = _errno(error)
    return out


def tcp(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return "connected"
    except OSError as error:
        return _errno(error)


def bind(port):
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
        return "bound"
    except OSError as error:
        return _errno(error)


def udp(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"ping", ("127.0.0.1", port))
        return "sent"
    except OSError as error:
        return _errno(error)
"""

_PR_SET_PTRACER = 0x59616D61
_PR_SET_PTRACER_ANY = 2**64 - 1  # (unsigned long) -1

GENUINE = 'def run():\n    return "source"\n'
PLANTED = 'def run():\n    return "forged"\n'  # as long as GENUINE: its pyc's size matches


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "var"
    (data / "state").mkdir(parents=True)
    return data


@pytest.fixture
def package(data_dir: Path) -> Path:
    folder = data_dir / "profiles" / "default" / "functions" / "probe"
    folder.mkdir(parents=True)
    (folder / "probe.py").write_text(PROBE)
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


@pytest.fixture
def listening(tmp_path: Path) -> Iterator[dict[str, str]]:
    """Stand-ins for the session's sockets, so the battery never touches the user's own."""
    sockets: dict[str, str] = {
        "dbus": str(tmp_path / "bus"),
        "dbus abstract": f"@/tmp/dbus-allin-test-{os.getpid()}",
        "x11": str(tmp_path / ".X11-unix" / "X99"),
        "x11 abstract": f"@/tmp/.X11-unix/X-allin-test-{os.getpid()}",
        "ssh agent": str(tmp_path / "agent.sock"),
    }
    (tmp_path / ".X11-unix").mkdir()
    servers: list[socket.socket] = []
    for address in sockets.values():
        server = socket.socket(socket.AF_UNIX)
        server.bind("\0" + address[1:] if address.startswith("@") else address)
        server.listen()
        servers.append(server)
    yield sockets
    for server in servers:
        server.close()


def run(
    launcher: ToolLauncher,
    package: Path,
    workspace: Path,
    function: str,
    reach: Reach = MINIMUM,
    **arguments: object,
) -> object:
    outcome = launcher.run(
        ToolCall(
            package=package,
            module="probe",
            function=function,
            arguments=arguments,  # pyright: ignore[reportArgumentType]  # JSON values in tests
            workspace=workspace,
            reach=reach,
        ),
        timeout=20,
    )
    assert outcome.error is None, outcome.error
    return outcome.value


def test_dbus_session_bus_by_path_and_by_abstract_name(
    launcher: ToolLauncher,
    package: Path,
    workspace: Path,
    listening: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path={listening['dbus']}")
    for name in ("dbus", "dbus abstract"):
        assert run(launcher, package, workspace, "unix_connect", Reach(display=True), address=listening[name]) == "connected"  # fmt: skip
        assert run(launcher, package, workspace, "unix_connect", address=listening[name]) == "EPERM"  # fmt: skip
    told = run(launcher, package, workspace, "environment", names=["DBUS_SESSION_BUS_ADDRESS"])
    assert told == {"DBUS_SESSION_BUS_ADDRESS": False}


def test_x11_by_its_socket_with_a_path_and_by_its_abstract_socket(
    launcher: ToolLauncher, package: Path, workspace: Path, listening: dict[str, str]
):
    for name in ("x11", "x11 abstract"):
        assert run(launcher, package, workspace, "unix_connect", Reach(display=True), address=listening[name]) == "connected"  # fmt: skip
        assert run(launcher, package, workspace, "unix_connect", address=listening[name]) == "EPERM"  # fmt: skip


def test_io_uring(launcher: ToolLauncher, package: Path, workspace: Path):
    refused = run(launcher, package, workspace, "io_uring")
    assert refused == {"setup": "EPERM", "enter": "EPERM", "register": "EPERM"}


@pytest.fixture
def traceable_main() -> Iterator[int]:
    """Main as any process of the user may trace it, Yama's scope aside: so only the Encierro
    can refuse a tool's ``ptrace``. Its pid."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(_PR_SET_PTRACER, ctypes.c_ulong(_PR_SET_PTRACER_ANY), 0, 0, 0)
    yield os.getpid()
    libc.prctl(_PR_SET_PTRACER, 0, 0, 0, 0)


def unconfined(package: Path, function: str, *arguments: object) -> object:
    """The probe run by a plain child of Main, with no Encierro: the control."""
    code = f"import json, probe; print(json.dumps(probe.{function}(*{arguments!r})))"
    ran = subprocess.run(  # -B: no __pycache__, which Main would refuse the package for
        [sys.executable, "-B", "-c", code],
        cwd=package,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(ran.stdout)


def test_a_signal_to_main(launcher: ToolLauncher, package: Path, workspace: Path):
    main = os.getpid()
    assert unconfined(package, "signals", main) == {
        "probe": "sent",
        "SIGWINCH": "sent",  # ignored by default
        "pidfd_send_signal": "sent",
    }
    refused = run(launcher, package, workspace, "signals", main=main)
    assert refused == {"probe": "EPERM", "SIGWINCH": "EPERM", "pidfd_send_signal": "EPERM"}


def test_ptrace_on_main_and_what_shares_its_check(
    launcher: ToolLauncher, package: Path, workspace: Path, traceable_main: int
):
    control = unconfined(package, "trace", traceable_main)
    assert isinstance(control, dict)
    assert control["PTRACE_SEIZE"] == "done" and control["pidfd_getfd"] == "done"
    refused = run(launcher, package, workspace, "trace", main=traceable_main)
    assert refused == {
        "PTRACE_SEIZE": "EPERM",
        "process_vm_readv": "EPERM",
        "pidfd_getfd": "EPERM",
        "mem": "EACCES",
        "environ": "EACCES",
    }


def test_opening_the_terminal_and_typing_into_it(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    master, slave = os.openpty()  # Main's terminal, as the human's xterm would be
    try:
        os.set_blocking(master, False)
        tried = run(launcher, package, workspace, "terminal", slave=os.ttyname(slave))
        time.sleep(0.1)
        with pytest.raises(BlockingIOError):
            os.read(master, 1024)  # nothing reached it
    finally:
        os.close(master)
        os.close(slave)
    assert tried == {
        "main's terminal": "EACCES",
        "/dev/tty": "EACCES",
        **{f"{name} {fd}": "EPERM" for name in ("TIOCSTI", "TIOCLINUX") for fd in (0, 1, 2)},
    }


def test_the_ssh_agent(
    launcher: ToolLauncher,
    package: Path,
    workspace: Path,
    listening: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SSH_AUTH_SOCK", listening["ssh agent"])
    assert run(launcher, package, workspace, "unix_connect", Reach(display=True), address=listening["ssh agent"]) == "connected"  # fmt: skip
    assert run(launcher, package, workspace, "unix_connect", address=listening["ssh agent"]) == "EPERM"  # fmt: skip
    told = run(launcher, package, workspace, "environment", names=["SSH_AUTH_SOCK"])
    assert told == {"SSH_AUTH_SOCK": False}


def plant(package: Path, module: str, mode: py_compile.PycInvalidationMode) -> None:
    """``module.py`` holds GENUINE, and its ``.pyc`` was compiled from PLANTED: a timestamp
    one with the source's size and modification time, or an unchecked one, which Python
    loads without looking at the source at all."""
    source = package / f"{module}.py"
    source.write_text(PLANTED)
    stamp = source.stat().st_mtime_ns
    py_compile.compile(str(source), invalidation_mode=mode, doraise=True)
    source.write_text(GENUINE)
    os.utime(source, ns=(stamp, stamp))


PYC_MODES = [
    py_compile.PycInvalidationMode.TIMESTAMP,
    py_compile.PycInvalidationMode.UNCHECKED_HASH,
]

SPAWNS = """
import multiprocessing, subprocess, sys


def _child(end):
    import victim
    end.send(victim.run())


def through_children():
    context = multiprocessing.get_context("spawn")
    mine, theirs = context.Pipe()  # a socketpair: a Queue would need /dev/shm
    child = context.Process(target=_child, args=(theirs,))
    child.start()
    spawned = mine.recv()
    child.join()
    command = [sys.executable, "-c", "import victim; print(victim.run())"]
    env = {"PYTHONPATH": sys.path[-1]}
    ran = subprocess.run(command, capture_output=True, text=True, env=env, check=True)
    return {"multiprocessing": spawned, "python": ran.stdout.strip()}
"""


@pytest.mark.parametrize("mode", PYC_MODES)
def test_a_prepared_pyc_that_does_not_match_its_source(
    launcher: ToolLauncher, package: Path, workspace: Path, mode: py_compile.PycInvalidationMode
):
    plant(package, "victim", mode)
    assert Path(importlib.util.cache_from_source(str(package / "victim.py"))).exists()
    with pytest.raises(PackageNotSource) as refused:
        launcher.run(ToolCall(package, "victim", "run", {}, workspace=workspace), timeout=20)
    assert refused.value.fields["files"] == ["__pycache__", f"__pycache__/{cached_name()}"]


def test_a_pyc_or_a_compiled_module_without_its_source(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    for name in ("ghost.pyc", f"native{importlib.machinery.EXTENSION_SUFFIXES[0]}"):
        (package / name).write_bytes(b"\0compiled")
        with pytest.raises(PackageNotSource):
            launcher.run(ToolCall(package, "probe", "tcp", {"port": 1}, workspace), timeout=20)
        with pytest.raises(PackageNotSource):
            launcher.describe(package, "probe", timeout=20)
        (package / name).unlink()


@pytest.mark.parametrize("mode", PYC_MODES)
def test_a_pyc_that_gets_past_mains_check_is_not_imported_by_the_warden(
    launcher: ToolLauncher,
    package: Path,
    workspace: Path,
    mode: py_compile.PycInvalidationMode,
    monkeypatch: pytest.MonkeyPatch,
):
    # As if it were planted between Main's check and the import: the warden still compiles the
    # source. Its children do not (see through_children), which is why Main checks first.
    monkeypatch.setattr(launcher_module, "_refuse_compiled", _no_check)
    plant(package, "victim", mode)
    outcome = launcher.run(ToolCall(package, "victim", "run", {}, workspace=workspace), timeout=20)
    assert outcome.value == "source"


def test_with_no_pyc_the_functions_children_import_the_source(
    launcher: ToolLauncher, package: Path, workspace: Path
):
    (package / "victim.py").write_text(GENUINE)
    (package / "spawns.py").write_text(SPAWNS)
    outcome = launcher.run(ToolCall(package, "spawns", "through_children", {}, workspace), 20)
    assert outcome.error is None, outcome.stderr
    assert outcome.value == {"multiprocessing": "source", "python": "source"}


def _no_check(_package: Path) -> None:
    pass


def cached_name() -> str:
    return Path(importlib.util.cache_from_source("victim.py")).name


def test_the_ports_of_allins_servers(package: Path, workspace: Path, data_dir: Path):
    with (
        socket.create_server(("127.0.0.1", 0)) as server,
        socket.create_server(("127.0.0.1", 0)) as service,
    ):
        port, other = server.getsockname()[1], service.getsockname()[1]
        launcher = ToolLauncher(data_dir, server_ports=lambda: frozenset({port}))
        launcher.start()
        try:
            everything, listed = Reach(net=True), Reach(net=(port,))
            assert run(launcher, package, workspace, "tcp", everything, port=other) == "connected"
            tried = {
                "minimum": run(launcher, package, workspace, "tcp", port=port),
                "net=True": run(launcher, package, workspace, "tcp", everything, port=port),
                "listed": run(launcher, package, workspace, "tcp", listed, port=port),
                "udp minimum": run(launcher, package, workspace, "udp", port=port),
            }
            server.close()  # a sleeping Server: a tool must not take its port
            tried["bind net=True"] = run(
                launcher, package, workspace, "bind", everything, port=port
            )
        finally:
            launcher.stop()
    assert tried == {
        "minimum": "EPERM",  # no IP socket at all
        "net=True": "EACCES",
        "listed": "EACCES",
        "udp minimum": "EPERM",
        "bind net=True": "EACCES",
    }
