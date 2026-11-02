import queue
from pathlib import Path

from allin.main.launcher import Launcher, xterm_command

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


def test_the_windows_xterm_answers_no_questions_and_takes_no_colour_changes():
    command = xterm_command(["python", "-m", "allin.window"], "AllIn · abc123")
    resources = [command[i + 1] for i, word in enumerate(command) if word == "-xrm"]
    assert "XTerm*allowWindowOps: false" in resources
    assert "XTerm*allowColorOps: false" in resources
    assert not any(resource.endswith(": true") and "allow" in resource for resource in resources)
    assert "XTerm*modifyOtherKeys: 2" in resources, "Ctrl+Enter still arrives"
    assert "XTerm*omitTranslation: fullscreen" in resources, "Alt+Enter reaches the Window"
