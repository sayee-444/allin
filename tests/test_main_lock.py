import signal
import subprocess
import sys
from pathlib import Path

import pytest

from allin.core.errors import MainAlreadyRunning
from allin.main.lock import MainLock


def test_a_second_main_with_the_same_lock_cannot_start(tmp_path: Path):
    first = MainLock(tmp_path / "state" / "root.lock")
    first.acquire()
    with pytest.raises(MainAlreadyRunning):
        MainLock(tmp_path / "state" / "root.lock").acquire()


def test_mains_with_different_ids_run_side_by_side(tmp_path: Path):
    MainLock(tmp_path / "root.lock").acquire()
    MainLock(tmp_path / "other.lock").acquire()


_HOLD = """
import sys, time
from pathlib import Path
from allin.main.lock import MainLock
MainLock(Path(sys.argv[1])).acquire()
print(flush=True)
time.sleep(60)
"""


def test_a_main_killed_outright_leaves_its_lock_free(tmp_path: Path):
    path = tmp_path / "root.lock"
    holder = subprocess.Popen([sys.executable, "-c", _HOLD, str(path)], stdout=subprocess.PIPE)
    assert holder.stdout is not None
    holder.stdout.readline()  # it holds the lock now
    with pytest.raises(MainAlreadyRunning):
        MainLock(path).acquire()
    holder.send_signal(signal.SIGKILL)
    holder.wait()
    MainLock(path).acquire()
