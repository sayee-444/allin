"""The Command mains that read and change Settings: ``/set``, ``/reset`` and ``/session``.

Main's current Settings and its defaults live here, as their files last had them; every change
is checked whole before anything is written, so a bad key or value changes nothing.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from allin.core.errors import HumanOnly, InvalidArguments, SyspromptLocked
from allin.core.ids import MainId, SessionId
from allin.core.origin import HUMAN, Origin
from allin.core.settings import (
    MainSettings,
    SessionSettings,
    SettingKey,
    SettingName,
    apply_settings,
    parse_assignments,
    shown_settings,
)
from allin.main.conversation import Conversation
from allin.main.devastation import Devastation
from allin.main.links import LinkResolver
from allin.main.notices import NoticeTypes
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.settings_files import LoadedSettings, MainSettingsFiles
from allin.main.sysprompt import render_sysprompt
from allin.main.turn_queue import TurnQueue

_DEFAULT_FLAG = "--default"
_HUMAN_ONLY = frozenset({SettingName("exempt"), SettingName("session.exempt")})
# What a Sysprompt is rendered from, so changing one renders it again.
_SYSPROMPT_KEYS = ("sysprompt", "assistant_name", "username")
_RESTART_NOTE = "base_url takes effect when Main restarts"


_SESSION_USAGE = (
    "/session <sid> | /session set <sid> [key=value…] | /session compact <sid> [instructions]"
)


@dataclass(frozen=True)
class SessionCommand:
    """What ``/session`` was asked about: a Session and, after ``set``, its assignments, or
    after ``compact``, the human's instructions for the summary."""

    session_id: str  # as typed; loading it checks it
    rest: str = ""
    compact: bool = False


def parse_session_command(args: list[str]) -> SessionCommand | None:
    """``/session <sid>``, ``/session set <sid> [key=value…]`` or ``/session compact <sid>
    [instructions]``, from the words after ``/session`` (the second one holding the rest of
    the line); ``None`` if it is none of them."""
    if not args:
        return None
    if args[0] not in ("set", "compact"):
        return SessionCommand(args[0]) if len(args) == 1 else None
    words = args[1].split(maxsplit=1) if len(args) > 1 else []
    if not words:
        return None
    rest = words[1] if len(words) > 1 else ""
    return SessionCommand(words[0], rest, compact=args[0] == "compact")


def _check_authority(assignments: Mapping[SettingName, str], origin: Origin) -> None:
    """Only a human gives or takes away the Exemption (ADR-0013)."""
    for name in assignments:
        if name in _HUMAN_ONLY and origin != HUMAN:
            raise HumanOnly(action=name, origin=origin)


class SettingsCommands:
    def __init__(
        self,
        reporter: Reporter,
        turns: TurnQueue,
        sessions: SessionStore,
        main_id: MainId,
        loaded: LoadedSettings,
        files: MainSettingsFiles,
        sysprompts: Path,
        devastation: Devastation,
        notices: NoticeTypes,
        links: LinkResolver,
    ) -> None:
        self._links = links
        self._devastation = devastation
        self._notices = notices
        self._reporter = reporter
        self._turns = turns
        self._sessions = sessions
        self._main_id = main_id
        self._current = loaded.current
        self._default = loaded.default
        self._files = files
        self._sysprompts = sysprompts
        self._profiles_of: Callable[[SessionId], list[str]] = lambda _: []
        # Told the Atajos of Main's Chat whenever its current Settings change them (#57).
        self.keys_changed: Callable[[dict[str, str]], None] = lambda _: None

    def show_profiles(self, profiles_of: Callable[[SessionId], list[str]]) -> None:
        """``/session <sid>`` also shows the Profiles the Session belongs to (ADR-0010)."""
        self._profiles_of = profiles_of

    @property
    def current(self) -> MainSettings:
        return self._current

    def set(self, text: str = "", origin: Origin = HUMAN) -> bool:
        """``/set [--default] [key=value…]``: change Main's current or default Settings, or
        show them when no key is given."""
        words = text.split(maxsplit=1)
        to_default = bool(words) and words[0] == _DEFAULT_FLAG
        rest = (words[1] if len(words) > 1 else "") if to_default else text
        target = self._default if to_default else self._current
        assignments = parse_assignments(rest)
        extra = self._notices.setting_keys(prefix="session.")
        if not assignments:
            self._show(None, target, extra)
            return True
        _check_authority(assignments, origin)
        changed = apply_settings(target, assignments, extra)
        if changed.session.sysprompt != target.session.sysprompt:
            render_sysprompt(self._sysprompts, changed.session)  # the file must be there
        # The default Enlace moves no Session: only those /new makes from now on (ADR-0022).
        link = None
        if changed.session.server != target.session.server:
            link = self._links.resolve(changed.session)  # its Supervisor must have Settings
        if to_default:
            self._files.save_default(changed)
            self._default = changed
        else:
            self._use(changed)
            if link is not None:  # its state is known, and told, before /new uses it
                self._turns.watch(link)
        return True

    def reset(self) -> bool:
        """``/reset``: the current Settings become the defaults again."""
        self._use(self._default)
        return True

    def session(self, first: str, rest: str = "", origin: Origin = HUMAN) -> bool:
        """``/session <sid>`` shows a Session's Settings; ``/session set <sid> key=value…``
        changes them; ``/session compact <sid> [instructions]`` compacts its Conversation,
        without a Confirmation since a Rewind undoes it (ADR-0017)."""
        target = parse_session_command([first, rest] if rest else [first])
        if target is None:
            raise InvalidArguments(usage=_SESSION_USAGE)
        session = self._sessions.load_owned(target.session_id, self._main_id)
        if target.compact:
            self._turns.compact(session, target.rest)
            return True
        assignments = parse_assignments(target.rest)
        extra = self._notices.setting_keys()
        if not assignments:
            self._show(session.id, session.settings, extra)
            self._reporter.show(session.id, f"profiles={', '.join(self._profiles_of(session.id))}")
            return True
        _check_authority(assignments, origin)
        changed = apply_settings(session.settings, assignments, extra)
        # The Enlace changes on its own once the new Server is ready (ADR-0022); checked here,
        # with the rest, before anything is written.
        link = self._links.resolve(changed) if "server" in assignments else None
        self._save_session(session, changed.model_copy(update={"server": session.settings.server}))
        if changed.exempt != session.settings.exempt:
            self._devastation.mark(session.id)
        if link is not None:
            self._turns.change_link(self._sessions.load(session.id), link)
        return True

    def use(self, settings: MainSettings) -> None:
        """Make ``settings`` the current ones, on disk and in the running Main."""
        self._use(settings)

    def save_session(self, session: Session, settings: SessionSettings) -> None:
        self._save_session(session, settings)

    def take(self, settings: MainSettings) -> None:
        """Main's current Settings become ``settings``: a Kit's, chosen when it loaded."""
        self._use(settings)

    def enter(self, loaded: LoadedSettings, files: MainSettingsFiles, sysprompts: Path) -> None:
        """The Settings and Sysprompts of the Profile Main just entered (``/profile switch``):
        what changes goes on in the running Main, and its default Enlace only reaches the
        Sessions ``/new`` makes from now on (ADR-0022)."""
        self._files, self._default, self._sysprompts = files, loaded.default, sysprompts
        self._apply(loaded.current)

    def _use(self, settings: MainSettings) -> None:
        """Make ``settings`` the current ones, on disk and in the running Main."""
        self._files.save_current(settings)
        self._apply(settings)

    def _apply(self, settings: MainSettings) -> None:
        """Make ``settings`` the current ones in the running Main."""
        before, self._current = self._current, settings
        if settings.parallel != before.parallel:
            self._turns.set_parallel(settings.parallel)
        if settings.step.stall != before.step.stall:
            self._turns.set_stall(settings.step.stall)
        if settings.admission.patience != before.admission.patience:
            self._turns.set_patience(settings.admission.patience)
        if settings.base_url != before.base_url:
            self._reporter.report(None, "settings_restart_needed", _RESTART_NOTE)
        if settings.keys != before.keys:
            self.keys_changed(settings.keys)

    def _save_session(self, session: Session, settings: SessionSettings) -> None:
        """Save a Session's new Settings, rendering its Sysprompt again while it has no Turn."""
        if all(getattr(settings, key) == getattr(session.settings, key) for key in _SYSPROMPT_KEYS):
            self._sessions.save_settings(session, settings)
            return
        conversation = self._sessions.load_conversation(session) or Conversation(sysprompt="")
        # A running Turn is saved with the Sysprompt it was sent, so it counts as one already.
        if conversation.started or self._turns.streamed(session.id) is not None:
            # Its Sysprompt is fixed; new names only reach the Chat.
            if settings.sysprompt != session.settings.sysprompt:
                raise SyspromptLocked(session_id=session.id)
            self._sessions.save_settings(session, settings)
            return
        sysprompt = render_sysprompt(self._sysprompts, settings)  # fails before any write
        saved = self._sessions.save_settings(session, settings)
        self._sessions.save_conversation(
            saved, conversation.model_copy(update={"sysprompt": sysprompt})
        )

    def _show(
        self,
        session_id: str | None,
        settings: MainSettings | SessionSettings,
        extra: list[SettingKey],
    ) -> None:
        for line in shown_settings(settings, extra):
            self._reporter.show(session_id, line)
