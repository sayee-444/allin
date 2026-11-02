"""Which Kit each piece of a Profile came from (``kits.json``), as Kits write it and Quarantine
reads it to tell who wrote a piece's current version (ADR-0014, ADR-0016)."""

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

KITS_FILE = "kits.json"


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
