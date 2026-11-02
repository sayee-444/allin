"""Kits, first part: a recipe exported from the active Profile and loaded by copy into another
(ADR-0014).

For now a Kit holds Sysprompts, part of Main's Settings and suggested Server Settings; it
never holds Sessions, Logs, Atajos or anything of a running Main. Loading one runs nothing,
checks it whole first and writes all of it or nothing; each piece remembers which Kit and
version it came from (``kits.json`` in the Profile's folder).
"""

import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError

from allin.core.errors import (
    KitPieceUnknown,
    KitReferenceMissing,
    KitRejected,
    ProfileStorageFailed,
)
from allin.core.settings import (
    MainSettings,
    SettingKey,
    SettingName,
    apply_settings,
    shown_settings,
)
from allin.main.portable import (
    Manifest,
    Piece,
    PieceFiles,
    Portable,
    piece_id,
    read_portable,
    write_portable,
)
from allin.main.profiles import Profile
from allin.supervisor.settings import (
    REACHING_KEYS,
    ServerSettings,
    load_server_settings,
    server_toml,
)

_SETTINGS_FILE = "settings.json"
# Settings no Kit brings: the Exemption is only a human's to give (ADR-0013), the Enlace is
# each Session's (ADR-0022) and the username is the person's own.
_LEFT_OUT = frozenset({"session.exempt", "session.server", "session.username"})
# The Settings that name a Sysprompt file, which an exported Kit brings along.
_SYSPROMPT_KEYS = ("session.sysprompt", "session.compact.sysprompt")
# The one the Kit or the Profile must have: without its own Compaction Sysprompt, Main uses
# the one in its code (ADR-0017).
_REQUIRED_SYSPROMPT = "session.sysprompt"
# Server Settings a Kit may suggest: none that names a file or opens the network, but the
# model, by its file's name (ADR-0008).
_SERVER_KEYS = (frozenset(ServerSettings.model_fields) - REACHING_KEYS.keys()) | {"model"}
_LOADABLE = frozenset({"settings", "sysprompt", "server"})
_SETTINGS_VALUES = TypeAdapter(dict[str, str])

# What to do with a piece of the Kit that differs from the Profile's own.
type Choice = Literal["keep", "take", "both"]


@dataclass(frozen=True)
class KitLoaded:
    """What loading a Kit did, for Main to tell."""

    kit: str
    version: str
    written: list[str] = field(default_factory=list[str])  # pieces now in the Profile
    kept: list[str] = field(default_factory=list[str])  # conflicts that kept the Profile's
    # Sysprompts kept twice: the Kit's name for each, and the one its copy got.
    renamed: dict[str, str] = field(default_factory=dict[str, str])
    settings: MainSettings | None = None  # Main's current Settings with the Kit's, if taken
    # Suggested Supervisors this machine has none of, by name: their Settings.
    servers: dict[str, dict[str, JsonValue]] = field(
        default_factory=dict[str, dict[str, JsonValue]]
    )


class _Origin(BaseModel, frozen=True):
    kit: str
    version: str


class _Provenance(BaseModel, frozen=True):
    pieces: dict[str, _Origin] = Field(default_factory=dict[str, _Origin])


class Kits:
    def __init__(self, servers: Path) -> None:
        """``servers``: the folder of every Supervisor's Settings."""
        self._servers = servers

    def export(
        self,
        profile: Profile,
        current: MainSettings,
        extra: Sequence[SettingKey],
        folder: Path,
        wanted: Sequence[str],
        version: str,
    ) -> Manifest:
        """Write a Kit of ``profile``'s pieces into ``folder``: those ``wanted`` (all its
        Sysprompts and its Settings if none) and what they name."""
        available = self._available(profile)
        chosen = list(wanted) or ["settings", *(p for p in available if p.startswith("sysprompt:"))]
        for piece in chosen:
            if piece not in available:
                raise KitPieceUnknown(piece=piece)
        values = _kit_settings(current, extra)
        if "settings" in chosen:  # its dependencies join it by themselves
            for key in _SYSPROMPT_KEYS:
                chosen.append(f"sysprompt:{values.get(key)}")
            chosen.append(f"server:{current.session.server}")
        pieces = [
            self._piece(profile, piece, values)
            for piece in dict.fromkeys(chosen)
            if piece in available
        ]
        return write_portable(folder, "kit", folder.name, version, pieces)

    def load(
        self,
        profile: Profile,
        current: MainSettings,
        extra: Sequence[SettingKey],
        folder: Path,
        choices: Mapping[str, Choice],
    ) -> KitLoaded:
        """Copy the Kit in ``folder`` into ``profile``'s folder. A piece that differs from the
        Profile's own keeps the Profile's unless ``choices`` says otherwise; nothing is written
        unless the whole Kit can be."""
        kit = read_portable(folder, "kit")
        manifest = kit.manifest
        _check_pieces(manifest, choices)
        settings = _check_settings(kit, current, extra)
        servers = _check_servers(kit)
        _check_references(kit, profile)
        loaded = KitLoaded(manifest.name, manifest.version)
        writes: dict[Path, bytes] = {}
        for piece in manifest.pieces:
            if piece.kind == "sysprompt":
                self._plan_sysprompt(profile, kit, piece, choices, writes, loaded)
        if settings is not None and _differs(kit, current, extra):
            if choices.get("settings") == "take":
                settings = _renamed_references(settings, extra, loaded.renamed)
                loaded = replace(loaded, settings=settings)
                loaded.written.append("settings")
            else:
                loaded.kept.append("settings")
        provenance = _provenance(profile)
        origin = _Origin(kit=manifest.name, version=manifest.version)
        pieces = {**provenance.pieces, **dict.fromkeys(loaded.written, origin)}
        writes[profile.folder / "kits.json"] = (
            _Provenance(pieces=pieces).model_dump_json(indent=2).encode()
        )
        _write_all(writes)
        suggested = {
            name: values
            for name, values in servers.items()
            if not (self._servers / f"{name}.toml").exists()
        }
        return replace(loaded, servers=suggested)

    def _available(self, profile: Profile) -> list[str]:
        sysprompts = sorted(p.name for p in profile.sysprompts.glob("*") if p.is_file())
        servers = sorted(p.stem for p in self._servers.glob("*.toml"))
        return [
            "settings",
            *(f"sysprompt:{name}" for name in sysprompts),
            *(f"server:{name}" for name in servers),
        ]

    def _piece(self, profile: Profile, piece: str, values: dict[str, str]) -> PieceFiles:
        kind, _, name = piece.partition(":")
        if kind == "settings":
            data = json.dumps(values, indent=2, ensure_ascii=False).encode()
            return PieceFiles("settings", "main", {_SETTINGS_FILE: data})
        if kind == "sysprompt":
            data = (profile.sysprompts / name).read_bytes()
            return PieceFiles("sysprompt", name, {f"sysprompts/{name}": data})
        settings = load_server_settings(self._servers / f"{name}.toml")
        suggested = {
            key: value
            for key, value in settings.model_dump(exclude_none=True).items()
            if key in _SERVER_KEYS
        }
        suggested["model"] = Path(settings.model).name
        return PieceFiles("server", name, {f"servers/{name}.toml": server_toml(suggested).encode()})

    def _plan_sysprompt(
        self,
        profile: Profile,
        kit: Portable,
        piece: Piece,
        choices: Mapping[str, Choice],
        writes: dict[Path, bytes],
        loaded: KitLoaded,
    ) -> None:
        [data] = kit.contents(piece).values()
        target = profile.sysprompts / piece.name
        own = target.read_bytes() if target.is_file() else None
        if own == data:
            return
        choice = choices.get(piece_id("sysprompt", piece.name), "keep")
        if own is not None and choice == "keep":
            loaded.kept.append(piece_id("sysprompt", piece.name))
            return
        if own is not None and choice == "both":
            target = _free_name(target, kit.manifest.name)
            loaded.renamed[piece.name] = target.name
        writes[target] = data
        loaded.written.append(piece_id("sysprompt", target.name))


def _kit_settings(current: MainSettings, extra: Sequence[SettingKey]) -> dict[str, str]:
    """The part of Main's Settings a Kit carries: each Session's, but those no Kit brings."""
    values: dict[str, str] = {}
    for line in shown_settings(current, extra):
        key, _, value = line.partition("=")
        if key.startswith("session.") and key not in _LEFT_OUT and value:
            values[key] = value
    return values


def _renamed_references(
    settings: MainSettings, extra: Sequence[SettingKey], renamed: Mapping[str, str]
) -> MainSettings:
    """The Settings taken from the Kit naming its own Sysprompts where both were kept and
    the Kit's got another name."""
    assignments = {
        SettingName(key): renamed[value]
        for key, value in _kit_settings(settings, extra).items()
        if key in _SYSPROMPT_KEYS and value in renamed
    }
    return apply_settings(settings, assignments, extra) if assignments else settings


def _differs(kit: Portable, current: MainSettings, extra: Sequence[SettingKey]) -> bool:
    """Whether a Setting of the Kit has another value than the Profile's, compared as ``/set``
    shows them: applying one sets a default it already had, which is no change."""
    piece = kit.manifest.piece("settings", "main")
    if piece is None:
        return False
    own = _kit_settings(current, extra)
    return any(own.get(key) != value for key, value in _settings_values(kit, piece).items())


def _check_pieces(manifest: Manifest, choices: Mapping[str, Choice]) -> None:
    for piece in manifest.pieces:
        if piece.kind == "shortcut":  # a key runs with a human's Origin (ADR-0013)
            raise KitRejected(reason="a Kit never brings Atajos", piece=piece.name)
        if piece.kind not in _LOADABLE:
            raise KitRejected(
                reason="this AllIn cannot load it yet", piece=piece_id(piece.kind, piece.name)
            )
    present = {piece_id(piece.kind, piece.name) for piece in manifest.pieces}
    for chosen, choice in choices.items():
        if chosen not in present:
            raise KitPieceUnknown(piece=chosen)
        if chosen == "settings" and choice == "both":
            raise KitPieceUnknown(piece=chosen, reason="Settings are kept or taken, never both")


def _check_settings(
    kit: Portable, current: MainSettings, extra: Sequence[SettingKey]
) -> MainSettings | None:
    """Main's current Settings with the Kit's applied; ``None`` if it brings none."""
    piece = kit.manifest.piece("settings", "main")
    if piece is None:
        return None
    values = _settings_values(kit, piece)
    assignments: dict[SettingName, str] = {}
    for key, value in values.items():
        name = SettingName(key)
        if not name.startswith("session.") or name in _LEFT_OUT:
            raise KitRejected(reason="a Setting no Kit brings", key=name)
        assignments[name] = value
    return apply_settings(current, assignments, extra)


def _settings_values(kit: Portable, piece: Piece) -> dict[str, str]:
    try:
        return _SETTINGS_VALUES.validate_json(kit.contents(piece)[_SETTINGS_FILE])
    except (KeyError, ValidationError) as error:
        raise KitRejected(reason="unreadable Settings") from error


def _check_servers(kit: Portable) -> dict[str, dict[str, JsonValue]]:
    """Each suggested Server's Settings, none naming a file or opening the network."""
    servers: dict[str, dict[str, JsonValue]] = {}
    for piece in kit.manifest.pieces:
        if piece.kind != "server":
            continue
        [data] = kit.contents(piece).values()
        try:
            values: dict[str, JsonValue] = tomllib.loads(data.decode())
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise KitRejected(reason="unreadable Server Settings", server=piece.name) from error
        for key in values:
            if key not in _SERVER_KEYS:
                raise KitRejected(
                    reason="a Server Setting that names a file or opens the network",
                    server=piece.name,
                    key=key,
                )
        model = values.get("model")
        if not isinstance(model, str) or Path(model).name != model:
            raise KitRejected(reason="the model goes by its file's name", server=piece.name)
        try:
            ServerSettings.model_validate({**values, "port": 1})
        except ValidationError as error:
            reason = error.errors()[0]["msg"]
            raise KitRejected(reason=reason, server=piece.name) from error
        servers[piece.name] = values
    return servers


def _check_references(kit: Portable, profile: Profile) -> None:
    """The Sysprompt the Kit's Settings name is in the Kit or in the Profile."""
    piece = kit.manifest.piece("settings", "main")
    if piece is None:
        return
    values = _settings_values(kit, piece)
    name = values.get(_REQUIRED_SYSPROMPT)
    if name is None or kit.manifest.piece("sysprompt", name) is not None:
        return
    if not (profile.sysprompts / name).is_file():
        raise KitReferenceMissing(missing=[f"sysprompt:{name}"])


def _provenance(profile: Profile) -> _Provenance:
    try:
        return _Provenance.model_validate_json((profile.folder / "kits.json").read_bytes())
    except (OSError, ValidationError):
        return _Provenance()


def _free_name(target: Path, kit: str) -> Path:
    """Where the Kit's copy of a piece goes when both are kept: its name with the Kit's."""
    candidate = target.with_name(f"{target.stem}-{kit}{target.suffix}")
    number = 2
    while candidate.exists():
        candidate = target.with_name(f"{target.stem}-{kit}-{number}{target.suffix}")
        number += 1
    return candidate


def _write_all(writes: Mapping[Path, bytes]) -> None:
    """Write every file or, if one fails, put back what was there before."""
    backups: dict[Path, bytes | None] = {}
    try:
        for path, data in writes.items():
            backups[path] = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    except OSError as error:
        for path, before in backups.items():
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
        raise ProfileStorageFailed(path=str(next(iter(writes))), reason=str(error)) from error
