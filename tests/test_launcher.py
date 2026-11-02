import queue
from pathlib import Path

from allin.core.themes import parse_theme
from allin.main.launcher import Launcher, xterm_command
from allin.ui.look import xterm_options

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


def test_xterm_takes_only_the_orders_a_theme_uses_never_its_questions():
    """#47: the size in characters, the letter and the colours; nothing that answers."""
    command = xterm_command(["python", "-m", "allin.window"], "AllIn · abc123")
    resources = dict(
        command[i + 1].split(": ", 1) for i, word in enumerate(command) if word == "-xrm"
    )
    window_ops = resources["XTerm*disallowedWindowOps"].split(",")
    assert "SetWinSizeChars" not in window_ops
    assert {"GetWinTitle", "GetSelection", "SetSelection", "GetWinSizeChars"} <= set(window_ops)
    assert len(window_ops) == 26
    assert resources["XTerm*allowFontOps"] == "false"
    assert resources["XTerm*disallowedFontOps"] == "GetFont"
    assert resources["XTerm*disallowedColorOps"] == "GetColor,GetAnsiColor"
    assert command[command.index("-tn") + 1] == "xterm-256color"


def test_xterm_is_born_with_its_themes_look():
    theme = parse_theme(
        "oceano", '[font]\nfamily = "Fira Code"\nsize = 12\n[window]\nsize="100x30"'
    )
    command = xterm_command(["python"], "AllIn · abc123", xterm_options(theme))
    assert command[command.index("-fa") + 1] == "Fira Code,monospace"
    assert command[command.index("-fs") + 1] == "12"
    assert command[command.index("-geometry") + 1] == "100x30"
    assert "-bg" not in command  # the terminal's own
    assert command[-2:] == ["-e", "python"]
