"""#99: the first code a tool process runs. It confines itself, then imports the function.

Started by launch.py as `python -I boot.py <main_pid> <channel_fd>`; reads one JSON line with
the call from the channel (a socketpair end) and writes one JSON line back.
"""

import ctypes
import importlib
import json
import os
import signal
import socket
import sys
import time

T_START = time.perf_counter()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import confine as c


def born_state() -> dict[str, object]:
    """What this process inherited, read before confining (so /proc is still readable)."""
    fds: dict[int, str] = {}
    for n in sorted(int(n) for n in os.listdir("/proc/self/fd")):
        try:
            fds[n] = os.readlink(f"/proc/self/fd/{n}")
        except OSError:
            fds[n] = "(closed: the listdir's own fd)"
    ttys: dict[str, str] = {}
    for fd in fds:
        try:
            if os.isatty(fd):
                ttys[str(fd)] = os.ttyname(fd)
        except OSError:
            pass
    try:
        dev_tty = os.open("/dev/tty", os.O_RDWR)
        os.close(dev_tty)
        controlling = True
    except OSError:
        controlling = False
    with open("/proc/self/environ", "rb") as f:
        environ_keys = sorted(
            kv.split(b"=", 1)[0].decode() for kv in f.read().split(b"\0") if kv
        )
    return {
        "pid": os.getpid(),
        "sid_is_own": os.getsid(0) == os.getpid(),
        "pgid_is_own": os.getpgid(0) == os.getpid(),
        "fds": {str(k): v.split(":")[0] if ":" in v else v for k, v in fds.items()},
        "ttys": ttys,
        "controlling_tty": controlling,
        "environ_keys": environ_keys,
        "sys_path": list(sys.path),
        "cwd": os.getcwd(),
    }


def main() -> None:
    main_pid = int(sys.argv[1])
    channel = socket.socket(fileno=int(sys.argv[2]))
    libc = ctypes.CDLL(None, use_errno=True)
    # Die with the thread that launched us, then check it had not already died (the race).
    libc.prctl(c.PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    if os.getppid() != main_pid:
        os._exit(70)
    libc.prctl(c.PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    reader = channel.makefile("r", encoding="utf-8")
    call = json.loads(reader.readline())
    state = born_state() if call.get("report_born") else None
    shared_net = call.get("shared_net_fd")
    t_conf0 = time.perf_counter()
    if call.get("confine"):
        reach = c.Reach(
            read=tuple(call["read"]),
            write=tuple(call["write"]),
            net=None if call["net"] is None else (
                "all" if call["net"] == "all" else tuple(call["net"])
            ),
            display=bool(call.get("display")),
        )
        if shared_net is not None:
            # Two layers: this call's fs layer (which does not handle TCP), then Main's shared
            # net layer. Stacking copies the parent domain's rules into the new one, so the
            # 65 534-rule layer goes last to be copied once (net_layer_first measures the other).
            c.no_new_privs()
            if call.get("net_layer_first"):
                c.restrict_self(shared_net)
                _confine_fs_only(reach)
            else:
                _confine_fs_only(reach)
                c.restrict_self(shared_net)
            os.close(shared_net)
            c.apply_filter(c.build_filter(net=True, unix=reach.display))
        else:
            c.confine(reach)
    t_conf1 = time.perf_counter()
    os.chdir(call["work"])
    # Import only from the system, the venv and the function's package.
    sys.path[:] = [p for p in call["sys_path"]] + [call["package"]]
    sys.modules.pop("confine", None)
    module = importlib.import_module(call["module"])
    t_imp = time.perf_counter()
    result = getattr(module, call["function"])(**call.get("kwargs", {}))
    t_end = time.perf_counter()
    reply = {
        "result": result,
        "born": state,
        "timings_ms": {
            "boot_to_confine": (t_conf0 - T_START) * 1e3,
            "confine": (t_conf1 - t_conf0) * 1e3,
            "import": (t_imp - t_conf1) * 1e3,
            "call": (t_end - t_imp) * 1e3,
        },
    }
    channel.sendall((json.dumps(reply) + "\n").encode())
    if call.get("confine"):
        _kill_descendants()
    os._exit(0)


def _confine_fs_only(reach: c.Reach) -> None:
    ruleset = c.create_ruleset(
        fs=c.FS_ALL, scoped=c.SCOPE_ABSTRACT_UNIX_SOCKET | c.SCOPE_SIGNAL
    )
    for path in reach.read:
        c.add_path(ruleset, path, c.FS_READ)
    for path in reach.write:
        c.add_path(ruleset, path, c.FS_READ | c.FS_WRITE)
    c.restrict_self(ruleset)
    os.close(ruleset)


def _kill_descendants() -> None:
    # Only ever called once confined with LANDLOCK_SCOPE_SIGNAL: kill(-1) then reaches the
    # processes of this call's own domain and nothing else (reaper.py). Unconfined, it would
    # kill every process of the user.
    try:
        os.kill(-1, signal.SIGKILL)
    except ProcessLookupError:
        pass  # no descendants left


if __name__ == "__main__":
    main()
