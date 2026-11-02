"""Which Kit each piece of a Profile came from (``kits.json``), as Kits write it, and which
Session created one with Crear (``created.json``, #112): Quarantine reads both to tell who
wrote a piece's current version (ADR-0014, ADR-0016)."""

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from allin.core.files import write_atomically

KITS_FILE = "kits.json"
CREATED_FILE = "created.json"


class KitOrigin(BaseModel, frozen=True):
    """Which Kit and version a piece came from, and the hash it was written with: once the
    piece changes, the Kit no longer wrote its current version (ADR-0016)."""

    kit: str
    version: str
    hash: str | None = None  # None: written before Kits recorded it, or not a file piece


class _KitOrigins(BaseModel, frozen=True):
    pieces: dict[str, KitOrigin] = Field(default_factory=dict[str, KitOrigin])


def kit_origins(profile_folder: Path) -> dict[str, KitOrigin]:
    """The pieces of a Profile that came from a Kit, by piece id."""
    try:
        return dict(
            _KitOrigins.model_validate_json((profile_folder / KITS_FILE).read_bytes()).pieces
        )
    except (OSError, ValidationError):
        return {}


def kits_json(pieces: Mapping[str, KitOrigin]) -> bytes:
    return _KitOrigins(pieces=dict(pieces)).model_dump_json(indent=2).encode()


class SessionOrigin(BaseModel, frozen=True):
    """The Session that created a piece with Crear, and the hash it wrote."""

    session: str
    hash: str


class _SessionOrigins(BaseModel, frozen=True):
    pieces: dict[str, SessionOrigin] = Field(default_factory=dict[str, SessionOrigin])


def session_origins(profile_folder: Path) -> dict[str, SessionOrigin]:
    """The pieces of a Profile a Session created, by piece id."""
    try:
        data = (profile_folder / CREATED_FILE).read_bytes()
        return dict(_SessionOrigins.model_validate_json(data).pieces)
    except (OSError, ValidationError):
        return {}


def record_created(profile_folder: Path, piece: str, origin: SessionOrigin | None) -> None:
    """Record which Session created ``piece`` as it is now, or, with ``None``, that none did
    (a human wrote its current version)."""
    pieces = session_origins(profile_folder)
    if origin is None:
        if pieces.pop(piece, None) is None:
            return
    else:
        pieces[piece] = origin
    data = _SessionOrigins(pieces=pieces).model_dump_json(indent=2)
    write_atomically(profile_folder / CREATED_FILE, data)
