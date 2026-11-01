"""Who holds a TCP port on this machine, read from ``/proc`` (Linux only, like AllIn)."""

import socket
from pathlib import Path

_LISTEN = "0A"  # TCP_LISTEN in /proc/net/tcp


def port_free(port: int) -> bool:
    """Whether a Server could listen on ``port`` on the loopback now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # As llama does: connections the last Server left in TIME_WAIT do not hold the port.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def port_holder(port: int, proc: Path = Path("/proc")) -> str:
    """Who listens on ``port``, as ``pid <n> (<name>)``; ``unknown`` when ``/proc`` hides it
    (another user's process)."""
    inodes = _listening_inodes(port, proc)
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            for fd in (pid_dir / "fd").iterdir():
                target = str(fd.readlink())
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    name = (pid_dir / "comm").read_text().strip()
                    return f"pid {pid_dir.name} ({name})"
        except OSError:  # gone, or not ours to read
            continue
    return "unknown"


def _listening_inodes(port: int, proc: Path) -> set[str]:
    inodes: set[str] = set()
    for table in ("tcp", "tcp6"):
        try:
            lines = (proc / "net" / table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            local, state, inode = fields[1], fields[3], fields[9]
            if state == _LISTEN and int(local.rsplit(":", 1)[1], 16) == port:
                inodes.add(inode)
    return inodes
