"""Neither Main nor what it starts imports from its current folder (ADR-0005, #100)."""

import subprocess
import sys
from pathlib import Path

from allin.main.children import python_module

PLANTED = "raise SystemExit('planted module imported')\n"


def dirty_folder(tmp_path: Path) -> Path:
    for name in ("json", "argparse", "curses", "pydantic"):
        (tmp_path / f"{name}.py").write_text(PLANTED)
    return tmp_path


def test_main_started_with_python_m_ignores_modules_left_in_its_current_folder(tmp_path: Path):
    started = subprocess.run(
        [sys.executable, "-m", "allin", "--help"],
        cwd=dirty_folder(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert "planted" not in started.stderr
    assert started.returncode == 0 and "usage: allin" in started.stdout


def test_the_processes_main_starts_ignore_its_current_folder(tmp_path: Path):
    # The Window, without its arguments, stops at its usage, after its imports.
    started = subprocess.run(
        python_module("allin.window"),
        cwd=dirty_folder(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert "planted" not in started.stderr
