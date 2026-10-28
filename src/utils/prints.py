import os


def fds(title: str) -> None:
    x = 10
    print("=" * x + title + x * "=")
    _PID = os.getpid()
    _PPID = os.getppid()
    _SID = os.getsid(_PID)
    print(f"PID: {_PID} | PPID: {_PPID} | SID: {_SID}")
    for fd in sorted(os.listdir("/proc/self/fd"), key=int):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
            print(f"fd {fd} -> {target}")
        except OSError:
            pass
