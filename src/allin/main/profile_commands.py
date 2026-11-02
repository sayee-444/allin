"""``/profile``: the Profiles there are, making a new one, switching to another and sharing
Sessions between them (ADR-0010)."""

from pathlib import Path

from pydantic import JsonValue

from allin.core.errors import InvalidArguments
from allin.core.ids import MainId, SessionId
from allin.main.boxes import MainBoxes
from allin.main.profile_copy import ProfileCopies
from allin.main.profile_switch import ProfileSwitch
from allin.main.profiles import Profile, ProfileStore
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore

USAGE = (
    "/profile [new [name] | switch <name> [--now] | share <sid> <profile>"
    " | unshare <sid> [profile] | export <name> <folder> | import <folder> <name>]"
)


class ProfileCommands:
    def __init__(
        self,
        reporter: Reporter,
        profiles: ProfileStore,
        switch: ProfileSwitch,
        sessions: SessionStore,
        main_id: MainId,
        boxes: MainBoxes,
    ) -> None:
        self._copies = ProfileCopies(profiles.data_dir, profiles)
        self._reporter = reporter
        self._profiles = profiles
        self._switch = switch
        self._sessions = sessions
        self._main_id = main_id
        self._boxes = boxes

    def run(self, text: str = "") -> bool:
        """``/profile`` lists the Profiles with their Sessions; ``/profile new [name]`` makes one;
        ``/profile switch <name> [--now]`` makes Main use another; ``/profile share <sid>
        <profile>`` and ``/profile unshare <sid> [profile]`` put a Session in a Profile's list or
        take it out (the active one's by default)."""
        match text.split():
            case []:
                self._list()
            case ["new"]:
                self._new(None)
            case ["new", name]:
                self._new(name)
            case ["switch", name]:
                self._switch.ask(name, now=False)
            case ["switch", name, "--now"]:
                self._switch.ask(name, now=True)
            case ["share", session_id, name]:
                self._share(session_id, name)
            case ["unshare", session_id]:
                self._unshare(session_id, None)
            case ["unshare", session_id, name]:
                self._unshare(session_id, name)
            case ["export", name, folder]:
                self._export(name, Path(folder).expanduser())
            case ["import", folder, name]:
                self._import(Path(folder).expanduser(), name)
            case _:
                raise InvalidArguments(usage=USAGE)
        return True

    def created(self, session_id: SessionId) -> None:
        """A Session ``/new`` made: it belongs to the active Profile."""
        self._profiles.add_session(self._switch.active, session_id)

    def renamed(self, old: SessionId, new: SessionId) -> None:
        """A Session took a new id: every Profile follows it (#59)."""
        self._profiles.rename_session(old, new, self._switch.active.name)

    def opened(self, session_id: SessionId) -> None:
        """A Session a human opened: it belongs to the active Profile from now on, and if it
        was only another Profile's, a Notice says so, since a Profile is private."""
        active = self._switch.active
        owners = self._profiles.profiles_of(session_id)
        if owners and active.name not in owners:
            text = (
                f"{session_id} belongs to profile {', '.join(owners)}; added to {active.name} too"
            )
            self._boxes.notify(None, "Session shared", text)
            self._reporter.log_main("session_shared", session=session_id, profile=active.name)
        self._profiles.add_session(active, session_id)

    def in_active(self, session_id: SessionId) -> bool:
        """Whether the Session is one of the active Profile's: all a Session reaches with
        ``/send`` (ADR-0010)."""
        return self._switch.active.name in self._profiles.profiles_of(session_id)

    def profiles_of(self, session_id: SessionId) -> list[str]:
        return list(self._profiles.profiles_of(session_id))

    def _list(self) -> None:
        active = self._switch.active.name
        for name in self._profiles.names():
            shown = f"{name} (active)" if name == active else name
            sessions = self._profiles.sessions(self._profiles.find(name))
            self._reporter.show(None, f"{shown}: {', '.join(sessions)}" if sessions else shown)

    def _new(self, name: str | None) -> None:
        # It copies the active Profile's Settings with its pieces, because they name them.
        created = self._profiles.create(name, self._switch.active)
        shown = f"profile {created.name} created"
        self._reporter.report(None, "profile_created", shown, profile=created.name)

    def _export(self, name: str, folder: Path) -> None:
        profile = self._profiles.find(name)
        count = self._copies.export(profile, folder)
        shown = f"profile {profile.name} exported: {count} sessions"
        self._reporter.report(None, "profile_exported", shown, profile=profile.name, sessions=count)

    def _import(self, folder: Path, name: str) -> None:
        profile, renamed = self._copies.import_(folder, name)
        shown = f"profile {profile.name} imported"
        if renamed:
            shown += ", new ids: " + ", ".join(f"{old}→{new}" for old, new in renamed.items())
        fields: dict[str, JsonValue] = {str(old): str(new) for old, new in renamed.items()}
        self._reporter.report(None, "profile_imported", shown, profile=profile.name, renamed=fields)

    def _share(self, session_id: str, name: str) -> None:
        """Only the lists change: sharing is no Traspaso, and the Session's folder stays."""
        session = self._sessions.load_owned(session_id, self._main_id)
        profile = self._profiles.find(name)
        self._profiles.add_session(profile, session.id)
        shown = f"{session.id} shared with profile {profile.name}"
        self._reporter.report(
            None, "session_shared", shown, session=session.id, profile=profile.name
        )

    def _unshare(self, session_id: str, name: str | None) -> None:
        session = self._sessions.load_owned(session_id, self._main_id)
        profile: Profile = self._switch.active if name is None else self._profiles.find(name)
        self._profiles.remove_session(profile, session.id)
        shown = f"{session.id} no longer in profile {profile.name}"
        self._reporter.report(
            None, "session_unshared", shown, session=session.id, profile=profile.name
        )
