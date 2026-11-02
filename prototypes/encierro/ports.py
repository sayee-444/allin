"""#99, question 1: the cost of `net=True` (every TCP port but the Servers') in Landlock.

Each measurement runs in a fresh child, since a Landlock domain cannot be undone.
Usage: python -I prototypes/encierro/ports.py
"""

import json
import os
import socket
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import confine as c

SERVERS = frozenset({9931, 9932})
RUNS = 15


def child_inline() -> dict[str, float]:
    """Build the 65 534 port rules in this process and apply them."""
    t0 = time.perf_counter()
    c.no_new_privs()
    rs = c.create_ruleset(fs=0, net=c.NET_BIND_TCP | c.NET_CONNECT_TCP)
    t1 = time.perf_counter()
    c.add_all_ports_but(rs, SERVERS)
    t2 = time.perf_counter()
    c.restrict_self(rs)
    t3 = time.perf_counter()
    os.close(rs)
    return {"build_ms": (t2 - t1) * 1e3, "restrict_ms": (t3 - t2) * 1e3, "total_ms": (t3 - t0) * 1e3}


def child_inherited(fd: int) -> dict[str, float]:
    """Apply a ruleset Main built once and passed down as a descriptor."""
    t0 = time.perf_counter()
    c.no_new_privs()
    c.restrict_self(fd)
    os.close(fd)
    t1 = time.perf_counter()
    return {"total_ms": (t1 - t0) * 1e3}


def child_check() -> dict[str, object]:
    """After the inherited net layer plus an fs layer: Server ports refused, others allowed."""
    fs = c.create_ruleset(fs=c.FS_ALL, scoped=c.SCOPE_SIGNAL | c.SCOPE_ABSTRACT_UNIX_SOCKET)
    c.add_path(fs, "/usr", c.FS_READ)
    c.restrict_self(fs)
    out: dict[str, object] = {}
    for port in (9931, 9932, 9933, 443):
        s = socket.socket()
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            out[str(port)] = "connected"
        except PermissionError:
            out[str(port)] = "EACCES (Landlock)"
        except OSError as e:
            out[str(port)] = f"allowed by Landlock ({type(e).__name__})"
        finally:
            s.close()
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 9931))
        out["bind 9931"] = "bound"
    except PermissionError:
        out["bind 9931"] = "EACCES (Landlock)"
    finally:
        s.close()
    t0 = time.perf_counter()
    for _ in range(1000):
        s = socket.socket()
        try:
            s.connect(("127.0.0.1", 9933))
        except OSError:
            pass
        s.close()
    out["connect_refused_us"] = (time.perf_counter() - t0) * 1e3
    return out


def run_child(mode: str, pass_fd: int | None = None) -> dict[str, object]:
    args = [sys.executable, "-I", __file__, mode]
    if pass_fd is not None:
        args.append(str(pass_fd))
    out = subprocess.run(
        args, capture_output=True, text=True, check=True, pass_fds=() if pass_fd is None else (pass_fd,)
    )
    return json.loads(out.stdout)


def main() -> None:
    inline = [run_child("inline") for _ in range(RUNS)]
    # Main builds the net layer once; each child only applies it.
    t0 = time.perf_counter()
    shared = c.create_ruleset(fs=0, net=c.NET_BIND_TCP | c.NET_CONNECT_TCP)
    c.add_all_ports_but(shared, SERVERS)
    build_once_ms = (time.perf_counter() - t0) * 1e3
    os.set_inheritable(shared, True)
    inherited = [run_child("inherited", shared) for _ in range(RUNS)]
    check = run_child("check-inherited", shared)
    baseline = run_child("check-none")

    def med(rows: list[dict[str, object]], key: str) -> float:
        return round(statistics.median(float(str(r[key])) for r in rows), 2)

    report = {
        "abi": c.abi_version(),
        "rules": 65536 - len(SERVERS),
        "inline_build_ms_median": med(inline, "build_ms"),
        "inline_restrict_ms_median": med(inline, "restrict_ms"),
        "inline_total_ms_median": med(inline, "total_ms"),
        "inline_total_ms_max": round(max(float(str(r["total_ms"])) for r in inline), 2),
        "main_build_once_ms": round(build_once_ms, 2),
        "inherited_apply_ms_median": med(inherited, "total_ms"),
        "check_with_net_layer": check,
        "check_without_any_layer_connect_refused_us_per_1000": baseline["connect_refused_us"],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 1:
        main()
    elif sys.argv[1] == "inline":
        print(json.dumps(child_inline()))
    elif sys.argv[1] == "inherited":
        print(json.dumps(child_inherited(int(sys.argv[2]))))
    elif sys.argv[1] == "check-inherited":
        fd = int(sys.argv[2])
        c.no_new_privs()
        c.restrict_self(fd)
        os.close(fd)
        print(json.dumps(child_check()))
    elif sys.argv[1] == "check-none":
        c.no_new_privs()
        print(json.dumps({"connect_refused_us": child_check()["connect_refused_us"]}))
