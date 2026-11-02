"""A Profile's copy: its whole workspace in a folder, to back it up or move it to another
machine, and a new Profile made from one (ADR-0010).

The copy holds the Profile's Main Settings, its pieces, the Settings of the Supervisors its
Enlaces name and its Sessions whole (Conversation, Settings and Log). It never holds MainState:
an imported Profile starts with no Windows, queue or Drafts.
"""

import json
import shutil
from pathlib import Path, PurePosixPath

from allin.core.errors import (
    AllInError,
    ProfileExists,
    ProfileNameInvalid,
    ProfileStorageFailed,
    ServerSettingsInvalid,
)
from allin.core.ids import SessionId, new_session_id, parse_profile_name, parse_session_id
from allin.main.portable import PieceFiles, Portable, read_portable, write_portable
from allin.main.profiles import Profile, ProfileStore
from allin.main.sessions import SessionStore
from allin.main.settings_files import MainSettingsFiles
from allin.supervisor.settings import ServerSettings, load_server_settings, server_toml

_VERSION = "1"


class ProfileCopies:
    def __init__(self, data_dir: Path, profiles: ProfileStore) -> None:
        self._profiles = profiles
        self._sessions = data_dir / "sessions"
        self._servers = data_dir / "servers"

    def export(self, profile: Profile, folder: Path) -> int:
        """Write ``profile``'s copy into ``folder``; how many Sessions it holds."""
        session_ids = self._profiles.sessions(profile)
        pieces = [
            *_settings_piece(profile.settings),
            *_sysprompt_pieces(profile.sysprompts),
            *_theme_pieces(profile.themes),
            *self._servers_of(profile, session_ids),
            *(self._session(session_id) for session_id in session_ids),
        ]
        write_portable(folder, "profile", profile.name, _VERSION, pieces)
        return len(session_ids)

    def import_(self, folder: Path, name: str) -> tuple[Profile, dict[SessionId, SessionId]]:
        """A new Profile from the copy in ``folder``, with the same rules as ``/profile new``;
        it never replaces one. Sessions whose id is taken get a new one: the renamed ones."""
        chosen = parse_profile_name(name)
        if chosen is None:
            raise ProfileNameInvalid(name=name)
        if chosen in self._profiles.names():
            raise ProfileExists(profile=chosen)
        copy = read_portable(folder, "profile")
        root = self._profiles.root
        staging = root / f".{chosen}.import"
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            renamed: dict[SessionId, SessionId] = {}
            listed: list[SessionId] = []
            for piece in copy.manifest.pieces:
                files = copy.contents(piece)
                match piece.kind:
                    case "settings" | "sysprompt" | "theme":
                        _write(staging, files)
                    case "server":
                        self._import_server(piece.name, files)
                    case "session":
                        session_id = self._import_session(piece.name, files, copy)
                        if session_id != piece.name:
                            renamed[SessionId(piece.name)] = session_id
                        listed.append(session_id)
                    case _:
                        pass  # a piece of a later AllIn: a copy of this one never has it
            (staging / "sessions.json").write_text(json.dumps({"sessions": listed}, indent=2))
            staging.rename(root / chosen)
        except OSError as error:
            shutil.rmtree(staging, ignore_errors=True)
            raise ProfileStorageFailed(path=str(root / chosen), reason=str(error)) from error
        return self._profiles.find(chosen), renamed

    def _servers_of(self, profile: Profile, session_ids: list[SessionId]) -> list[PieceFiles]:
        """The Settings of every Supervisor the Profile's Enlaces name, without any path of
        this machine."""
        names = {MainSettingsFiles(profile.settings).load().current.session.server}
        store = SessionStore(self._sessions)
        for session_id in session_ids:
            try:
                names.add(store.load(session_id).settings.server)
            except AllInError:  # an unreadable Session is copied as it is all the same
                continue
        pieces: list[PieceFiles] = []
        for name in sorted(n for n in names if n is not None):
            try:
                settings = load_server_settings(self._servers / f"{name}.toml")
            except ServerSettingsInvalid:
                continue  # Main's own base_url, or a Supervisor that no longer exists
            text = server_toml(_without_paths(settings).model_dump(exclude_none=True))
            pieces.append(PieceFiles("server", name, {f"servers/{name}.toml": text.encode()}))
        return pieces

    def _session(self, session_id: SessionId) -> PieceFiles:
        folder = self._sessions / session_id
        files = {
            f"sessions/{session_id}/{path.relative_to(folder).as_posix()}": path.read_bytes()
            for path in sorted(folder.rglob("*"))
            if path.is_file()
        }
        return PieceFiles("session", session_id, files)

    def _import_server(self, name: str, files: dict[str, bytes]) -> None:
        """A Supervisor the copy names is created only if there is none of that name here:
        an existing one is never changed."""
        target = self._servers / f"{name}.toml"
        if target.exists():
            return
        self._servers.mkdir(parents=True, exist_ok=True)
        [data] = files.values()
        target.write_bytes(data)

    def _import_session(self, name: str, files: dict[str, bytes], copy: Portable) -> SessionId:
        old = parse_session_id(name)
        if old is None:
            raise ProfileStorageFailed(path=str(copy.manifest.name), reason=f"bad session {name}")
        new = old
        while (self._sessions / new).exists():
            new = new_session_id()
        prefix = f"sessions/{old}/"
        target = self._sessions / new
        for path, data in files.items():
            relative = PurePosixPath(path[len(prefix) :])
            written = data
            if relative.as_posix() == "session.json" and new != old:
                written = _with_id(data, new)
            out = target / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(written)
        return new


def _files(folder: Path, prefix: str) -> dict[str, bytes]:
    if not folder.is_dir():
        return {}
    return {
        f"{prefix}/{path.name}": path.read_bytes()
        for path in sorted(folder.iterdir())
        if path.is_file()
    }


def _settings_piece(folder: Path) -> list[PieceFiles]:
    files = _files(folder, "settings")
    return [PieceFiles("settings", "main", files)] if files else []


def _sysprompt_pieces(folder: Path) -> list[PieceFiles]:
    return [
        PieceFiles("sysprompt", path.removeprefix("sysprompts/"), {path: data})
        for path, data in _files(folder, "sysprompts").items()
    ]


def _theme_pieces(folder: Path) -> list[PieceFiles]:
    return [
        PieceFiles("theme", PurePosixPath(path).stem, {path: data})
        for path, data in _files(folder, "themes").items()
    ]


def _write(root: Path, files: dict[str, bytes]) -> None:
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _with_id(data: bytes, session_id: SessionId) -> bytes:
    try:
        metadata = json.loads(data)
    except ValueError:
        return data
    metadata["id"] = session_id
    return json.dumps(metadata, indent=2).encode()


def _without_paths(settings: ServerSettings) -> ServerSettings:
    """The same Settings naming files by their name only: a path of this machine means
    nothing on another, and no copy carries one."""
    return settings.model_copy(
        update={
            "model": Path(settings.model).name,
            "mmproj": None if settings.mmproj is None else Path(settings.mmproj).name,
            "cwd": None,
            "command": [Path(settings.command[0]).name, *settings.command[1:]],
            "extra": [Path(word).name if word.startswith("/") else word for word in settings.extra],
        }
    )
