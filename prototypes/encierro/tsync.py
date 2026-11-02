"""#99, question 4: which landlock_restrict_self flags this kernel accepts (bit 3 is
LANDLOCK_RESTRICT_SELF_TSYNC in /usr/include/linux/landlock.h, ABI 8).
Usage: python -I prototypes/encierro/tsync.py
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import confine as c

for bit in range(5):
    pid = os.fork()
    if pid == 0:
        c.no_new_privs()
        rs = c.create_ruleset(fs=c.FS_READ_DIR)
        c.add_path(rs, "/", c.FS_READ_DIR)
        r = c._libc.syscall(c.SYS_LANDLOCK_RESTRICT_SELF, rs, 1 << bit)
        err = os.strerror(ctypes.get_errno())
        print(f"restrict_self flag 1<<{bit}: {'accepted' if r == 0 else err}", flush=True)
        os._exit(0)
    os.waitpid(pid, 0)
