"""Quarantine and Approval (ADR-0013, ADR-0016): every new or changed piece waits until a human
approves it, and only what is approved is offered to the LLM or used.

A piece is a function's package (``functions/<package>/``, each top-level module that does not
start with ``_`` one function or fproc) or a Sysprompt (``sysprompts/<file>``). Each Profile
keeps the hash each piece was approved with (``approvals.json``): a piece whose hash differs,
whoever changed it, is in quarantine. A package with compiled code or binaries is rejected and
never approved. What a human adjusts when approving (a smaller Alcance, tools that ask a
Confirmation) is kept with the Approval, never in the code, so the hash does not change.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from allin.core.errors import (
    AllInError,
    ApprovalWidens,
    PieceInQuarantine,
    PieceNotDescribed,
    PieceNotFound,
    PieceNotShown,
    PieceRejected,
)
from allin.core.files import write_atomically
from allin.core.hashing import content_hash, folder_hash
from allin.core.sources import binary_files, compiled_files
from allin.decorators import Reach
from allin.main.descriptions import Description, Descriptions
from allin.main.provenance import kit_origins

type PieceKind = Literal["function", "sysprompt"]
type PieceState = Literal["approved", "quarantined", "rejected"]
type Author = Literal["user", "kit", "session"]

_APPROVALS = "approvals.json"
_FOLDERS: dict[PieceKind, str] = {"function": "functions", "sysprompt": "sysprompts"}


@dataclass(frozen=True)
class PieceRef:
    """A piece by kind and name, as commands write it: ``function:<package>``,
    ``sysprompt:<file>``."""

    kind: PieceKind
    name: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.name}"


def parse_piece_ref(text: str) -> PieceRef | None:
    kind, _, name = text.partition(":")
    if not name or "/" in name or name.startswith("."):
        return None
    match kind:
        case "function" | "sysprompt":
            return PieceRef(kind, name)
        case _:
            return None


class Provenance(BaseModel, frozen=True):
    """Who wrote a piece's current version: the user, a Kit (``who``, its name) or a Session
    (``who``, its id). An Approval never changes it (ADR-0016)."""

    by: Author
    who: str | None = None

    @property
    def writer(self) -> str:
        """As screens name it: ``you``, ``kit tutor``, ``session abcd``."""
        return "you" if self.by == "user" else f"{self.by} {self.who or ''}".rstrip()


class Approval(BaseModel, frozen=True):
    """A human's Approval of one version of a piece, with what they adjusted: a smaller
    Alcance per module and the tools that ask a Confirmation (ADR-0013)."""

    hash: str
    reach: dict[str, Reach] = Field(default_factory=dict[str, Reach])
    confirm: tuple[str, ...] = ()


class _Approvals(BaseModel, frozen=True):
    pieces: dict[str, Approval] = Field(default_factory=dict[str, Approval])


@dataclass(frozen=True)
class Piece:
    """A piece as it is on disk now, and where that leaves it."""

    ref: PieceRef
    path: Path
    hash: str
    state: PieceState
    provenance: Provenance
    rejected: tuple[str, ...] = ()  # what keeps it from being approved

    @property
    def modules(self) -> tuple[str, ...]:
        """A package's functions, by module; none for a Sysprompt."""
        if self.ref.kind != "function":
            return ()
        return tuple(sorted(p.stem for p in self.path.glob("*.py") if not p.name.startswith("_")))


@dataclass(frozen=True)
class Adjustments:
    """What a human changes when approving, without touching the code."""

    reach: Mapping[str, Reach] = field(default_factory=dict[str, Reach])  # module -> Alcance
    confirm: tuple[str, ...] = ()  # tools that ask a Confirmation, as with ``@confirm``


@dataclass(frozen=True)
class OfferedFunction:
    """An approved function, as Main offers it: its description with the approved Alcance."""

    package: Path
    description: Description
    confirm: frozenset[str]  # its tools that ask a Confirmation, added at approval


@dataclass(frozen=True)
class BulkApproval:
    """What approving the user's own pieces at once did: the ones approved, and why each other
    was left out."""

    approved: list[str] = field(default_factory=list[str])
    left_out: dict[str, str] = field(default_factory=dict[str, str])


type Described = dict[str, Description | AllInError]  # module -> its description or why not


class Quarantine:
    """The pieces of one Profile and their Approvals."""

    def __init__(self, profile_folder: Path, descriptions: Descriptions) -> None:
        self._folder = profile_folder
        self._descriptions = descriptions

    def pieces(self) -> list[Piece]:
        """Every piece of the Profile, functions first, by name."""
        refs = [
            PieceRef(kind, entry.name)
            for kind, folder in _FOLDERS.items()
            if (self._folder / folder).is_dir()
            for entry in sorted((self._folder / folder).iterdir())
            if not entry.name.startswith((".", "_")) and _is_piece(kind, entry)
        ]
        approvals = self._approvals()
        return [self._piece(ref, approvals) for ref in refs]

    def piece(self, ref: PieceRef) -> Piece:
        path = self._path(ref)
        if not _is_piece(ref.kind, path):
            raise PieceNotFound(piece=ref.id)
        return self._piece(ref, self._approvals())

    def describe(self, piece: Piece) -> Described:
        """What the encierro de descripción makes of each function of a package; on a kernel
        that cannot confine in full, a piece the user did not write is not described."""
        described: Described = {}
        for module in piece.modules:
            try:
                described[module] = self._descriptions.describe(
                    piece.path, module, written_by_user=piece.provenance.by == "user"
                )
            except AllInError as error:
                described[module] = error
        return described

    def approve(
        self, ref: PieceRef, shown: str, adjustments: Adjustments | None = None
    ) -> Approval:
        """Approve ``ref`` as the approval screen showed it, whose hash was ``shown``. Raises
        ``PieceRejected``, ``PieceNotShown`` if it changed since, ``PieceNotDescribed`` for a
        function without its description and ``ApprovalWidens`` for an adjustment that asks
        for more than the piece declares."""
        adjustments = adjustments or Adjustments()
        piece = self.piece(ref)
        if piece.state == "rejected":
            raise PieceRejected(piece=ref.id, files=list(piece.rejected))
        if piece.hash != shown:
            raise PieceNotShown(piece=ref.id)
        approval = Approval(
            hash=piece.hash,
            reach=dict(adjustments.reach),
            confirm=tuple(sorted(set(adjustments.confirm))),
        )
        if ref.kind == "function":
            _check_adjustments(ref, _described(ref, self.describe(piece)), adjustments)
        self._save({**self._approvals().pieces, ref.id: approval})
        return approval

    def approve_own(self) -> BulkApproval:
        """Approve at once every piece in quarantine the user wrote, but those that act as the
        user (``display``, or writing where they run things) or declare Otorgar: each of those
        is approved alone, after its screen (ADR-0016, ADR-0025)."""
        done = BulkApproval()
        approved: dict[str, Approval] = {}
        for piece in self.pieces():
            if piece.state != "quarantined":
                continue
            if (why := self._kept_from_bulk(piece)) is not None:
                done.left_out[piece.ref.id] = why
                continue
            approved[piece.ref.id] = Approval(hash=piece.hash)
            done.approved.append(piece.ref.id)
        if approved:
            self._save({**self._approvals().pieces, **approved})
        return done

    def offered(self) -> list[OfferedFunction]:
        """The approved functions, each with the Alcance and Confirmations of its Approval:
        what Main offers the LLM. One that cannot be described now is left out."""
        approvals = self._approvals().pieces
        offered: list[OfferedFunction] = []
        for piece in self.pieces():
            if piece.ref.kind != "function" or piece.state != "approved":
                continue
            approval = approvals[piece.ref.id]
            for module, description in self.describe(piece).items():
                if isinstance(description, AllInError):
                    continue
                reach = approval.reach.get(module, description.reach)
                tools = {tool.name for tool in description.tools}
                offered.append(
                    OfferedFunction(
                        package=piece.path,
                        description=description.model_copy(update={"reach": reach}),
                        confirm=frozenset(approval.confirm) & tools,
                    )
                )
        return offered

    def approval(self, ref: PieceRef) -> Approval | None:
        return self._approvals().pieces.get(ref.id)

    def _kept_from_bulk(self, piece: Piece) -> str | None:
        if piece.provenance.by != "user":
            return f"written by {piece.provenance.writer}"
        if piece.ref.kind != "function":
            return None
        if not piece.modules:  # as approving it alone refuses it
            return "no functions"
        for module, description in self.describe(piece).items():
            if isinstance(description, AllInError):
                return f"{module} could not be described: {description.code}"
            if description.reach.acts_as_user:
                return f"{module} acts as you"
            if description.reach.grants:
                return f"{module} declares Otorgar"
        return None

    def _piece(self, ref: PieceRef, approvals: _Approvals) -> Piece:
        path = self._path(ref)
        if ref.kind == "function":
            digest: str = folder_hash(path)
            rejected = tuple(sorted({*compiled_files(path), *binary_files(path)}))
        else:
            digest = content_hash(path.read_bytes())
            rejected = ()
        approval = approvals.pieces.get(ref.id)
        state: PieceState = (
            "rejected"
            if rejected
            else "approved"
            if approval is not None and approval.hash == digest
            else "quarantined"
        )
        return Piece(ref, path, digest, state, self._provenance(ref, digest), rejected)

    def _provenance(self, ref: PieceRef, digest: str) -> Provenance:
        """The Kit's while the piece is as the Kit wrote it; once it changed on disk, outside
        AllIn, the user's (ADR-0016). Pieces a Session creates arrive with Crear (#112)."""
        origin = kit_origins(self._folder).get(ref.id)
        # Without its hash (recorded before Kits kept it) there is no telling it changed: the
        # Kit's, which never goes in bulk.
        if origin is not None and origin.hash in (digest, None):
            return Provenance(by="kit", who=origin.kit)
        return Provenance(by="user")

    def _path(self, ref: PieceRef) -> Path:
        return self._folder / _FOLDERS[ref.kind] / ref.name

    def _approvals(self) -> _Approvals:
        return _load_approvals(self._folder)

    def _save(self, pieces: dict[str, Approval]) -> None:
        _save_approvals(self._folder, pieces)


def approve_installed(profile_folder: Path, installed: Path) -> None:
    """Approve the Profile's Sysprompts that are, byte for byte, the ones AllIn installs (the
    seed a Profile is born with): they are AllIn's own, as its code is. Main calls it whenever
    it opens a Profile, so the ones a Profile had before Approvals existed keep working."""
    folder = profile_folder / _FOLDERS["sysprompt"]
    approvals = _load_approvals(profile_folder).pieces
    approved: dict[str, Approval] = {}
    for seed in sorted(installed.glob("*")) if installed.is_dir() else ():
        ref = PieceRef("sysprompt", seed.name)
        mine = folder / seed.name
        if not (seed.is_file() and mine.is_file()):
            continue
        digest = content_hash(mine.read_bytes())
        kept = approvals.get(ref.id)
        if digest == content_hash(seed.read_bytes()) and (kept is None or kept.hash != digest):
            approved[ref.id] = Approval(hash=digest)
    if approved:
        _save_approvals(profile_folder, {**approvals, **approved})


def check_sysprompt(sysprompts: Path, name: str, data: bytes) -> None:
    """Raise ``PieceInQuarantine`` unless the Sysprompt ``name`` of the Profile whose folder
    holds ``sysprompts`` is approved as ``data`` (ADR-0013)."""
    ref = PieceRef("sysprompt", name)
    approval = _load_approvals(sysprompts.parent).pieces.get(ref.id)
    if approval is None or approval.hash != content_hash(data):
        raise PieceInQuarantine(piece=ref.id)


def _save_approvals(profile_folder: Path, pieces: dict[str, Approval]) -> None:
    approvals = _Approvals(pieces=pieces).model_dump_json(indent=2)
    write_atomically(profile_folder / _APPROVALS, approvals)


def _load_approvals(profile_folder: Path) -> _Approvals:
    try:
        return _Approvals.model_validate_json((profile_folder / _APPROVALS).read_bytes())
    except (OSError, ValidationError):
        return _Approvals()  # none yet, or unreadable: nothing is approved


def _is_piece(kind: PieceKind, path: Path) -> bool:
    return path.is_dir() if kind == "function" else path.is_file()


def _described(ref: PieceRef, described: Described) -> dict[str, Description]:
    """Every function of the package with its description; a package that has none, or one
    that could not be described, cannot be approved."""
    if not described:
        raise PieceNotDescribed(piece=ref.id, module=None, error="no functions")
    known: dict[str, Description] = {}
    for module, description in described.items():
        if isinstance(description, AllInError):
            raise PieceNotDescribed(piece=ref.id, module=module, error=description.code)
        known[module] = description
    return known


def _check_adjustments(
    ref: PieceRef, described: dict[str, Description], adjustments: Adjustments
) -> None:
    for module, reach in adjustments.reach.items():
        declared = described.get(module)
        if declared is None or not reach.within(declared.reach):
            raise ApprovalWidens(piece=ref.id, module=module, detail="reach")
    tools = {tool.name for description in described.values() for tool in description.tools}
    for tool in adjustments.confirm:
        if tool not in tools:
            raise ApprovalWidens(piece=ref.id, module=None, detail=f"no tool {tool}")
