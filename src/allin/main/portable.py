"""What a Profile's copy and a Kit share: a folder whose versioned manifest lists every piece
it holds, with its files and their hash (ADR-0010, ADR-0014).

What AllIn writes into one names files relative to the folder, never by an absolute path of
the machine, and holds no MainState, pids, FIFOs or locks.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from allin.core.errors import (
    ExportFolderNotEmpty,
    KitRejected,
    PortableFormatUnknown,
    PortableInvalid,
)
from allin.core.hashing import files_hash

FORMAT = 1
MANIFEST = "manifest.json"

type PortableType = Literal["profile", "kit"]
# What a piece is. The manifest can already declare the kinds that arrive with F2 and later,
# so a Kit made by a newer AllIn is told apart from a broken one.
type PieceKind = Literal[
    "settings",
    "sysprompt",
    "server",
    "session",
    "function",
    "mold",
    "plan",
    "theme",
    "history",
    "entry",
    "shortcut",
]


class Piece(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    kind: PieceKind
    name: str
    files: list[str]  # relative to the folder, ``/``-separated
    sha256: str  # of its files, paths included


class Manifest(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    type: PortableType
    format: int
    name: str
    version: str
    pieces: list[Piece]

    def piece(self, kind: PieceKind, name: str) -> Piece | None:
        return next((p for p in self.pieces if p.kind == kind and p.name == name), None)


class _Format(BaseModel, frozen=True):
    format: int


@dataclass(frozen=True)
class PieceFiles:
    """A piece as it is exported: its files' contents by their relative path."""

    kind: PieceKind
    name: str
    files: dict[str, bytes]


@dataclass(frozen=True)
class Portable:
    """A Profile's copy or a Kit as read: its manifest and its files' contents."""

    manifest: Manifest
    files: dict[str, bytes]

    def contents(self, piece: Piece) -> dict[str, bytes]:
        return {path: self.files[path] for path in piece.files}


def piece_id(kind: str, name: str) -> str:
    """How a piece is named in commands and in provenance: ``settings``, ``sysprompt:<file>``."""
    return kind if kind == "settings" else f"{kind}:{name}"


def write_portable(
    folder: Path, kind: PortableType, name: str, version: str, pieces: list[PieceFiles]
) -> Manifest:
    """Write a copy or a Kit into ``folder``, which must not exist yet or be empty; it is built
    aside and moved into place at once, so a failure leaves nothing half-written."""
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ExportFolderNotEmpty(path=str(folder))
    manifest = Manifest(
        type=kind,
        format=FORMAT,
        name=name,
        version=version,
        pieces=[
            Piece(kind=p.kind, name=p.name, files=sorted(p.files), sha256=files_hash(p.files))
            for p in pieces
        ],
    )
    staging = folder.with_name(f".{folder.name}.new")
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for piece in pieces:
            for path, data in piece.files.items():
                target = staging / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        (staging / MANIFEST).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        if folder.exists():
            folder.rmdir()
        staging.rename(folder)
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise PortableInvalid(path=str(folder), reason=str(error)) from error
    return manifest


def read_portable(folder: Path, kind: PortableType) -> Portable:
    """Read a copy or a Kit whole, checking its manifest and every file's hash; nothing that
    fails is half-used."""
    try:
        raw = (folder / MANIFEST).read_bytes()
    except OSError as error:
        raise PortableInvalid(path=str(folder), reason=f"no {MANIFEST}: {error}") from error
    try:
        version = _Format.model_validate_json(raw).format
    except ValidationError as error:
        raise PortableInvalid(path=str(folder), reason=f"broken {MANIFEST}") from error
    if version != FORMAT:
        raise PortableFormatUnknown(format=version, supported=FORMAT)
    try:
        manifest = Manifest.model_validate_json(raw)
    except ValidationError as error:
        reason = f"broken {MANIFEST}: {error.errors()[0]['msg']}"
        raise PortableInvalid(path=str(folder), reason=reason) from error
    if manifest.type != kind:
        raise PortableInvalid(path=str(folder), reason=f"a {manifest.type}, not a {kind}")
    if kind == "kit":
        _check_readable(folder)
    files = _read_files(folder, manifest)
    for piece in manifest.pieces:
        if files_hash({path: files[path] for path in piece.files}) != piece.sha256:
            raise PortableInvalid(
                path=str(folder), reason=f"{piece_id(piece.kind, piece.name)} changed"
            )
    return Portable(manifest, files)


def _read_files(folder: Path, manifest: Manifest) -> dict[str, bytes]:
    listed = [path for piece in manifest.pieces for path in piece.files]
    for path in listed:
        posix = PurePosixPath(path)
        if posix.is_absolute() or ".." in posix.parts or not posix.parts:
            raise PortableInvalid(path=str(folder), reason=f"not a relative path: {path}")
    present = {
        file.relative_to(folder).as_posix() for file in folder.rglob("*") if file.is_file()
    } - {MANIFEST}
    unlisted = sorted(present - set(listed))
    if unlisted:
        raise PortableInvalid(path=str(folder), reason=f"files not in {MANIFEST}: {unlisted}")
    try:
        return {path: (folder / path).read_bytes() for path in listed}
    except OSError as error:
        raise PortableInvalid(path=str(folder), reason=str(error)) from error


def _check_readable(folder: Path) -> None:
    """A Kit only holds sources a person can read: compiled code or a binary refuses it whole
    (ADR-0014)."""
    compiled = sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.name == "__pycache__" or path.suffix in (".pyc", ".pyo")
    )
    if compiled:
        raise KitRejected(reason="compiled code", files=list[JsonValue](compiled))
    binary = sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.is_file() and not _is_text(path.read_bytes())
    )
    if binary:
        raise KitRejected(reason="binary files", files=list[JsonValue](binary))


def _is_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True
