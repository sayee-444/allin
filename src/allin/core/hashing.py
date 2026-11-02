"""The hash that names a piece's contents (ADR-0013): its files, by relative path."""

import hashlib
import json
from pathlib import Path
from typing import NewType

PackageHash = NewType("PackageHash", str)


def files_hash(files: dict[str, bytes]) -> str:
    """The sha256 of ``files``, each with its path, in path order."""
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(json.dumps(path).encode() + b"\0" + files[path] + b"\0")
    return digest.hexdigest()


def folder_hash(folder: Path) -> PackageHash:
    """The hash of everything under ``folder``, its ``__pycache__`` too: Python imports a
    ``.pyc`` there instead of its source. A link counts as where it points, not as the file
    there, which a tool could not read anyway."""
    files: dict[str, bytes] = {}
    for path in sorted(folder.rglob("*")):
        name = path.relative_to(folder).as_posix()
        if path.is_symlink():
            files[name] = b"link\0" + path.readlink().as_posix().encode()
        elif path.is_file():
            files[name] = path.read_bytes()
    return PackageHash(files_hash(files))
