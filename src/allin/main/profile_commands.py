"""``/profile``: the Profiles there are, making a new one and switching to another
(ADR-0010)."""

from allin.core.errors import InvalidArguments
from allin.core.ids import SessionId
from allin.main.profile_switch import ProfileSwitch
from allin.main.profiles import ProfileStore
from allin.main.reporting import Reporter

_USAGE = "/profile [new [name] | switch <name> [--now]]"


class ProfileCommands:
    def __init__(self, reporter: Reporter, profiles: ProfileStore, switch: ProfileSwitch) -> None:
        self._reporter = reporter
        self._profiles = profiles
        self._switch = switch

    def run(self, text: str = "") -> bool:
        """``/profile`` lists the Profiles; ``/profile new [name]`` makes one; ``/profile switch
        <name> [--now]`` makes Main use another."""
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
            case _:
                raise InvalidArguments(usage=_USAGE)
        return True

    def joined(self, session_id: SessionId) -> None:
        """A Session the active Profile created or opened: it belongs to it from now on."""
        self._profiles.add_session(self._switch.active, session_id)

    def _list(self) -> None:
        active = self._switch.active.name
        for name in self._profiles.names():
            self._reporter.show(None, f"{name} (active)" if name == active else name)

    def _new(self, name: str | None) -> None:
        # It copies the active Profile's Settings with its pieces, because they name them.
        created = self._profiles.create(name, self._switch.active)
        shown = f"profile {created.name} created"
        self._reporter.report(None, "profile_created", shown, profile=created.name)
