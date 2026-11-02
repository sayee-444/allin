"""#99: Main's side of the prototype launcher, and the measurements that use it.

Usage: python -I prototypes/encierro/launch.py {probe|born|cost|pickle|tiocsti} <scratch dir>
<scratch dir> stands for the data folder: work dirs and TMPDIRs are created in it.
"""

import json
import multiprocessing as mp
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time
from typing import Literal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import confine as c

PYTHON = sys.executable
INSTALL = os.path.dirname(os.path.dirname(HERE))  # the repo: AllIn's install folder
VENV = sys.prefix
HOME = os.path.expanduser("~")
SYS_PATH = [p for p in sys.path if p != HERE]  # stdlib + venv (+ src, editable install)
DATA_DIR = os.path.join(INSTALL, "var")


def trimmed(root: str, keep_out: str) -> list[str]:
    """ADR-0016's trimming: `root` minus the branch that leads to `keep_out`."""
    if not keep_out.startswith(root.rstrip("/") + "/"):
        return [root]
    out: list[str] = []
    here = root
    for part in os.path.relpath(keep_out, root).split("/"):
        out += [os.path.join(here, e) for e in os.listdir(here) if e != part]
        here = os.path.join(here, part)
    return out


SYSTEM_READ = ["/usr", "/etc", VENV, *trimmed(INSTALL, DATA_DIR)]
PACKAGE = os.path.join(HERE, "tool_pkg")


def tool_call(
    scratch: str,
    *,
    module: str = "probe_tool",
    function: str = "basics",
    kwargs: dict[str, object] | None = None,
    confine: bool = True,
    extra_read: tuple[str, ...] = (),
    extra_write: tuple[str, ...] = (),
    net: tuple[int, ...] | list[int] | Literal["all"] | None = None,
    shared_net_fd: int | None = None,
    display: bool = False,
    report_born: bool = False,
    net_layer_first: bool = False,
    timeout: float = 120,
) -> tuple[dict[str, object], float]:
    """Launch one confined call: subprocess with a socketpair, clean env, own session."""
    t0 = time.perf_counter()
    work = os.path.join(scratch, "workspaces", "s1")
    tmp = os.path.join(scratch, "tmp", f"call-{os.getpid()}-{time.monotonic_ns()}")
    os.makedirs(work, exist_ok=True)
    os.makedirs(tmp)
    parent, child = socket.socketpair()
    pass_fds = [child.fileno()]
    if shared_net_fd is not None:
        pass_fds.append(shared_net_fd)
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": tmp, "HOME": work}
    proc = subprocess.Popen(
        [PYTHON, "-I", os.path.join(HERE, "boot.py"), str(os.getpid()), str(child.fileno())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=work,
        start_new_session=True,
        close_fds=True,
        pass_fds=pass_fds,
    )
    child.close()
    call = {
        "confine": confine,
        "read": [*SYSTEM_READ, PACKAGE, *extra_read],
        "write": [work, tmp, *extra_write],
        "net": net,
        "display": display,
        "shared_net_fd": shared_net_fd,
        "work": work,
        "sys_path": SYS_PATH,
        "package": PACKAGE,
        "module": module,
        "function": function,
        "kwargs": kwargs or {},
        "report_born": report_born,
        "net_layer_first": net_layer_first,
    }
    parent.sendall((json.dumps(call) + "\n").encode())
    parent.settimeout(timeout)
    line = parent.makefile("r", encoding="utf-8").readline()
    parent.close()
    try:
        _, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        _, err = proc.communicate()
    elapsed = (time.perf_counter() - t0) * 1e3
    shutil.rmtree(tmp, ignore_errors=True)
    if not line:
        raise RuntimeError(f"no reply, rc={proc.returncode}: {err.decode()[-2000:]}")
    return json.loads(line), elapsed


def shared_net_layer(servers: frozenset[int] = frozenset({9931})) -> int:
    fd = c.create_ruleset(fs=0, net=c.NET_BIND_TCP | c.NET_CONNECT_TCP)
    c.add_all_ports_but(fd, servers)
    os.set_inheritable(fd, True)
    return fd


# --- question 2: /proc and /dev ---------------------------------------------------------------


def probe(scratch: str) -> None:
    dev = ("/dev/zero", "/dev/urandom", "/dev/random")
    variants: dict[str, dict[str, tuple[str, ...]]] = {
        "minimum (system, venv, package, work, TMPDIR)": {},
        "+ /dev/null rw": {"extra_write": ("/dev/null",)},
        "+ /dev/null rw, /dev/zero /dev/urandom /dev/random r": {
            "extra_read": dev,
            "extra_write": ("/dev/null",),
        },
        "+ those, /dev/shm rw": {"extra_read": dev, "extra_write": ("/dev/null", "/dev/shm")},
        "+ those, /dev/shm rw, /proc r": {
            "extra_read": (*dev, "/proc"),
            "extra_write": ("/dev/null", "/dev/shm"),
        },
    }
    report: dict[str, object] = {}
    unconfined, _ = tool_call(scratch, confine=False, net="all")
    report["unconfined"] = unconfined["result"]
    for name, extra in variants.items():
        reply, _ = tool_call(scratch, net=None, **extra)  # pyright: ignore[reportArgumentType]
        report[name] = reply["result"]
    fd = shared_net_layer()
    for label, resolve in (("without", ()), ("with", ("/run/systemd/resolve",))):
        reply, _ = tool_call(
            scratch,
            net="all",
            shared_net_fd=fd,
            extra_read=(*dev, *resolve),
            extra_write=("/dev/null", "/dev/shm"),
        )
        report[f"net=True (shared layer), {label} /run/systemd/resolve r"] = reply["result"]

    # Leaks. An abstract unix socket that Main listens on, so a refused connect means the
    # confinement and not "nobody there"; display=True lets AF_UNIX through seccomp, so that
    # case shows Landlock's own scope.
    abstract_name = f"\0allin-probe-{os.getpid()}"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(abstract_name)
    listener.listen(8)
    cases: list[tuple[str, dict[str, object]]] = [
        ("net=False", {"net": None}),
        ("net=True", {"net": "all", "shared_net_fd": fd}),
        ("display=True (AF_UNIX allowed by seccomp)", {"net": None, "display": True}),
        ("unconfined", {"confine": False}),
    ]
    for i, (label, kw) in enumerate(cases):
        sleep_arg = f"30.{os.getpid()}{i}"  # a title no other process has
        reply, _ = tool_call(
            scratch,
            function="leaks",
            kwargs={
                "home": HOME,
                "data_dir": DATA_DIR,
                "abstract_name": abstract_name,
                "sleep_arg": sleep_arg,
            },
            extra_read=("/proc",),
            extra_write=("/dev/null",),
            **kw,  # pyright: ignore[reportArgumentType]
        )
        result = reply["result"]
        assert isinstance(result, dict)
        time.sleep(0.3)
        alive = subprocess.run(
            ["pgrep", "-f", "-x", f"sleep {sleep_arg}"], capture_output=True, text=True, check=False
        ).stdout.split()
        result["setsid grandchild alive after the call"] = str(bool(alive))
        for pid in alive:  # matched by this run's own unique title
            os.kill(int(pid), signal.SIGKILL)
        report[f"leaks, {label}"] = result
    listener.close()
    os.close(fd)
    print(json.dumps(report, indent=2))


# --- question 3: born clean -------------------------------------------------------------------


def _mp_child_state(q: "mp.Queue[dict[str, object]]") -> None:
    import boot

    q.put(boot.born_state())


def born(scratch: str) -> None:
    """Make Main dirty (a terminal on 0-2, secrets in env, a stray inheritable fd, a json.py in
    its cwd) and compare what each launcher's child inherits."""
    master, slave = os.openpty()
    for fd in (0, 1, 2):
        os.dup2(slave, fd)
    os.environ["ALLIN_PROBE_TOKEN"] = "secret"
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/1000/bus"
    stray = os.open("/dev/null", os.O_RDONLY)
    os.set_inheritable(stray, True)
    dirty_cwd = os.path.join(scratch, "dirty-cwd")
    os.makedirs(dirty_cwd, exist_ok=True)
    with open(os.path.join(dirty_cwd, "json.py"), "w") as f:
        f.write("raise SystemExit('planted json.py imported')\n")
    os.chdir(dirty_cwd)
    report: dict[str, object] = {"main": {"stray_fd": stray, "pty_slave": os.ttyname(0)}}
    ctx = mp.get_context("spawn")
    q: mp.Queue[dict[str, object]] = ctx.Queue()
    p = ctx.Process(target=_mp_child_state, args=(q,))
    p.start()
    report["multiprocessing spawn"] = q.get(timeout=30)
    p.join()
    reply, _ = tool_call(scratch, confine=False, report_born=True)
    report["subprocess + socketpair"] = reply["born"]
    os.close(master)
    sys.stdout = open(os.path.join(scratch, "born.json"), "w")
    print(json.dumps(report, indent=2))


# --- question 3 (cont.): what Main trusts on the channel --------------------------------------


def _evil_put(q: "mp.Queue[object]", marker: str) -> None:
    import pickle

    class Evil:
        def __reduce__(self) -> tuple[object, tuple[str]]:
            return (open, (marker, "w"))  # harmless: Main creates an empty marker file

    q._sem.acquire()  # pyright: ignore - the child owns the queue internals too
    q._writer.send_bytes(pickle.dumps(Evil()))  # pyright: ignore


def pickle_demo(scratch: str) -> None:
    marker = os.path.join(scratch, "unpickled-in-main")
    if os.path.exists(marker):
        os.unlink(marker)
    ctx = mp.get_context("spawn")
    q: mp.Queue[object] = ctx.Queue()
    p = ctx.Process(target=_evil_put, args=(q, marker))
    p.start()
    got = q.get(timeout=30)
    p.join()
    print(json.dumps({"main_got": repr(got), "marker_created_by_main": os.path.exists(marker)}))


# --- seccomp alone: TIOCSTI with a real terminal ----------------------------------------------


def tiocsti(_scratch: str) -> None:
    import fcntl
    import termios

    out: dict[str, str] = {}
    for label, filtered in (("without filter", False), ("with filter", True)):
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            master, slave = os.openpty()
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            if filtered:
                c.no_new_privs()
                c.apply_filter(c.build_filter(net=False, unix=False))
            try:
                fcntl.ioctl(slave, termios.TIOCSTI, b"x")
                msg = "injected"
            except OSError as e:
                msg = f"{type(e).__name__}: {e}"
            os.write(w, msg.encode())
            os._exit(0)
        os.close(w)
        out[label] = os.read(r, 200).decode()
        os.waitpid(pid, 0)
    print(json.dumps(out, indent=2))


# --- question 5: cost per call ----------------------------------------------------------------


def _noop() -> int:
    return 1


def _noop_to(q: "mp.Queue[int]") -> None:
    q.put(_noop())


def cost(scratch: str) -> None:
    runs = 30
    results: dict[str, dict[str, float]] = {}

    def summarize(name: str, samples: list[float]) -> None:
        samples.sort()
        results[name] = {
            "median_ms": round(statistics.median(samples), 1),
            "p90_ms": round(statistics.quantiles(samples, n=10)[8], 1),
        }

    ctx = mp.get_context("spawn")
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        p = ctx.Process(target=_noop)
        p.start()
        p.join()
        samples.append((time.perf_counter() - t0) * 1e3)
    summarize("multiprocessing spawn, no confinement, no result", samples)
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        q = ctx.Queue()
        p = ctx.Process(target=_noop_to, args=(q,))
        p.start()
        assert q.get(timeout=30) == 1
        p.join()
        samples.append((time.perf_counter() - t0) * 1e3)
    summarize("multiprocessing spawn + Queue with the result (ADR-0005 today)", samples)

    fd = shared_net_layer()
    cases: dict[str, dict[str, object]] = {
        "subprocess+socketpair, no confinement": {"confine": False},
        "+ confinement, net=False": {"net": None},
        "+ confinement, net=[443]": {"net": [443]},
        "+ confinement, net=True built in the child": {"net": "all"},
        "+ confinement, net=True, Main's shared layer below the fs layer": {
            "net": "all",
            "shared_net_fd": fd,
            "net_layer_first": True,
        },
        "+ confinement, net=True, Main's shared layer last": {"net": "all", "shared_net_fd": fd},
    }
    phases: dict[str, dict[str, float]] = {}
    for name, kw in cases.items():
        samples = []
        confine_ms: list[float] = []
        boot_ms: list[float] = []
        for _ in range(runs):
            reply, elapsed = tool_call(scratch, function="noop", module="noop_tool", **kw)  # pyright: ignore[reportArgumentType]
            samples.append(elapsed)
            timings = reply["timings_ms"]
            assert isinstance(timings, dict)
            confine_ms.append(float(timings["confine"]))  # pyright: ignore[reportUnknownArgumentType]
            boot_ms.append(float(timings["boot_to_confine"]))  # pyright: ignore[reportUnknownArgumentType]
        summarize(name, samples)
        phases[name] = {
            "boot_to_confine_ms": round(statistics.median(boot_ms), 1),
            "confine_ms": round(statistics.median(confine_ms), 1),
        }
    os.close(fd)
    print(json.dumps({"end_to_end": results, "inside_child": phases}, indent=2))


if __name__ == "__main__":
    mode, scratch_dir = sys.argv[1], os.path.abspath(sys.argv[2])
    os.makedirs(scratch_dir, exist_ok=True)
    {"probe": probe, "born": born, "cost": cost, "pickle": pickle_demo, "tiocsti": tiocsti}[mode](
        scratch_dir
    )
