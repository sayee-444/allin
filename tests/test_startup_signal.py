"""A shutdown signal while Main starts, before its Dispatcher runs, shuts it down as ``/exit``
does (#19): ``main_stopped`` in its Log, no traceback, the terminal out of curses."""

import fcntl
import json
import os
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Main in a child process whose startup has a step that waits: it tells the test it is there,
# then waits until the test has sent its signal.
_CHILD = """
import sys, time
from pathlib import Path
import allin.main.app as app

here = Path(sys.argv[1])
real = app.remove_stale_fifos

def waiting_step(folder):
    (here / "starting").touch()
    deadline = time.monotonic() + 10
    while not (here / "go").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    real(folder)

app.remove_stale_fifos = waiting_step
app.run(here / "data", Path(sys.argv[2]), None)
"""


def _wait_for(path: Path, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} never appeared"
        time.sleep(0.01)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_signal_while_main_starts_shuts_it_down_as_exit_does(
    tmp_path: Path, signum: signal.Signals
):
    master, child_tty = os.openpty()
    fcntl.ioctl(child_tty, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    env = {**os.environ, "TERM": "xterm", "PYTHONPATH": str(ROOT / "src")}
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(tmp_path), str(ROOT / "sysprompts")],
        stdin=child_tty,
        stdout=child_tty,
        stderr=child_tty,
        env=env,
        start_new_session=True,  # the terminal's own signals never reach the test
    )
    os.close(child_tty)
    shown = bytearray()

    def read_terminal() -> None:  # so the child never blocks writing to it
        while True:
            try:
                data = os.read(master, 4096)
            except OSError:
                return
            if not data:
                return
            shown.extend(data)

    reader = threading.Thread(target=read_terminal, daemon=True)
    reader.start()
    try:
        _wait_for(tmp_path / "starting", 10)
        child.send_signal(signum)
        time.sleep(0.2)  # the signal lands while the step still waits
        (tmp_path / "go").touch()
        code = child.wait(timeout=15)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        reader.join(timeout=2)
        os.close(master)
    terminal = bytes(shown)
    assert code == 0, terminal.decode(errors="replace")[-2000:]
    assert b"Traceback" not in terminal
    # curses.wrapper's endwin leaves the alternate screen it entered.
    assert terminal.rfind(b"\x1b[?1049l") > terminal.rfind(b"\x1b[?1049h") >= 0
    log = tmp_path / "data" / "logs" / "main" / "log.jsonl"
    events = [json.loads(line)["event"] for line in log.read_text().splitlines()]
    assert events[0] == "main_started"
    assert events[-1] == "main_stopped"
