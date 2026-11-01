"""The Command mains that read and change Settings: ``/set``, ``/reset`` and ``/session``.

Main's current Settings and its defaults live here, as their files last had them; every change
is checked whole before anything is written, so a bad key or value changes nothing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from allin.core.errors import HumanOnly, InvalidArguments, InvalidSetting, SyspromptLocked
from allin.core.ids import MainId
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


_SESSION_USAGE = "/session <sid> | /session set <sid> [key=value…]"


@dataclass(frozen=True)
class SessionCommand:
    """What ``/session`` was asked about: a Session and, after ``set``, its assignments."""

    session_id: str  # as typed; loading it checks it
    assignments: str = ""


def parse_session_command(args: list[str]) -> SessionCommand | None:
    """``/session <sid>`` or ``/session set <sid> [key=value…]``, from the words after
    ``/session`` (the second one holding the rest of the line); ``None`` if it is neither."""
    if not args:
        return None
    if args[0] != "set":
        return SessionCommand(args[0]) if len(args) == 1 else None
    words = args[1].split(maxsplit=1) if len(args) > 1 else []
    if not words:
        return None
    return SessionCommand(words[0], words[1] if len(words) > 1 else "")


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
    ) -> None:
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
        if to_default:
            self._files.save_default(changed)
            self._default = changed
        else:
            self._use(changed)
        return True

    def reset(self) -> bool:
        """``/reset``: the current Settings become the defaults again."""
        self._use(self._default)
        return True

    def session(self, first: str, rest: str = "", origin: Origin = HUMAN) -> bool:
        """``/session <sid>`` shows a Session's Settings; ``/session set <sid> key=value…``
        changes them."""
        target = parse_session_command([first, rest] if rest else [first])
        if target is None:
            raise InvalidArguments(usage=_SESSION_USAGE)
        session = self._sessions.load_owned(target.session_id, self._main_id)
        assignments = parse_assignments(target.assignments)
        extra = self._notices.setting_keys()
        if not assignments:
            self._show(session.id, session.settings, extra)
            return True
        _check_authority(assignments, origin)
        if "server" in assignments:  # atomic, waiting for the new Supervisor: #27 (ADR-0022)
            raise InvalidSetting(key="server", reason="a Session's Enlace cannot change yet")
        changed = apply_settings(session.settings, assignments, extra)
        self._save_session(session, changed)
        if changed.exempt != session.settings.exempt:
            self._devastation.mark(session.id)
        return True

    def _use(self, settings: MainSettings) -> None:
        """Make ``settings`` the current ones, on disk and in the running Main."""
        self._files.save_current(settings)
        before, self._current = self._current, settings
        if settings.parallel != before.parallel:
            self._turns.set_parallel(settings.parallel)
        if settings.base_url != before.base_url:
            self._reporter.report(None, "settings_restart_needed", _RESTART_NOTE)

    def _save_session(self, session: Session, settings: SessionSettings) -> None:
        """Save a Session's new Settings, rendering its Sysprompt again while it has no Turn."""
        if all(getattr(settings, key) == getattr(session.settings, key) for key in _SYSPROMPT_KEYS):
            self._sessions.save_settings(session, settings)
            return
        conversation = self._sessions.load_conversation(session) or Conversation(sysprompt="")
        # A running Turn is saved with the Sysprompt it was sent, so it counts as one already.
        if conversation.turns or self._turns.streamed(session.id) is not None:
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
