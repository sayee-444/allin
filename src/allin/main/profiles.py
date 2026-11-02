"""Profiles on disk: one folder per Profile under ``<data-dir>/profiles/<name>/`` (ADR-0010).

Each Profile keeps its Main's Settings (``settings/``), the ids of its Sessions
(``sessions.json``), its MainState (``state.json``) and its pieces (``sysprompts/``). Sessions
stay in ``sessions/``, shared by every Profile that lists them; Notice modules stay in
``notices/``, because they are part of Main (ADR-0016). Which Profile Main opened last is kept
in ``state/last_profile.json``.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from allin.core.errors import (
    ProfileExists,
    ProfileNameInvalid,
    ProfileNotFound,
    ProfileStorageFailed,
)
from allin.core.files import write_atomically
from allin.core.ids import (
    DEFAULT_PROFILE,
    ProfileName,
    SessionId,
    new_profile_name,
    parse_profile_name,
)
from allin.main.sessions import SessionStore
from allin.main.state import MainStateStore

# The folders of a Profile's pieces, copied whole by ``/profile new``; functions, Molds, Plans
# and Themes will join them as they appear (ADR-0010).
_PIECES = ("sysprompts",)
_SETTINGS = "settings"
_SESSIONS = "sessions.json"
_STATE = "state.json"


@dataclass(frozen=True)
class Profile:
    name: ProfileName
    folder: Path

    @property
    def settings(self) -> Path:
        """The folder of its Main's Settings files."""
        return self.folder / _SETTINGS

    @property
    def sysprompts(self) -> Path:
        return self.folder / "sysprompts"

    @property
    def state(self) -> Path:
        """Its MainState's file."""
        return self.folder / _STATE


class _SessionList(BaseModel, frozen=True):
    sessions: list[SessionId]


class _LastProfile(BaseModel, frozen=True):
    name: ProfileName


class ProfileStore:
    def __init__(self, data_dir: Path, seed_sysprompts: Path) -> None:
        """``seed_sysprompts`` are the installation's Sysprompts, which ``default`` is born
        with."""
        self._data_dir = data_dir
        self._root = data_dir / "profiles"
        self._seed_sysprompts = seed_sysprompts
        self._last = data_dir / "state" / "last_profile.json"

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def root(self) -> Path:
        """The folder that holds every Profile's."""
        return self._root

    def check(self, name: str | None) -> ProfileName:
        """The Profile Main would open for ``name`` (the last one used if ``None``), touching
        nothing: one that does not exist, nor would after ``migrate``, is an error."""
        chosen = self._chosen(name)
        if not self._exists(chosen) and (self._root.exists() or chosen != DEFAULT_PROFILE):
            raise ProfileNotFound(profile=chosen)
        return chosen

    def migrate(self) -> None:
        """Turn what there was before Profiles into the Profile ``default``, the first time.

        It is built aside and moved into place in one rename, so a Main stopped halfway leaves
        the old layout as it was and the next start migrates again.
        """
        if self._root.exists():
            return
        old_state = self._data_dir / "state" / "root.json"
        old_settings = self._data_dir / _SETTINGS
        staging = self._data_dir / "profiles.new"
        try:
            shutil.rmtree(staging, ignore_errors=True)
            folder = staging / DEFAULT_PROFILE
            folder.mkdir(parents=True)
            if old_state.exists():
                shutil.copy2(old_state, folder / _STATE)
            if old_settings.is_dir():
                shutil.copytree(old_settings, folder / _SETTINGS)
            _copy_folder(self._seed_sysprompts, folder / "sysprompts")
            all_sessions = sorted(SessionStore(self._data_dir / "sessions").ids())
            _write_sessions(folder, all_sessions)
            staging.rename(self._root)
            old_state.unlink(missing_ok=True)
            shutil.rmtree(old_settings, ignore_errors=True)
        except OSError as error:
            raise ProfileStorageFailed(path=str(self._root), reason=str(error)) from error

    def open(self, name: str | None) -> Profile:
        """The Profile named ``name``, or the last one used; it becomes the last one used."""
        chosen = self._chosen(name)
        if not self._exists(chosen):
            raise ProfileNotFound(profile=chosen)
        try:
            self._last.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(self._last, _LastProfile(name=chosen).model_dump_json())
        except OSError as error:
            raise ProfileStorageFailed(path=str(self._last), reason=str(error)) from error
        return self._profile(chosen)

    def find(self, name: str) -> Profile:
        """The Profile named ``name``, without making it the last one used."""
        chosen = self._chosen(name)
        if not self._exists(chosen):
            raise ProfileNotFound(profile=chosen)
        return self._profile(chosen)

    def names(self) -> list[ProfileName]:
        """Every Profile's name, sorted."""
        if not self._root.is_dir():
            return []
        parsed = (parse_profile_name(path.name) for path in self._root.iterdir() if path.is_dir())
        return sorted(name for name in parsed if name is not None)

    def create(self, name: str | None, source: Profile) -> Profile:
        """A new Profile with a copy of ``source``'s Settings and pieces and no Sessions;
        without ``name`` it gets a generated one."""
        chosen = new_profile_name() if name is None else parse_profile_name(name)
        if chosen is None:
            raise ProfileNameInvalid(name=name)
        if self._exists(chosen):
            raise ProfileExists(profile=chosen)
        staging = self._root / f".{chosen}.new"  # never a Profile's name, so never listed
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            _copy_folder(source.settings, staging / _SETTINGS)
            for piece in _PIECES:
                _copy_folder(source.folder / piece, staging / piece)
            _write_sessions(staging, [])
            staging.rename(self._root / chosen)
        except OSError as error:
            raise ProfileStorageFailed(path=str(self._root / chosen), reason=str(error)) from error
        return self._profile(chosen)

    def sessions(self, profile: Profile) -> list[SessionId]:
        """The ids of the Sessions that belong to ``profile``, in the order they joined it."""
        path = profile.folder / _SESSIONS
        try:
            return _SessionList.model_validate_json(path.read_bytes()).sessions
        except FileNotFoundError:
            return []
        except (OSError, ValidationError) as error:
            raise ProfileStorageFailed(path=str(path), reason=str(error)) from error

    def add_session(self, profile: Profile, session_id: SessionId) -> None:
        """Put a Session in ``profile``'s list, if it is not there yet."""
        listed = self.sessions(profile)
        if session_id in listed:
            return
        try:
            _write_sessions(profile.folder, [*listed, session_id])
        except OSError as error:
            raise ProfileStorageFailed(path=str(profile.folder), reason=str(error)) from error

    def remove_session(self, profile: Profile, session_id: SessionId) -> None:
        """Take a Session out of ``profile``'s list; the Session itself stays in ``sessions/``."""
        listed = self.sessions(profile)
        if session_id not in listed:
            return
        try:
            _write_sessions(profile.folder, [sid for sid in listed if sid != session_id])
        except OSError as error:
            raise ProfileStorageFailed(path=str(profile.folder), reason=str(error)) from error

    def rename_session(self, old: SessionId, new: SessionId, active: ProfileName) -> None:
        """A Session renamed (#59): every Profile lists it, and keeps its MainState, under its
        new id; but ``active``'s MainState, which the running Main keeps in memory and saves
        itself."""
        for name in self.names():
            profile = self._profile(name)
            listed = self.sessions(profile)
            if old in listed:
                try:
                    _write_sessions(profile.folder, [new if s == old else s for s in listed])
                except OSError as error:
                    raise ProfileStorageFailed(
                        path=str(profile.folder), reason=str(error)
                    ) from error
            if name == active:
                continue
            store = MainStateStore(profile.state)
            state = store.load()
            renamed = state.renamed(old, new)
            if renamed != state:
                store.save(renamed)

    def profiles_of(self, session_id: SessionId) -> list[ProfileName]:
        """The Profiles whose list has the Session, sorted."""
        return [name for name in self.names() if session_id in self.sessions(self._profile(name))]

    def _chosen(self, name: str | None) -> ProfileName:
        """The Profile ``name`` names, the last one used if ``None``; a name that is not one
        is never turned into a path."""
        if name is None:
            return self._last_used()
        chosen = parse_profile_name(name)
        if chosen is None:
            raise ProfileNotFound(profile=name)
        return chosen

    def _last_used(self) -> ProfileName:
        """``default`` if no Profile was opened yet or the record of it is unreadable."""
        try:
            return _LastProfile.model_validate_json(self._last.read_bytes()).name
        except (OSError, ValidationError):
            return DEFAULT_PROFILE

    def _exists(self, name: ProfileName) -> bool:
        return (self._root / name).is_dir()

    def _profile(self, name: ProfileName) -> Profile:
        return Profile(name, self._root / name)


def _copy_folder(source: Path, destination: Path) -> None:
    """A copy of ``source``; an empty folder if there is none."""
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.mkdir(parents=True)


def _write_sessions(folder: Path, sessions: list[SessionId]) -> None:
    write_atomically(folder / _SESSIONS, _SessionList(sessions=sessions).model_dump_json(indent=2))
