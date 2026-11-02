"""Pieces hold only sources a person can read (ADR-0013): what in a folder is compiled code."""

import importlib.machinery
from pathlib import Path

# Bytecode and extension modules: Python would import either instead of a source.
_COMPILED_SUFFIXES = frozenset(
    (*importlib.machinery.BYTECODE_SUFFIXES, ".pyo", *importlib.machinery.EXTENSION_SUFFIXES)
)


def compiled_files(folder: Path) -> list[str]:
    """The ``__pycache__`` folders and compiled modules under ``folder``, by relative path."""
    return sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.name == "__pycache__" or any(path.name.endswith(s) for s in _COMPILED_SUFFIXES)
    )
