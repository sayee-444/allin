"""#99, question 1: kernel memory held by each process confined with the 65 534-port layer.

Starts N children that apply Main's shared net layer and sleep, and reads the slab growth
from /proc/meminfo (SUnreclaim). Usage: python -I prototypes/encierro/ports_memory.py
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import confine as c

N = 40
CHILD = (
    "import os,sys,time;sys.path.insert(0,sys.argv[2]);import confine as c;"
    "c.no_new_privs();c.restrict_self(int(sys.argv[1]));os.close(int(sys.argv[1]));"
    "print('ok',flush=True);time.sleep(30)"
)


def sunreclaim_kb() -> int:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("SUnreclaim:"):
                return int(line.split()[1])
    raise RuntimeError("no SUnreclaim")


def measure(with_layer: bool) -> float:
    shared = c.create_ruleset(fs=0, net=c.NET_BIND_TCP | c.NET_CONNECT_TCP)
    if with_layer:
        c.add_all_ports_but(shared, frozenset({9931}))
    else:
        c.add_port(shared, 443)
    os.set_inheritable(shared, True)
    here = os.path.dirname(os.path.abspath(__file__))
    before = sunreclaim_kb()
    procs = [
        subprocess.Popen(
            [sys.executable, "-I", "-c", CHILD, str(shared), here],
            stdout=subprocess.PIPE,
            pass_fds=(shared,),
        )
        for _ in range(N)
    ]
    for p in procs:
        assert p.stdout is not None
        p.stdout.readline()
    time.sleep(1)
    after = sunreclaim_kb()
    for p in procs:
        p.kill()
        p.wait()
    os.close(shared)
    return (after - before) / N


if __name__ == "__main__":
    base = measure(False)
    full = measure(True)
    print(f"per process, 1 port rule: {base:.0f} KiB; 65 535 port rules: {full:.0f} KiB")
