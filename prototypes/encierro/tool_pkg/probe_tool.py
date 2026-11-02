"""#99: a stand-in function. Each check returns "ok" or the error it hit."""

import os
import subprocess
import sys
from collections.abc import Callable


def _try(fn: Callable[[], object]) -> str:
    try:
        fn()
    except BaseException as e:  # a probe reports every failure as data
        return f"{type(e).__name__}: {e}"[:160]
    return "ok"


def _imports() -> None:
    import decimal
    import hashlib
    import json
    import sqlite3
    import ssl

    import pydantic


def _mp_queue(method: str) -> None:
    import multiprocessing as mp

    ctx = mp.get_context(method)
    q = ctx.Queue()
    p = ctx.Process(target=_put, args=(q,))
    p.start()
    assert q.get(timeout=20) == 42
    p.join(20)
    assert p.exitcode == 0, p.exitcode


def _put(q: "object") -> None:
    q.put(42)  # pyright: ignore[reportAttributeAccessIssue]


def _mp_pool() -> None:
    import multiprocessing as mp

    with mp.get_context("spawn").Pool(2) as pool:
        assert pool.map(abs, [-1, -2]) == [1, 2]


def _threads() -> None:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as ex:
        assert list(ex.map(abs, [-1, -2])) == [1, 2]


def _asyncio() -> None:
    import asyncio

    asyncio.run(asyncio.sleep(0))


def _subprocess(*cmd: str) -> Callable[[], None]:
    def run() -> None:
        subprocess.run(cmd, check=True, capture_output=True, timeout=20)

    return run


def _tempfile() -> None:
    import tempfile

    fd, path = tempfile.mkstemp()
    os.close(fd)
    os.unlink(path)


def _write_work() -> None:
    with open("out.txt", "w") as f:
        f.write("x")
    os.replace("out.txt", "out2.txt")
    os.unlink("out2.txt")


def _read(path: str) -> Callable[[], None]:
    def run() -> None:
        with open(path, "rb") as f:
            f.read(16)

    return run


def _write_dev_null() -> None:
    with open("/dev/null", "w") as f:
        f.write("x")


def _getaddrinfo() -> None:
    import socket

    socket.getaddrinfo("example.com", 443, type=socket.SOCK_STREAM)


def _cpu_count() -> None:
    assert (os.cpu_count() or 0) > 0
    assert len(os.sched_getaffinity(0)) > 0


def basics() -> dict[str, str]:
    return {
        "imports (json, sqlite3, ssl, pydantic)": _try(_imports),
        "write in work (incl. atomic rename)": _try(_write_work),
        "tempfile in TMPDIR": _try(_tempfile),
        "threads": _try(_threads),
        "asyncio (socketpair self-pipe)": _try(_asyncio),
        "os.cpu_count / sched_getaffinity": _try(_cpu_count),
        "os.urandom": _try(lambda: os.urandom(8)),
        "subprocess ls": _try(_subprocess("ls")),
        "subprocess git --version": _try(_subprocess("git", "--version")),
        "subprocess python -c pass": _try(_subprocess(sys.executable, "-c", "pass")),
        "multiprocessing spawn + Queue": _try(lambda: _mp_queue("spawn")),
        "multiprocessing fork + Queue": _try(lambda: _mp_queue("fork")),
        "multiprocessing forkserver (3.14 default) + Queue": _try(lambda: _mp_queue("forkserver")),
        "multiprocessing spawn Pool": _try(_mp_pool),
        "open /dev/null for writing": _try(_write_dev_null),
        "read /dev/urandom": _try(_read("/dev/urandom")),
        "read /dev/zero": _try(_read("/dev/zero")),
        "read /dev/random": _try(_read("/dev/random")),
        "read /proc/self/status": _try(_read("/proc/self/status")),
        "read /proc/cpuinfo": _try(_read("/proc/cpuinfo")),
        "getaddrinfo example.com": _try(_getaddrinfo),
    }


def leaks(home: str, data_dir: str, abstract_name: str, sleep_arg: str) -> dict[str, str]:
    """Known exits. Each should fail under confinement."""
    import socket

    def dbus() -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(f"/run/user/{os.getuid()}/bus")

    def abstract() -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(abstract_name)  # Main listens there

    def io_uring() -> None:
        import ctypes

        params = (ctypes.c_char * 120)()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.syscall(425, 8, params) < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))

    def tiocsti() -> None:
        import fcntl
        import termios

        for fd in (0, 1, 2):
            fcntl.ioctl(fd, termios.TIOCSTI, b"x")

    def signal_main() -> None:
        os.kill(os.getppid(), 0)

    def server_port() -> None:
        s = socket.socket()
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", 9931))
        except ConnectionRefusedError:
            return  # allowed by the confinement, nothing listening
        finally:
            s.close()

    def read_home() -> None:
        os.listdir(home)

    def read_data() -> None:
        os.listdir(data_dir)

    def setsid_grandchild() -> None:
        # Without the tool's pipes, so that Main is not kept waiting and the count means
        # something; sleep_arg is a title unique to this run.
        subprocess.Popen(
            ["setsid", "sleep", sleep_arg],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    return {
        "D-Bus session bus (AF_UNIX path)": _try(dbus),
        "abstract unix socket Main listens on": _try(abstract),
        "io_uring_setup": _try(io_uring),
        "TIOCSTI on fds 0-2": _try(tiocsti),
        "signal Main (kill 0)": _try(signal_main),
        "connect Server port 9931": _try(server_port),
        "list home": _try(read_home),
        "list data dir": _try(read_data),
        "start a setsid grandchild": _try(setsid_grandchild),
    }
