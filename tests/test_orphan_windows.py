import subprocess
import sys
import time
from pathlib import Path

import pytest

from allin.main.windows import close_orphan_window


def spawn(*args: str) -> subprocess.Popen[bytes]:
    """A process whose command line looks like ``args``; it just sleeps."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *args])
    cmdline = Path(f"/proc/{process.pid}/cmdline")
    while b"time.sleep" not in cmdline.read_bytes():  # still the forked test runner
        time.sleep(0.01)
    return process


def test_the_xterm_of_a_sessions_window_left_by_a_dead_main_is_closed():
    orphan = spawn("-m", "allin.window", "abc123")
    assert close_orphan_window("abc123", orphan.pid)
    assert orphan.wait(timeout=5) != 0


@pytest.mark.parametrize("args", [("-m", "allin.window", "zzz999"), ("unrelated",)])
def test_a_process_that_reused_the_pid_is_left_alone(args: tuple[str, ...]):
    other = spawn(*args)
    try:
        assert not close_orphan_window("abc123", other.pid)
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_a_window_that_is_already_gone_closes_nothing():
    gone = spawn("-m", "allin.window", "abc123")
    gone.kill()
    gone.wait()
    assert not close_orphan_window("abc123", gone.pid)
