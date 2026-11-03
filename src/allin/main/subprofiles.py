"""Subperfiles (#123, ADR-0026): the space of each instance of an fproc that declares
``@subprofile``, within the Profile it was attached in.

Each one keeps, in ``<profile>/subprofiles/<name>.json``, the Sessions its fproc created, which
are not in the Profile's list, whether a human gave it the Exención, and how many of its
Sessions may have a Turn running or queued at once. It is a boundary like the Profile's: what a
Session or a Proceso adherido reaches is its own space alone, and only a human crosses. It
outlives its process, frozen like the rest of its Profile: attached again with the same name, the
instance finds it as it was.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from allin.core.errors import (
    OutsideProfile,
    OutsideSubprofile,
    ProfileStorageFailed,
    SubprofileOfAnother,
)
from allin.core.files import write_atomically
from allin.core.ids import AttachedName, FprocName, SessionId, parse_attached_name
from allin.core.origin import Origin, actors

FOLDER = "subprofiles"
DEFAULT_TURNS = 1  # its Sessions with a Turn running or queued at once: one of the two slots


class SubprofileRecord(BaseModel, frozen=True):
    fproc: FprocName | None = None  # whose it is: another fproc with its name never gets it
    sessions: list[SessionId] = Field(default_factory=list[SessionId])
    exempt: bool = False  # a human's: --devastation answers what stays inside it
    turns: int = Field(default=DEFAULT_TURNS, ge=1)


@dataclass(frozen=True)
class Space:
    """Where a Session is: the Profile itself, or the Subperfil of one instance."""

    subprofile: AttachedName | None = None  # ``None``: the Profile


PROFILE = Space()


class Subprofiles:
    """The Subperfiles of the active Profile; only Main's dispatcher changes them."""

    def __init__(
        self,
        profile_folder: Callable[[], Path],
        in_profile: Callable[[SessionId], bool],
        declares: Callable[[AttachedName], bool],
    ) -> None:
        """``profile_folder``: the active Profile's folder; ``in_profile``: whether a Session
        is in its own list; ``declares``: whether the Proceso adherido of that name runs now
        and its fproc declares ``@subprofile``."""
        self._profile_folder = profile_folder
        self._in_profile = in_profile
        self._declares = declares

    def names(self) -> list[AttachedName]:
        """Every Subperfil of the active Profile, by its instance's name."""
        folder = self._folder()
        if not folder.is_dir():
            return []
        parsed = (parse_attached_name(path.stem) for path in folder.glob("*.json"))
        return sorted(name for name in parsed if name is not None)

    def record(self, name: AttachedName) -> SubprofileRecord:
        path = self._path(name)
        try:
            return SubprofileRecord.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            return SubprofileRecord()
        except (OSError, ValidationError) as error:
            raise ProfileStorageFailed(path=str(path), reason=str(error)) from error

    def ensure(self, name: AttachedName, fproc: FprocName) -> None:
        """The instance ``name`` of ``fproc`` has its Subperfil, empty if it had none yet.
        Raises ``SubprofileOfAnother`` if the one of that name is another fproc's."""
        if not self._path(name).exists():
            self._save(name, SubprofileRecord(fproc=fproc))
            return
        record = self.record(name)
        if record.fproc != fproc:
            raise SubprofileOfAnother(name=name, fproc=fproc, of=record.fproc)

    def add_session(self, name: AttachedName, session_id: SessionId) -> None:
        record = self.record(name)
        if session_id not in record.sessions:
            self._save(name, record.model_copy(update={"sessions": [*record.sessions, session_id]}))

    def set_exempt(self, name: AttachedName, exempt: bool) -> None:
        self._save(name, self.record(name).model_copy(update={"exempt": exempt}))

    def set_turns(self, name: AttachedName, turns: int) -> None:
        self._save(
            name,
            SubprofileRecord.model_validate({**self.record(name).model_dump(), "turns": turns}),
        )

    def renamed(self, old: SessionId, new: SessionId) -> None:
        """A Session took a new id (#59): its Subperfil lists it under it."""
        for name in self.names():
            record = self.record(name)
            if old in record.sessions:
                sessions = [new if sid == old else sid for sid in record.sessions]
                self._save(name, record.model_copy(update={"sessions": sessions}))

    def is_exempt(self, name: AttachedName) -> bool:
        """Whether the instance ``name`` has a Subperfil, with the Exención."""
        return name in self.names() and self.record(name).exempt

    def declares(self, name: AttachedName) -> bool:
        """Whether the Proceso adherido ``name`` runs now and its fproc declares its Subperfil:
        only then is what it does in it (ADR-0026)."""
        return self._declares(name)

    def held(self) -> dict[SessionId, AttachedName]:
        """Each Session of a Subperfil, and the instance whose it is."""
        return {sid: name for name in self.names() for sid in self.record(name).sessions}

    def holding(self, session_id: SessionId) -> AttachedName | None:
        """The Subperfil of the active Profile that has the Session, if any."""
        for name in self.names():
            if session_id in self.record(name).sessions:
                return name
        return None

    def space_of(self, session_id: SessionId) -> Space | None:
        """Where the Session is; ``None`` if neither in the active Profile nor in one of its
        Subperfiles."""
        if self._in_profile(session_id):
            return PROFILE
        name = self.holding(session_id)
        return None if name is None else Space(name)

    def acting_space(self, origin: Origin) -> Space | None:
        """The space ``origin``'s chain acts in; ``None`` for a human's or Main's own, which
        reach any. Each Session sets it; a Proceso adherido enters its Subperfil if its fproc
        declares one, and else stays where it was, the Profile if nothing came before it. Once
        in a Subperfil, nothing in the chain leaves it (ADR-0026). Raises ``OutsideProfile`` for
        a Session out of reach and ``OutsideSubprofile`` for a chain that crossed."""
        space: Space | None = None
        for link in actors(origin):
            if link.kind == "session":
                here = self.space_of(SessionId(link.id))
                if here is None:
                    raise OutsideProfile(session_id=link.id)
            else:
                name = AttachedName(link.id)
                here = Space(name) if self._declares(name) else (space or PROFILE)
            if space is not None and space.subprofile is not None and here != space:
                raise OutsideSubprofile(
                    link=f"{link.kind}:{link.id}",
                    subprofile=here.subprofile,
                    acts_in=space.subprofile,
                )
            space = here
        return space

    def check_acting(self, origin: Origin) -> None:
        """Raises ``OutsideProfile`` if whoever acts last in ``origin`` is a Session out of
        reach: neither in the active Profile nor in one of its Subperfiles."""
        self.acting_space(origin)

    def check_reach(self, origin: Origin, session_id: SessionId) -> Space:
        """The Session's space, if ``origin`` reaches it: a human any of the active Profile's,
        anyone else its own space alone (ADR-0010, ADR-0026). Raises ``OutsideProfile`` and
        ``OutsideSubprofile``."""
        target = self.space_of(session_id)
        if target is None:
            raise OutsideProfile(session_id=session_id)
        acting = self.acting_space(origin)
        if acting is not None and acting != target:
            raise OutsideSubprofile(
                session_id=session_id, subprofile=target.subprofile, acts_in=acting.subprofile
            )
        return target

    def _folder(self) -> Path:
        return self._profile_folder() / FOLDER

    def _path(self, name: AttachedName) -> Path:
        return self._folder() / f"{name}.json"

    def _save(self, name: AttachedName, record: SubprofileRecord) -> None:
        path = self._path(name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(path, record.model_dump_json(indent=2))
        except OSError as error:
            raise ProfileStorageFailed(path=str(path), reason=str(error)) from error
