import os
import threading
import uuid
from multiprocessing import Process
from pathlib import Path

from terminal.workers.osreadloop import read_loop
from terminal.workers.xterm_session import xterm_session as session
from utils import paths, prints


def naming_fifo(name: str) -> str:
    return f"fifo-{name}{uuid.uuid4().hex[:2]}"


if __name__ == "__main__":
    _SESSION_NAME: str = "eva"  # Write anythign what you want, it doesn't matter at all.

    _SESSION_ROOT = (
        paths.src_dir()  # Where python is executed, it will give you an absolute path to exactly the root how src/main.py
    )

    _MASTER = os.posix_openpt(
        os.O_RDWR  # calls /dev/pts/ptmx to ask a pair master and slave but it only creates _MASTER fd in src/main.py process
    )

    # permissions to _MASTER to the slave endpoint
    os.grantpt(_MASTER)
    os.unlockpt(_MASTER)

    _SLAVE_ttyname = os.ptsname(_MASTER)

    worker = Process(target=session, args=(_MASTER, _SLAVE_ttyname, _SESSION_ROOT))
    t_rloop = threading.Thread(target=read_loop, args=(_MASTER,))
    worker.start()
    t_rloop.start()

    prints.fds("src/main.py")

    NAME_FIFO_IN: str = naming_fifo(_SESSION_NAME)
    NAME_FIFO_OUT: str = naming_fifo(_SESSION_NAME)
    PATH_FIFO: Path = _SESSION_ROOT / "fifo"
    print(NAME_FIFO_IN)
    print(NAME_FIFO_OUT)
    print(PATH_FIFO)
