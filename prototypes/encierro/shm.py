"""#99, question 2: which Landlock rights on /dev/shm multiprocessing's semaphores need, and
what else in /dev/shm those rights expose. Usage: python -I prototypes/encierro/shm.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import confine as c

CREATE_ONLY = c.FS_MAKE_REG | c.FS_READ_FILE | c.FS_WRITE_FILE | c.FS_REMOVE_FILE | c.FS_TRUNCATE


def run(label: str, shm_rights: int, planted: str) -> None:
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        c.no_new_privs()
        rs = c.create_ruleset()
        for p in ("/usr", "/etc", sys.prefix):
            c.add_path(rs, p, c.FS_READ)
        c.add_path(rs, "/dev/shm", shm_rights)
        c.restrict_self(rs)
        out = []
        try:
            import multiprocessing as mp

            mp.get_context("fork").Lock()
            out.append("Lock(): ok")
        except OSError as e:
            out.append(f"Lock(): {e}")
        try:
            os.listdir("/dev/shm")
            out.append("listdir: ok")
        except OSError as e:
            out.append(f"listdir: {e.strerror}")
        try:
            with open(planted, "rb") as f:
                out.append(f"read another program's segment: {f.read()!r}")
        except OSError as e:
            out.append(f"read another program's segment: {e.strerror}")
        os.write(w, "; ".join(out).encode())
        os._exit(0)
    os.close(w)
    print(f"{label}: {os.read(r, 1000).decode()}")
    os.waitpid(pid, 0)


if __name__ == "__main__":
    planted = f"/dev/shm/allin-probe-{os.getpid()}"
    with open(planted, "wb") as f:
        f.write(b"secret")
    try:
        run("rw /dev/shm", c.FS_READ | c.FS_WRITE, planted)
        run("create/remove only, no READ_DIR", CREATE_ONLY, planted)
    finally:
        os.unlink(planted)
