"""The hash that names a piece's contents (ADR-0013): its files, by relative path."""

import hashlib
import json
from pathlib import Path


def files_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(json.dumps(path).encode() + b"\0" + files[path] + b"\0")
    return digest.hexdigest()


def folder_hash(folder: Path) -> str:
    """The hash of every file under ``folder`` but Python's caches, which change on their own."""
    files = {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file() and "__pycache__" not in path.relative_to(folder).parts
    }
    return files_hash(files)
