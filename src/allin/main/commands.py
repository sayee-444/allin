"""The Command mains: what each one is called, the arguments it takes and what it does."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from allin.core.errors import AllInError, InvalidArguments, UnknownCommand, WindowAlreadyOpen
from allin.core.ids import MainId
from allin.main.open_windows import OpenWindows
from allin.main.prompts import Prompt
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore
from allin.main.settings_commands import SettingsCommands, parse_session_command
from allin.main.sysprompt import render_sysprompt
from allin.main.turn_queue import TurnQueue


@dataclass(frozen=True)
class _MainCommand:
    params: tuple[str, ...]
    run: Callable[..., bool]  # takes one str per param; returns whether Main keeps running
    rest: bool = False  # the last param takes the rest of the line verbatim, newlines included
    optional: tuple[str, ...] = ()  # params after ``params`` that may be left out
    # The Session it concerns, from its arguments; by default its ``sid`` param.
    session_of: Callable[[list[str]], str | None] | None = None

    def usage(self, name: str) -> str:
        words = [name, *(f"<{param}>" for param in self.params)]
        return " ".join(words + [f"[{param}]" for param in self.optional])


class MainCommands:
    def __init__(
        self,
        reporter: Reporter,
        windows: OpenWindows,
        turns: TurnQueue,
        sessions: SessionStore,
        main_id: MainId,
        settings: SettingsCommands,
        sysprompts: Path,
    ) -> None:
        self._reporter = reporter
        self._windows = windows
        self._turns = turns
        self._sessions = sessions
        self._main_id = main_id
        self._settings = settings
        self._sysprompts = sysprompts
        # Command mains are registered once, when Main starts.
        self._commands: dict[str, _MainCommand] = {
            "/new": _MainCommand((), self._new),
            "/open": _MainCommand(("sid",), self._open),
            # The only Command main that queues Prompts; Enter in a Window is /send to its own.
            "/send": _MainCommand(("sid", "text"), self._send, rest=True),
            "/raw": _MainCommand(("state",), self._set_raw, optional=("sid",)),
            "/set": _MainCommand((), settings.set, rest=True, optional=("key=value",)),
            "/reset": _MainCommand((), settings.reset),
            "/session": _MainCommand(
                ("sid",),
                settings.session,
                rest=True,
                optional=("key=value",),
                session_of=_session_of_session_command,
            ),
            "/exit": _MainCommand((), lambda: False),
        }

    def run(self, text: str) -> bool:
        """Run a Command main and report how it went; return whether Main keeps running."""
        name = (text.split(maxsplit=1) or [""])[0]
        command = self._commands.get(name)
        args = text.split()[1:]
        if command is not None and command.rest:
            args = _split_rest(text, len(command.params) + len(command.optional))
        session_id = None
        keep_running = True
        try:
            if command is None:
                raise UnknownCommand(command=name)
            names = command.params + command.optional
            session_id = (
                command.session_of(args)
                if command.session_of is not None
                else dict(zip(names, args, strict=False)).get("sid")
            )
            if not len(command.params) <= len(args) <= len(names):
                raise InvalidArguments(usage=command.usage(name))
            keep_running = command.run(*args)
        except AllInError as error:
            self._report(session_id, text, error)
        else:
            self._report(session_id, text, None)
        return keep_running

    def _report(self, session_id: str | None, text: str, error: AllInError | None) -> None:
        result = "ok" if error is None else f"error {error}"
        shown = f"{_summary(text)}: {result}"
        self._reporter.report(session_id, "command", shown, command=text, result=result)

    def _new(self) -> bool:
        settings = self._settings.current.session  # copied: later changes never reach it
        sysprompt = render_sysprompt(self._sysprompts, settings)
        session = self._sessions.create(self._main_id, settings, sysprompt)
        self._reporter.record(session.id, "session_created", "session created", owner=self._main_id)
        self._windows.open(session, self._turns.streamed(session.id))
        return True

    def _open(self, session_id: str) -> bool:
        session = self._sessions.load_owned(session_id, self._main_id)
        if self._windows.is_open(session.id):
            raise WindowAlreadyOpen(session_id=session.id)
        self._windows.open(session, self._turns.streamed(session.id))
        return True

    def _send(self, session_id: str, text: str) -> bool:
        session = self._sessions.load_owned(session_id, self._main_id)
        self._turns.push(session, Prompt(session.id, text))
        return True

    def _set_raw(self, state: str, session_id: str | None = None) -> bool:
        if state not in ("on", "off"):
            raise InvalidArguments(usage=self._commands["/raw"].usage("/raw"))
        if session_id is not None:
            session_id = self._sessions.load_owned(session_id, self._main_id).id
        self._reporter.show_raw(state == "on", session_id)
        return True


def _session_of_session_command(args: list[str]) -> str | None:
    target = parse_session_command(args)
    return None if target is None else target.session_id


def _summary(text: str, width: int = 60) -> str:
    """A Command main as Main's Chat shows it: its first line, cut to ``width``."""
    lines = text.splitlines() or [""]
    first = lines[0]
    if len(lines) > 1 or len(first) > width:
        return first[:width] + "…"
    return first


def _split_rest(text: str, params: int) -> list[str]:
    """The arguments of a command whose last param takes the rest of the line.

    Words are separated by any whitespace; the rest starts after a single space or tab and is
    kept as typed (leading spaces and newlines matter in a Prompt). A blank rest is missing.
    """
    words = r"\s*\S+" + r"\s+(\S+)" * (params - 1)
    match = re.fullmatch(words + r"(?:[ \t](.*))?", text, re.DOTALL)
    if match is None:
        return []
    *args, rest = match.groups()
    return [*args, rest] if rest and rest.strip() else args
