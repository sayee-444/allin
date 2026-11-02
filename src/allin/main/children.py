"""How Main starts the processes that run AllIn's own code: Windows, the Organizer and the
Supervisors."""

import sys


def python_module(module: str, *args: str) -> list[str]:
    """The command that runs ``module`` with Main's Python, never importing from the current
    folder (``-P``, ADR-0005)."""
    return [sys.executable, "-P", "-m", module, *args]
