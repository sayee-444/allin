from pathlib import Path

import __main__


def src_dir() -> Path:
    """Where python is executed, it will give you an absolute path"""
    f = getattr(__main__, "__file__", None)
    if not f:
        raise RuntimeError("entrypoint sin __file__ (REPL, python -c, frozen...)")
    return Path(f).resolve().parent
