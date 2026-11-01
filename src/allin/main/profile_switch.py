"""``/profile switch``: Main leaves its Profile and enters another without restarting (ADR-0010).

No Turn starts once a switch is asked. When the running ones end (``--now`` cancels them, and a
Prompt cancelled so goes back to its input), the outgoing Profile keeps its MainState with its
queue, the Windows of Sessions the incoming Profile does not have close, and the incoming
Profile's Settings, Sysprompts and MainState take over. Windows of Sessions both share stay
open, and no Session's Enlace changes (ADR-0022).
"""

from collections.abc import Callable, Container
from dataclasses import dataclass

from allin.core.errors import AllInError
from allin.core.ids import SessionId
from allin.main.open_windows import OpenWindows
from allin.main.profiles import Profile, ProfileStore
from allin.main.recovery import Recovery
from allin.main.reporting import Reporter
from allin.main.state import MainStateStore
from allin.main.turn_queue import TurnQueue

SWITCHED = "profile switched"

# Makes the running Main use a Profile's Settings and Sysprompts.
type EnterProfile = Callable[[Profile], None]


@dataclass(frozen=True)
class _Asked:
    profile: Profile
    now: bool


class ProfileSwitch:
    def __init__(
        self,
        reporter: Reporter,
        profiles: ProfileStore,
        windows: OpenWindows,
        turns: TurnQueue,
        recovery: Recovery,
        active: Profile,
        enter: EnterProfile,
    ) -> None:
        self._reporter = reporter
        self._profiles = profiles
        self._windows = windows
        self._turns = turns
        self._recovery = recovery
        self._active = active
        self._enter = enter
        self._asked: _Asked | None = None

    @property
    def active(self) -> Profile:
        return self._active

    def ask(self, name: str, now: bool) -> None:
        """Switch to the Profile ``name`` once no Turn runs; ``now`` cancels the running ones.

        Asking again replaces a switch that waits, and asking for the active Profile drops it.
        """
        profile = self._profiles.find(name)  # one that does not exist changes nothing
        if profile == self._active:
            self._drop(profile)
            return
        now = now or (self._asked is not None and self._asked.now)
        self._asked = _Asked(profile, now)
        self._turns.pause()
        if now:
            for prompt in self._turns.cancel_turns(SWITCHED):
                self._windows.put_back(prompt.session_id, prompt.text)
        if self._turns.has_running():
            turns = self._turns.running_count()
            shown = f"profile switch to {profile.name}: waiting for {_turns(turns)} to end"
            self._reporter.report(
                None, "profile_switch_waiting", shown, profile=profile.name, turns=turns
            )
        self.check()

    def check(self) -> None:
        """Switch, if one was asked and no Turn runs any more."""
        if self._asked is None or self._turns.has_running():
            return
        target = self._asked.profile
        self._asked = None
        try:
            kept = set(self._profiles.sessions(target))
            entered = self._profiles.open(target.name)
        except AllInError as error:  # nothing changed yet: Main stays where it was
            self._turns.resume()
            self._windows.show_error(error)
            return
        self._leave(kept)
        self._active = entered
        self._enter(entered)
        self._recovery.enter(MainStateStore(entered.state))
        self._turns.resume()
        shown = f"profile {entered.name} active"
        self._reporter.report(None, "profile_switched", shown, profile=entered.name)

    def _leave(self, kept: Container[SessionId]) -> None:
        """The outgoing Profile keeps its MainState, queue included, and the Windows of its
        Sessions that ``kept`` does not have close."""
        self._recovery.save()
        self._turns.take_queue()
        for session_id in self._windows.pids():
            if session_id not in kept:
                self._windows.close(session_id, SWITCHED)

    def _drop(self, profile: Profile) -> None:
        if self._asked is None:
            self._reporter.show(None, f"profile {profile.name} is already active")
            return
        dropped = self._asked.profile.name
        self._asked = None
        self._turns.resume()
        shown = f"profile switch to {dropped} dropped"
        self._reporter.report(None, "profile_switch_dropped", shown, profile=dropped)


def _turns(count: int) -> str:
    return "1 turn" if count == 1 else f"{count} turns"
