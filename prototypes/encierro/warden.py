"""#99: the boot as a warden. It confines itself, forks the worker that runs the function, and
on a cancel from Main (or the worker's end) kills every process of its domain with kill(-1),
the escaped grandchildren included. Measures the extra fork and the cancel.
Usage: python -I prototypes/encierro/warden.py
"""

import os
import selectors
import signal
import socket
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import confine as c  # noqa: E402


def warden(channel: socket.socket) -> None:
    c.no_new_privs()
    rs = c.create_ruleset(scoped=c.SCOPE_SIGNAL | c.SCOPE_ABSTRACT_UNIX_SOCKET)
    for p in ("/usr", "/etc", sys.prefix):
        c.add_path(rs, p, c.FS_READ)
    c.add_path(rs, "/dev/null", c.FS_READ | c.FS_WRITE)
    c.restrict_self(rs)
    os.close(rs)
    t0 = time.perf_counter()
    worker = os.fork()
    if worker == 0:
        channel.close()
        # A function that hangs and leaves an escaped grandchild.
        subprocess.Popen(["setsid", "sleep", "61"], start_new_session=True)
        while True:
            pass  # busy, holding the GIL: a signal handler in this process would never run
    fork_ms = (time.perf_counter() - t0) * 1e3
    channel.sendall(f"forked {fork_ms:.2f}\n".encode())
    sel = selectors.DefaultSelector()
    sel.register(channel, selectors.EVENT_READ)
    sel.select()  # Main says cancel
    t1 = time.perf_counter()
    os.kill(-1, signal.SIGKILL)
    os.waitpid(worker, 0)
    channel.sendall(f"cancelled {(time.perf_counter() - t1) * 1e3:.2f}\n".encode())
    os._exit(0)


def main() -> None:
    forks: list[float] = []
    cancels: list[float] = []
    for _ in range(10):
        parent, child = socket.socketpair()
        pid = os.fork()
        if pid == 0:
            parent.close()
            os.setsid()
            warden(child)
        child.close()
        reader = parent.makefile("r")
        forks.append(float(reader.readline().split()[1]))
        time.sleep(0.2)
        parent.sendall(b"cancel\n")
        cancels.append(float(reader.readline().split()[1]))
        os.waitpid(pid, 0)
    left = subprocess.run(["pgrep", "-f", "-x", "sleep 61"], capture_output=True, check=False)
    print(  # noqa: T201
        f"fork of the worker: median {statistics.median(forks):.2f} ms; "
        f"cancel (kill(-1) + reap): median {statistics.median(cancels):.2f} ms; "
        f"escaped grandchildren left: {len(left.stdout.split())}"
    )


if __name__ == "__main__":
    main()
