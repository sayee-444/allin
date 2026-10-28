import fcntl
import os
import termios
from pathlib import Path

from utils import prints


def xterm_session(
    master: int,
    slave_ttyname: Path,
    session_root: Path,
):

    prints.fds("proc")
    os.close(master)
    os.setsid()
    _SLAVE = os.open(slave_ttyname, os.O_RDWR | os.O_NOCTTY)
    fcntl.ioctl(_SLAVE, termios.TIOCSCTTY, 0)  # sets the controlling terminal
    for fd in (0, 1, 2):
        os.dup2(_SLAVE, fd)
    os.close(_SLAVE)
    venv_python = session_root.parent / "venv" / "bin" / "python"
    script_path = session_root / "terminal" / "person" / "start.py"
    print(script_path)
    try:
        os.execvp(
            "xterm",
            [
                "xterm",
                "-hold",
                "-fa",
                "Monospace",
                "-fs",
                "10",
                "-geometry",
                "180x40",
                "-e",
                str(venv_python),
                str(script_path),
                "hola",
            ],
        )

    except OSError:
        os._exit(1)
