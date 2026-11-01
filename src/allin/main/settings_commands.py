"""The Command mains that read and change Settings: ``/set``, ``/reset`` and ``/session``.

Main's current Settings and its defaults live here, as their files last had them; every change
is checked whole before anything is written, so a bad key or value changes nothing.
"""

from pathlib import Path

from allin.core.errors import SyspromptLocked
from allin.core.ids import MainId
from allin.core.settings import (
    MainSettings,
    SessionSettings,
    apply_settings,
    parse_assignments,
    shown_settings,
)
from allin.main.conversation import Conversation
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.settings_files import LoadedSettings, MainSettingsFiles
from allin.main.sysprompt import render_sysprompt
from allin.main.turn_queue import TurnQueue

_DEFAULT_FLAG = "--default"
# What a Sysprompt is rendered from, so changing one renders it again.
_SYSPROMPT_KEYS = ("sysprompt", "assistant_name", "username")
_RESTART_NOTE = "base_url takes effect when Main restarts"


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
    ) -> None:
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

    def set(self, text: str = "") -> bool:
        """``/set [--default] [key=value…]``: change Main's current or default Settings, or
        show them when no key is given."""
        words = text.split(maxsplit=1)
        to_default = bool(words) and words[0] == _DEFAULT_FLAG
        rest = (words[1] if len(words) > 1 else "") if to_default else text
        target = self._default if to_default else self._current
        assignments = parse_assignments(rest)
        if not assignments:
            self._show(None, target)
            return True
        changed = apply_settings(target, assignments)
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

    def session(self, first: str, rest: str = "") -> bool:
        """``/session <sid>`` shows a Session's Settings; ``/session set <sid> key=value…``
        changes them."""
        if first != "set":
            self._show(first, self._sessions.load_owned(first, self._main_id).settings)
            return True
        session_id, _, assignments = rest.strip().partition(" ")
        session = self._sessions.load_owned(session_id, self._main_id)
        parsed = parse_assignments(assignments)
        if not parsed:
            self._show(session.id, session.settings)
            return True
        self._save_session(session, apply_settings(session.settings, parsed))
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

    def _show(self, session_id: str | None, settings: MainSettings | SessionSettings) -> None:
        for line in shown_settings(settings):
            self._reporter.show(session_id, line)
