import fcntl
import os
import termios
from pathlib import Path

from utils import prints


def translator(master: int, slave_ttyname: Path):

    prints.fds("proc")
    os.close(master)
    os.setsid()
    _SLAVE = os.open(slave_ttyname, os.O_RDWR | os.O_NOCTTY)
    fcntl.ioctl(_SLAVE, termios.TIOCSCTTY, 0)  # sets the controlling terminal
    for fd in (0, 1, 2):
        os.dup2(_SLAVE, fd)
    os.close(_SLAVE)
    try:
        os.execvp(
            "echo",
            [
                "echo",
                "hola desde translator",
            ],
        )

    except OSError:
        os._exit(1)
