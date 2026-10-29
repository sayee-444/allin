import queue
from pathlib import Path

from allin.main.launcher import Launcher

TIMEOUT = 5.0


def test_watcher_reports_exit_code_and_output_goes_to_log(tmp_path: Path):
    exits: queue.Queue[int] = queue.Queue()
    log = tmp_path / "logs" / "window.log"
    launcher = Launcher(["sh", "-c", "echo from-window; exit 3"], log, exits.put)
    launcher.start()
    assert exits.get(timeout=TIMEOUT) == 3
    assert "from-window" in log.read_text()


def test_terminate_stops_a_running_window(tmp_path: Path):
    exits: queue.Queue[int] = queue.Queue()
    launcher = Launcher(["sleep", "30"], tmp_path / "window.log", exits.put)
    launcher.start()
    launcher.terminate(timeout=TIMEOUT)
    assert exits.get(timeout=TIMEOUT) != 0
