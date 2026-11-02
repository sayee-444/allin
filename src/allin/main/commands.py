"""The Command mains: what each one is called, the arguments it takes and what it does."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from allin.core.errors import (
    AllInError,
    HumanOnly,
    InvalidArguments,
    UnknownCommand,
    WindowAlreadyOpen,
)
from allin.core.ids import MainId
from allin.core.origin import HUMAN, Origin
from allin.main.boxes import MainBoxes
from allin.main.devastation import Devastation
from allin.main.kit_commands import KitCommands
from allin.main.open_windows import OpenWindows
from allin.main.profile_commands import ProfileCommands
from allin.main.prompts import Prompt
from allin.main.reporting import Reporter
from allin.main.server_commands import ServerCommands
from allin.main.server_commands import delicate as server_delicate
from allin.main.server_commands import human_only as server_human_only
from allin.main.sessions import SessionStore
from allin.main.settings_commands import SettingsCommands, parse_session_command
from allin.main.sysprompt import render_sysprompt
from allin.main.turn_queue import TurnQueue

# Whether a command, by its arguments, is delicate or only a human's.
type ByArguments = Callable[[list[str]], bool]


@dataclass(frozen=True)
class _MainCommand:
    params: tuple[str, ...]
    run: Callable[..., bool]  # takes one str per param; returns whether Main keeps running
    rest: bool = False  # the last param takes the rest of the line verbatim, newlines included
    optional: tuple[str, ...] = ()  # params after ``params`` that may be left out
    # The Session it concerns, from its arguments; by default its ``sid`` param.
    session_of: Callable[[list[str]], str | None] | None = None
    # Delicate: asks a Confirmation first, which ``-y`` skips (ADR-0009).
    confirm: bool | ByArguments = False
    human_only: bool | ByArguments = False  # any other Origin is an error (ADR-0013)
    takes_origin: bool = False  # ``run`` also gets the Origin, as ``origin=``

    def delicate(self, args: list[str]) -> bool:
        return self.confirm(args) if callable(self.confirm) else self.confirm

    def only_human(self, args: list[str]) -> bool:
        return self.human_only(args) if callable(self.human_only) else self.human_only

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
        boxes: MainBoxes,
        devastation: Devastation,
        server: ServerCommands,
        profiles: ProfileCommands,
        kits: KitCommands,
    ) -> None:
        self._boxes = boxes
        self._devastation = devastation
        self._reporter = reporter
        self._windows = windows
        self._turns = turns
        self._sessions = sessions
        self._main_id = main_id
        self._settings = settings
        self._sysprompts = sysprompts
        self._profiles = profiles
        # Command mains are registered once, when Main starts.
        self._commands: dict[str, _MainCommand] = {
            "/new": _MainCommand((), self._new),
            "/open": _MainCommand(("sid",), self._open),
            # The only Command main that queues Prompts; Enter in a Window is /send to its own.
            "/send": _MainCommand(("sid", "text"), self._send, rest=True),
            "/raw": _MainCommand(("state",), self._set_raw, optional=("sid",)),
            "/set": _MainCommand(
                (), settings.set, rest=True, optional=("key=value",), takes_origin=True
            ),
            # Delicate: the current Settings are lost unless they were also the defaults.
            "/reset": _MainCommand((), settings.reset, confirm=True),
            "/session": _MainCommand(
                ("sid",),
                settings.session,
                rest=True,
                optional=("key=value",),
                session_of=_session_of_session_command,
                takes_origin=True,
            ),
            "/devastation": _MainCommand(("state",), self._switch_devastation, human_only=True),
            "/server": _MainCommand(
                (),
                server.run,
                rest=True,
                optional=("action…",),
                takes_origin=True,
                confirm=server_delicate,
                human_only=server_human_only,
            ),
            "/profile": _MainCommand((), profiles.run, rest=True, optional=("action…",)),
            # A Kit writes pieces into the Profile and may suggest Supervisors (ADR-0014).
            "/kit": _MainCommand((), kits.run, rest=True, optional=("action…",), human_only=True),
            "/exit": _MainCommand((), lambda: False),
        }

    def use_sysprompts(self, sysprompts: Path) -> None:
        """The active Profile's, which Sessions are born with, once Main switched to it."""
        self._sysprompts = sysprompts

    def run(self, text: str, origin: Origin = HUMAN) -> bool:
        """Run a Command main and report how it went; return whether Main keeps running.

        A delicate one first asks a Confirmation, unless ``-y`` (only from a human) or
        ``--devastation`` answers it; it then runs when the answer comes.
        """
        name = (text.split(maxsplit=1) or [""])[0]
        command = self._commands.get(name)
        yes = False
        if command is not None and command.confirm is not False:
            yes, text = _take_yes(text)
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
            if (yes or command.only_human(args)) and origin != HUMAN:
                raise HumanOnly(action=name, origin=origin)
            delicate = command.delicate(args)
            # --devastation answers the Confirmations of Main's own Chat; one asked from a
            # Session waits for its chain's Exemption, which arrives with F2 (ADR-0013).
            answered_by_main = self._devastation.on and origin != "session"
            if delicate and not yes and not answered_by_main:
                self._ask(command, args, text, session_id, origin)
                return True
            if delicate and not yes:
                shown = f"{_summary(text)}: confirmation answered by --devastation"
                self._reporter.report(session_id, "confirmation_auto_answered", shown, command=text)
            keep_running = self._call(command, args, origin)
        except AllInError as error:
            self._report(session_id, text, error)
        else:
            self._report(session_id, text, None)
        return keep_running

    def _call(self, command: _MainCommand, args: list[str], origin: Origin) -> bool:
        if command.takes_origin:
            return command.run(*args, origin=origin)
        return command.run(*args)

    def _ask(
        self,
        command: _MainCommand,
        args: list[str],
        text: str,
        session_id: str | None,
        origin: Origin,
    ) -> None:
        """Ask before a delicate command; once answered yes it runs with the Origin it came
        with, never more."""

        def on_answer(answer: bool) -> None:
            if not answer:
                shown = f"{_summary(text)}: rejected"
                self._reporter.report(session_id, "command", shown, command=text, result="rejected")
                return
            try:
                self._call(command, args, origin)
            except AllInError as error:
                self._report(session_id, text, error)
            else:
                self._report(session_id, text, None)

        shown = f"{_summary(text)}: waiting for confirmation"
        self._reporter.report(session_id, "confirmation_asked", shown, command=text)
        self._boxes.confirm(None, _summary(text), f"Run {_summary(text)}?", on_answer)

    def _report(self, session_id: str | None, text: str, error: AllInError | None) -> None:
        result = "ok" if error is None else f"error {error}"
        shown = f"{_summary(text)}: {result}"
        self._reporter.report(session_id, "command", shown, command=text, result=result)

    def _new(self) -> bool:
        settings = self._settings.current.session  # copied: later changes never reach it
        sysprompt = render_sysprompt(self._sysprompts, settings)
        session = self._sessions.create(self._main_id, settings, sysprompt)
        self._reporter.record(session.id, "session_created", "session created", owner=self._main_id)
        self._profiles.created(session.id)
        self._windows.open(session, self._turns.streamed(session.id))
        return True

    def _open(self, session_id: str) -> bool:
        session = self._sessions.load_owned(session_id, self._main_id)
        if self._windows.is_open(session.id):
            raise WindowAlreadyOpen(session_id=session.id)
        self._profiles.opened(session.id)  # opening another Profile's Session shares it
        self._windows.open(session, self._turns.streamed(session.id))
        return True

    def _send(self, session_id: str, text: str) -> bool:
        session = self._sessions.load_owned(session_id, self._main_id)
        self._turns.push(session, Prompt(session.id, text))
        return True

    def _switch_devastation(self, state: str) -> bool:
        if state not in ("on", "off"):
            raise InvalidArguments(usage=self._commands["/devastation"].usage("/devastation"))
        self._devastation.switch(on=state == "on")
        return True

    def _set_raw(self, state: str, session_id: str | None = None) -> bool:
        if state not in ("on", "off"):
            raise InvalidArguments(usage=self._commands["/raw"].usage("/raw"))
        if session_id is not None:
            session_id = self._sessions.load_owned(session_id, self._main_id).id
        self._reporter.show_raw(state == "on", session_id)
        return True


def _take_yes(text: str) -> tuple[bool, str]:
    """Whether ``-y`` follows the command's name, and the command without it."""
    words = text.split(maxsplit=2)
    if len(words) < 2 or words[1] != "-y":
        return False, text
    return True, " ".join([words[0], *words[2:]])


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
