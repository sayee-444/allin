"""#99: kill(-1, SIGKILL) from inside a Landlock domain with SCOPE_SIGNAL reaches only the
processes in that domain: every descendant of the call, even those that left its session,
and not Main, not a sibling call.

Usage: python -I prototypes/encierro/reaper.py
"""

import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import confine as c  # noqa: E402


def confined_call(tag: str, hold: float) -> int:
    """Fork a 'tool' that confines itself, leaves an escaped grandchild, and returns pid."""
    pid = os.fork()
    if pid:
        return pid
    os.setsid()
    c.no_new_privs()
    rs = c.create_ruleset(scoped=c.SCOPE_SIGNAL | c.SCOPE_ABSTRACT_UNIX_SOCKET)
    for p in ("/usr", "/etc"):
        c.add_path(rs, p, c.FS_READ)
    c.add_path(rs, "/dev/null", c.FS_READ | c.FS_WRITE)
    c.restrict_self(rs)
    os.close(rs)
    subprocess.Popen(
        ["setsid", "sleep", tag], start_new_session=True, stdout=subprocess.DEVNULL
    )
    time.sleep(hold)
    if hold < 1:
        os.kill(-1, signal.SIGKILL)  # the end of the call: take every descendant down
    os._exit(0)


def alive(tag: str) -> bool:
    out = subprocess.run(["pgrep", "-f", "-x", f"sleep {tag}"], capture_output=True, check=False)
    return bool(out.stdout.strip())


if __name__ == "__main__":
    sibling = confined_call("51", 3.0)  # a sibling call that is still running
    time.sleep(0.3)
    call = confined_call("52", 0.3)
    os.waitpid(call, 0)
    time.sleep(0.3)
    print(f"Main alive: True (this line)")  # noqa: T201
    print(f"escaped grandchild of the ending call alive: {alive('52')}")  # noqa: T201
    print(f"sibling call alive: {os.waitpid(sibling, os.WNOHANG) == (0, 0)}")  # noqa: T201
    print(f"sibling's grandchild alive: {alive('51')}")  # noqa: T201
    os.waitpid(sibling, 0)
    subprocess.run(["pkill", "-f", "-x", "sleep 51"], check=False)
    subprocess.run(["pkill", "-f", "-x", "sleep 52"], check=False)
