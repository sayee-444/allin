"""``python -m allin``: starts Main.

``python -m`` puts the current folder first on the import path, so a ``json.py`` left there
would run as part of Main. Main never imports from it (ADR-0005): the entry is dropped before
anything else is imported (``os`` and ``sys`` are loaded by the interpreter before any path).
"""

import os
import sys

# os.getcwd, not Path.cwd: pathlib is not loaded yet, and importing it would search the folder.
sys.path[:] = [entry for entry in sys.path if entry not in ("", os.getcwd())]  # noqa: PTH109

from allin.cli import main

if __name__ == "__main__":
    main()
