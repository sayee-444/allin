"""``/profile``: the Profiles there are, and making a new one (ADR-0010)."""

from allin.core.errors import InvalidArguments
from allin.core.ids import SessionId
from allin.main.profiles import Profile, ProfileStore
from allin.main.reporting import Reporter

_USAGE = "/profile [new [name]]"


class ProfileCommands:
    def __init__(self, reporter: Reporter, profiles: ProfileStore, active: Profile) -> None:
        self._reporter = reporter
        self._profiles = profiles
        self._active = active

    def run(self, text: str = "") -> bool:
        """``/profile`` lists the Profiles; ``/profile new [name]`` makes one."""
        match text.split():
            case []:
                self._list()
            case ["new"]:
                self._new(None)
            case ["new", name]:
                self._new(name)
            case _:
                raise InvalidArguments(usage=_USAGE)
        return True

    def joined(self, session_id: SessionId) -> None:
        """A Session the active Profile created or opened: it belongs to it from now on."""
        self._profiles.add_session(self._active, session_id)

    def _list(self) -> None:
        for name in self._profiles.names():
            self._reporter.show(None, f"{name} (active)" if name == self._active.name else name)

    def _new(self, name: str | None) -> None:
        # It copies the active Profile's Settings with its pieces, because they name them.
        created = self._profiles.create(name, self._active)
        shown = f"profile {created.name} created"
        self._reporter.report(None, "profile_created", shown, profile=created.name)
