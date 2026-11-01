"""``python -m allin.supervisor <name>``: one Supervisor, as its own process (ADR-0008)."""

import argparse
import contextlib
import os
import signal
import sys
from pathlib import Path
from types import FrameType

from allin.core.errors import SupervisorAlreadyRunning
from allin.core.ids import parse_supervisor_name
from allin.supervisor.process import Supervisor


def main() -> None:
    parser = argparse.ArgumentParser(prog="allin.supervisor")
    parser.add_argument("name", help="its Server Settings are servers/<name>.toml")
    parser.add_argument(
        "--data-dir", type=Path, default=Path.cwd() / "var", help="runtime data (default: ./var)"
    )
    args = parser.parse_args()
    name = parse_supervisor_name(args.name)
    if name is None:
        sys.exit(f"not a Supervisor name: {args.name!r}")
    # Its own session, like the Windows: it outlives whoever started it (unless it leads one).
    with contextlib.suppress(PermissionError):
        os.setsid()
    supervisor = Supervisor(name, args.data_dir.resolve())

    def stop(_signum: int, _frame: FrameType | None) -> None:
        supervisor.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, lambda _s, _f: supervisor.restart(now=False))
    # --now: Mains cut their Steps short and repeat them after the restart (ADR-0023).
    signal.signal(signal.SIGUSR1, lambda _s, _f: supervisor.restart(now=True))
    # /server sleep: drain, then sleep until a human wakes it (ADR-0022).
    signal.signal(signal.SIGUSR2, lambda _s, _f: supervisor.sleep_by_human())
    try:
        sys.exit(supervisor.run(os.getpid()))
    except SupervisorAlreadyRunning as error:
        sys.exit(f"{error}: Supervisor {name} already runs")


if __name__ == "__main__":
    main()
