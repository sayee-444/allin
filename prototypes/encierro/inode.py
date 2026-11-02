"""#99, question 6: Landlock rules are bound to the inode found when the rule was added.

A long-lived confined process (as a Proceso adherido would be) gets its rules; then someone
outside changes the files the way editors and tools do, and the process tries again.
Usage: python -I prototypes/encierro/inode.py <scratch dir>
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import confine as c  # noqa: E402


def attempt(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f"read {f.read()!r}"
    except OSError as e:
        return e.strerror or str(e)


def scenario(name: str, rule_on: str, change: "callable[[], None]", read: list[str]) -> None:  # type: ignore[valid-type]
    ready_r, ready_w = os.pipe()
    go_r, go_w = os.pipe()
    out_r, out_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        c.no_new_privs()
        rs = c.create_ruleset()
        for p in ("/usr", "/etc", sys.prefix):
            c.add_path(rs, p, c.FS_READ)
        c.add_path(rs, rule_on, c.FS_READ)
        c.restrict_self(rs)
        before = attempt(read[0])
        os.write(ready_w, b"1")
        os.read(go_r, 1)
        after = "; ".join(f"{os.path.basename(p)}: {attempt(p)}" for p in read)
        os.write(out_w, f"before: {before} | after: {after}".encode())
        os._exit(0)
    os.read(ready_r, 1)
    change()
    os.write(go_w, b"1")
    print(f"{name}\n    {os.read(out_r, 2000).decode()}")  # noqa: T201
    os.waitpid(pid, 0)


def write(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def atomic_replace(path: str, data: bytes) -> None:
    write(path + ".tmp", data)
    os.replace(path + ".tmp", path)


if __name__ == "__main__":
    root = os.path.join(os.path.abspath(sys.argv[1]), "inode")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    f = os.path.join(root, "shared.txt")
    d = os.path.join(root, "dir")

    write(f, b"v1")
    scenario("1. rule on a file; the file is rewritten in place",
             f, lambda: write(f, b"v2"), [f])

    write(f, b"v1")
    scenario("2. rule on a file; the file is replaced atomically (tmp + rename), as editors save",
             f, lambda: atomic_replace(f, b"v2"), [f])

    os.makedirs(d)
    g = os.path.join(d, "inner.txt")
    write(g, b"v1")
    scenario("3. rule on a folder; a file inside it is replaced atomically",
             d, lambda: atomic_replace(g, b"v2"), [g])

    old = d + ".old"

    def swap_dir() -> None:
        os.rename(d, old)
        os.makedirs(d)
        write(g, b"new")

    scenario("4. rule on a folder; the folder is moved away and a new one made in its place",
             d, swap_dir, [g, os.path.join(old, "inner.txt")])
