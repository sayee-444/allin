"""``/key``: the Atajos of Main's Chat, of a Session's Window and of the Session template (#57).

Only a human changes them (``/key`` is human-only, ADR-0013), and a Kit never brings them: they
live outside the Settings registry, which is all a Kit carries.
"""

from collections.abc import Callable

from allin.core.errors import AllInError, InvalidArguments
from allin.core.ids import MainId, parse_session_id
from allin.ipc.messages import KeyTable
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore
from allin.main.settings_commands import SettingsCommands
from allin.ui.screen import KeyBindings, ScreenUpdate
from allin.ui.shortcuts import assign, effective_keys, parse_key, shown_keys

USAGE = "/key | /key <sid> | /key [<sid>|--template] <key> <action|/command|off> [--replace]"
_TEMPLATE = "--template"
_REPLACE = "--replace"


class KeyCommands:
    def __init__(
        self,
        reporter: Reporter,
        settings: SettingsCommands,
        sessions: SessionStore,
        main_id: MainId,
        windows: OpenWindows,
        main_screen: Callable[[ScreenUpdate], None],
    ) -> None:
        self._reporter = reporter
        self._settings = settings
        self._sessions = sessions
        self._main_id = main_id
        self._windows = windows
        self._main_screen = main_screen
        settings.keys_changed = self.show_main

    def show_main(self, changes: dict[str, str]) -> None:
        """Main's Chat takes its Atajos: on start, and whenever they change."""
        self._main_screen(KeyBindings(effective_keys(changes)))

    def run(self, text: str = "") -> bool:
        words = text.split()
        replace = _REPLACE in words[-1:]
        if replace:
            words = words[:-1]
        if not words:
            self._list()
            return True
        target, words = self._target(words)
        if not words and target not in (None, _TEMPLATE):
            session = self._sessions.load_owned(target, self._main_id)
            self._show(session.id, "", session.settings.keys)
            return True
        if len(words) < 2:
            raise InvalidArguments(usage=USAGE)
        key, binding = words[0], " ".join(words[1:])
        if target is None:
            current = self._settings.current
            changes = assign(current.keys, key, binding, replace, commands=True)
            self._settings.use(current.model_copy(update={"keys": changes}))
        elif target == _TEMPLATE:
            current = self._settings.current
            changes = assign(current.session.keys, key, binding, replace, commands=False)
            template = current.session.model_copy(update={"keys": changes})
            self._settings.use(current.model_copy(update={"session": template}))
        else:
            session = self._sessions.load_owned(target, self._main_id)
            changes = assign(session.settings.keys, key, binding, replace, commands=False)
            self._settings.save_session(
                session, session.settings.model_copy(update={"keys": changes})
            )
            self._windows.send(session.id, KeyTable(bindings=effective_keys(changes)))
        return True

    def _target(self, words: list[str]) -> tuple[str | None, list[str]]:
        """Whose Atajos: Main's Chat (``None``), the template, or a Session named first. A first
        word that names a key is the key: no Session id is a key's name. A first word that is
        neither, or the first of only two, is a bad key, which says why."""
        if words[0] == _TEMPLATE:
            return _TEMPLATE, words[1:]
        try:
            parse_key(words[0])
        except AllInError:
            if len(words) == 2 or parse_session_id(words[0]) is None:
                raise
            return words[0], words[1:]
        return None, words

    def _list(self) -> None:
        current = self._settings.current
        self._show(None, "main: ", current.keys)
        self._show(None, "template: ", current.session.keys)

    def _show(self, session_id: str | None, prefix: str, changes: dict[str, str]) -> None:
        for line in shown_keys(changes):
            self._reporter.show(session_id, f"{prefix}{line}")
