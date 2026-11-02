"""The Command mains: what each one is called, the arguments it takes and what it does."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic import JsonValue

from allin.core.errors import (
    AllInError,
    HumanOnly,
    InvalidArguments,
    SessionNotOpen,
    TurnActive,
    UnknownCommand,
    WindowAlreadyOpen,
    WindowLaunchFailed,
)
from allin.core.ids import MainId, SessionId, new_prompt_id
from allin.core.origin import HUMAN, Origin
from allin.ipc.messages import OrganizerRow
from allin.main.attachments import attach
from allin.main.boxes import MainBoxes
from allin.main.chat_rebuild import error_entry
from allin.main.devastation import Devastation
from allin.main.func_commands import USAGE as FUNC_USAGE
from allin.main.func_commands import FuncCommands
from allin.main.func_commands import only_human as func_only_human
from allin.main.func_commands import session_of as func_session_of
from allin.main.key_commands import USAGE as KEY_USAGE
from allin.main.key_commands import KeyCommands
from allin.main.kit_commands import USAGE as KIT_USAGE
from allin.main.kit_commands import KitCommands
from allin.main.open_windows import OpenWindows
from allin.main.organizer import Organizer
from allin.main.piece_commands import USAGE as PIECE_USAGE
from allin.main.piece_commands import PieceCommands
from allin.main.profile_commands import USAGE as PROFILE_USAGE
from allin.main.profile_commands import ProfileCommands
from allin.main.prompt_commands import parse_prompt
from allin.main.prompts import Prompt, origin_header
from allin.main.reporting import Reporter
from allin.main.rewind import rewind
from allin.main.server_commands import USAGE as SERVER_USAGE
from allin.main.server_commands import ServerCommands
from allin.main.server_commands import delicate as server_delicate
from allin.main.server_commands import human_only as server_human_only
from allin.main.sessions import Session, SessionStore
from allin.main.settings_commands import (
    SESSION_USAGE,
    SettingsCommands,
    parse_session_command,
)
from allin.main.sysprompt import render_sysprompt
from allin.main.turn_queue import TurnQueue
from allin.ui.organizer_view import windows_list

_ID_OPTION = "--id="  # /new --id=<id> (#59)
_BG_OPTION = "--bg"  # /new and /open in the background (#52)
_NO_SOURCES = "-sf=off"  # /new without the Profile's fsources (#106)
_AGENTS_OPTION = "-fa="  # /new with these fagents (#106)
_AGENTS_USAGE = f"{_AGENTS_OPTION}<packages>"
_COMMANDS_OPTION = "-cs="  # /new with these Command sessions instead of the root's (#108)
_COMMANDS_USAGE = f"{_COMMANDS_OPTION}<names>"
CLOSED = "closed with /close"

# Whether a command, by its arguments, is delicate or only a human's.
type ByArguments = Callable[[list[str]], bool]


@dataclass(frozen=True)
class CommandSpec:
    """A Command main as registered: how it is called, what ``/help`` says of it and what it
    runs."""

    params: tuple[str, ...]
    run: Callable[..., bool]  # takes one str per param; returns whether Main keeps running
    summary: str  # one line for /help: every Command main declares it (#23)
    args: dict[str, str] = field(default_factory=dict[str, str])  # param -> its line in /help
    usage_text: str | None = None  # for one with actions, its usage; else built from params
    rest: bool = False  # the last param takes the rest of the line verbatim, newlines included
    optional: tuple[str, ...] = ()  # params after ``params`` that may be left out
    # The Session it concerns, from its arguments; by default its ``sid`` param.
    session_of: Callable[[list[str]], str | None] | None = None
    # Delicate: asks a Confirmation first, which ``-y`` skips (ADR-0009).
    confirm: bool | ByArguments = False
    human_only: bool | ByArguments = False  # any other Origin is an error (ADR-0013)
    takes_origin: bool = False  # ``run`` also gets the Origin, as ``origin=``
    # ``-y`` reaches ``run`` as ``yes=``, for what it starts later: /send's Turn (#107).
    takes_yes: bool = False

    def delicate(self, args: list[str]) -> bool:
        return self.confirm(args) if callable(self.confirm) else self.confirm

    def only_human(self, args: list[str]) -> bool:
        return self.human_only(args) if callable(self.human_only) else self.human_only

    def usage(self, name: str) -> str:
        if self.usage_text is not None:
            return self.usage_text
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
        keys: KeyCommands,
        pieces: PieceCommands,
        functions: FuncCommands,
    ) -> None:
        self._functions = functions
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
        self._organizer: Organizer | None = None
        self._organizer_rows: Callable[[], list[OrganizerRow]] = list
        # Command mains are registered once, when Main starts.
        self._commands: dict[str, CommandSpec] = {
            "/new": CommandSpec(
                (),
                self._new,
                "create a Session from the template and open it",
                args={
                    "--id=<id>": "its id, 4 to 12 characters of a-z and 0-9; else a random one",
                    _BG_OPTION: "open it in the background, without its Window",
                    _NO_SOURCES: "without the Profile's fsources",
                    _AGENTS_USAGE: "the fagents it gets, by package, comma separated",
                    _COMMANDS_USAGE: "its Command sessions instead of the root's, comma separated",
                },
                optional=("--id=<id>", _BG_OPTION, _NO_SOURCES, _AGENTS_USAGE, _COMMANDS_USAGE),
                takes_origin=True,
            ),
            "/open": CommandSpec(
                ("sid",),
                self._open,
                "open the Window of a Session of this Main, or bring it from the background",
                args={"sid": "the Session's id", _BG_OPTION: "open it in the background"},
                optional=(_BG_OPTION,),
                takes_origin=True,
            ),
            "/bg": CommandSpec(
                ("sid",),
                self._background,
                "send a Session to the background: its Window closes, its Turns go on",
                args={"sid": "the Session's id"},
                human_only=True,
            ),
            "/windows": CommandSpec(
                (),
                self._organize,
                "open the Organizer: the open Sessions, to bring, send to the background or close",
                args={"list": "write its list in Main's Chat instead"},
                optional=("list",),
                human_only=lambda args: not args,  # it is a screen for the human (ADR-0020)
            ),
            # Like the Window's X: cancels a running Turn, so only a human closes (ADR-0020).
            "/close": CommandSpec(
                ("sid",),
                self._close,
                "close a Session's Window, in view or in the background, as its X does",
                args={"sid": "the Session's id"},
                human_only=True,
            ),
            # The only Command main that queues Prompts; Enter in a Window is /send to its own.
            "/send": CommandSpec(
                ("sid", "text"),
                self._send,
                "queue a Prompt for a Session's next Turn",
                args={
                    "sid": "the Session's id",
                    "text": "the Prompt, up to the end; @key=value and @./file go first",
                },
                usage_text="/send [-y] <sid> <text>",
                rest=True,
                takes_origin=True,
                takes_yes=True,
            ),
            "/raw": CommandSpec(
                ("state",),
                self._set_raw,
                "show each event's raw JSON in the Chat, or stop",
                args={"state": "on or off", "sid": "one Session's Window; else Main's Chat"},
                optional=("sid",),
            ),
            "/set": CommandSpec(
                (),
                settings.set,
                "list Main's Settings, or change them",
                args={"key=value": "one or more Settings to change"},
                rest=True,
                optional=("key=value",),
                takes_origin=True,
            ),
            # Delicate: the current Settings are lost unless they were also the defaults.
            "/reset": CommandSpec(
                (), settings.reset, "put Main's Settings back to the defaults", confirm=True
            ),
            "/session": CommandSpec(
                ("sid",),
                self._session,
                "list a Session's Settings, change them, compact its Conversation or rename it",
                usage_text=SESSION_USAGE,
                rest=True,
                optional=("key=value",),
                session_of=_session_of_session_command,
                takes_origin=True,
                # Only a human changes a Session's id (#59) or points its Carpeta de trabajo
                # out of AllIn's data folder, which is an Approval (ADR-0013).
                human_only=lambda args: args[:1] in (["id"], ["workspace"]),
            ),
            # Only a Command main: no Session undoes its own Prompts (GLOSSARY, Rewind).
            "/rewind": CommandSpec(
                ("sid",),
                self._rewind,
                "undo a Session's last Prompts; a human's go back to its input",
                args={"sid": "the Session's id", "n": "how many Prompts, 1 if left out"},
                optional=("n",),
            ),
            "/devastation": CommandSpec(
                ("state",),
                self._switch_devastation,
                "let Main answer the Confirmations of its own Chat by itself",
                args={"state": "on or off"},
                human_only=True,
            ),
            "/server": CommandSpec(
                (),
                server.run,
                "list the Servers, or add, change, restart, adopt, sleep or wake one",
                usage_text=SERVER_USAGE,
                rest=True,
                optional=("action…",),
                takes_origin=True,
                confirm=server_delicate,
                human_only=server_human_only,
            ),
            "/profile": CommandSpec(
                (),
                profiles.run,
                "list the Profiles, or create, switch, share, export or import one",
                usage_text=PROFILE_USAGE,
                rest=True,
                optional=("action…",),
            ),
            # A Kit writes pieces into the Profile and may suggest Supervisors (ADR-0014).
            "/kit": CommandSpec(
                (),
                kits.run,
                "export pieces of the Profile as a Kit, or load one",
                usage_text=KIT_USAGE,
                rest=True,
                optional=("action…",),
                human_only=True,
            ),
            # Only a human widens what a Session's LLM may call (ADR-0013).
            "/func": CommandSpec(
                (),
                functions.run,
                "list the Function tools of the Profile or of a Session, or give or take them",
                usage_text=FUNC_USAGE,
                rest=True,
                optional=("action…",),
                human_only=func_only_human,
                session_of=func_session_of,
            ),
            # Only a human approves, on purpose: never delicate, so neither -y nor
            # --devastation answers for it (ADR-0013).
            "/piece": CommandSpec(
                (),
                pieces.run,
                "list the Profile's pieces, show one's approval screen or approve it",
                usage_text=PIECE_USAGE,
                rest=True,
                optional=("action…",),
                human_only=True,
            ),
            # Only a human changes which key does what (#57, ADR-0013).
            "/key": CommandSpec(
                (),
                keys.run,
                "list the keyboard Shortcuts, or bind a key",
                usage_text=KEY_USAGE,
                rest=True,
                optional=("key…",),
                human_only=True,
            ),
            # Built from this registry, so a new Command main shows by itself (#23).
            "/help": CommandSpec(
                (),
                self._help,
                "list the Command mains, or show how one is used",
                args={"command": "a Command main, with or without its slash"},
                optional=("command",),
                human_only=True,
            ),
            "/exit": CommandSpec((), lambda: False, "close Main; Sessions and Windows stay saved"),
        }

    def use_sysprompts(self, sysprompts: Path) -> None:
        """The active Profile's, which Sessions are born with, once Main switched to it."""
        self._sysprompts = sysprompts

    def help_entries(self) -> dict[str, CommandSpec]:
        """Every Command main registered, in the order /help lists them."""
        return dict(self._commands)

    def _help(self, asked: str | None = None) -> bool:
        if asked is None:
            width = max(map(len, self._commands))
            for name, command in self._commands.items():
                self._reporter.show(None, f"{name:<{width}}  {command.summary}")
            return True
        name = asked if asked.startswith("/") else f"/{asked}"
        command = self._commands.get(name)
        if command is None:
            raise UnknownCommand(command=name)
        self._reporter.show(None, f"{name}: {command.summary}")
        self._reporter.show(None, f"usage: {command.usage(name)}")
        for param in (*command.params, *command.optional):
            if param in command.args:
                self._reporter.show(None, f"  <{param}>: {command.args[param]}")
        return True

    def run(
        self, text: str, origin: Origin = HUMAN, answer: Callable[[str], None] | None = None
    ) -> bool:
        """Run a Command main and report how it went; return whether Main keeps running.

        A delicate one first asks a Confirmation, unless ``-y`` (only from a human) or
        ``--devastation`` answers it; it then runs when the answer comes. ``answer`` gets how
        it went once it did: ``ok``, ``error …`` or ``rejected`` (for a tool, ADR-0025).
        """
        answer = answer or _ignored
        name = (text.split(maxsplit=1) or [""])[0]
        command = self._commands.get(name)
        yes = False
        if command is not None and (command.confirm is not False or command.takes_yes):
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
            if (yes or command.only_human(args)) and not origin.is_human:
                raise HumanOnly(action=name, origin=list[JsonValue](origin.labels))
            delicate = command.delicate(args)
            answered_by_main = self._devastation.answers(origin)
            if delicate and not yes and not answered_by_main:
                self._ask(command, args, text, session_id, origin, answer)
                return True
            if delicate and not yes:
                shown = f"{_summary(text)}: confirmation answered by --devastation"
                self._reporter.report(session_id, "confirmation_auto_answered", shown, command=text)
            keep_running = self._call(command, args, origin, yes)
        except AllInError as error:
            self._report(session_id, text, error, origin)
            answer(f"error {error}")
        else:
            self._report(session_id, text, None, origin)
            answer("ok")
        return keep_running

    def _call(self, command: CommandSpec, args: list[str], origin: Origin, yes: bool) -> bool:
        options: dict[str, object] = {}
        if command.takes_origin:
            options["origin"] = origin
        if command.takes_yes:
            options["yes"] = yes
        return command.run(*args, **options)

    def _ask(
        self,
        command: CommandSpec,
        args: list[str],
        text: str,
        session_id: str | None,
        origin: Origin,
        answer: Callable[[str], None],
    ) -> None:
        """Ask before a delicate command; once answered yes it runs with the Origin it came
        with, never more."""

        def on_answer(yes: bool) -> None:
            if not yes:
                self._log_command(session_id, "command", text, "rejected", origin)
                answer("rejected")
                return
            try:
                self._call(command, args, origin, yes=False)
            except AllInError as error:
                self._report(session_id, text, error, origin)
                answer(f"error {error}")
            else:
                self._report(session_id, text, None, origin)
                answer("ok")

        self._log_command(session_id, "confirmation_asked", text, None, origin)
        self._boxes.confirm(None, _summary(text), f"Run {_summary(text)}?", on_answer)

    def _report(
        self, session_id: str | None, text: str, error: AllInError | None, origin: Origin
    ) -> None:
        result = "ok" if error is None else f"error {error}"
        self._log_command(session_id, "command", text, result, origin)

    def _log_command(
        self, session_id: str | None, event: str, text: str, result: str | None, origin: Origin
    ) -> None:
        """Main's Chat and Log, with the whole chain; and, when a Session's LLM sent it, that
        Session's Log too, where its LLM will read how it went (ADR-0013)."""
        shown = f"{_summary(text)}: {result or 'waiting for confirmation'}"
        fields: dict[str, JsonValue] = {"command": text}
        if result is not None:
            fields["result"] = result
        fields["origin"] = list[JsonValue](origin.labels)
        self._reporter.report(session_id, event, shown, **fields)
        sender = origin.sender
        if sender is not None and SessionId(sender) in self._sessions.ids():  # not one gone
            self._reporter.log_session(SessionId(sender), event, **fields)

    def _new(self, *options: str, origin: Origin = HUMAN) -> bool:
        chosen, background, sources, agents = None, False, True, list[str]()
        commands: list[str] | None = None
        for option in options:
            if option == _BG_OPTION and not background:
                background = True
            elif option.startswith(_ID_OPTION) and chosen is None:
                chosen = option.removeprefix(_ID_OPTION)
            elif option == _NO_SOURCES and sources:
                sources = False
            elif option.startswith(_AGENTS_OPTION) and not agents:
                agents = [a for a in option.removeprefix(_AGENTS_OPTION).split(",") if a]
            elif option.startswith(_COMMANDS_OPTION) and commands is None:
                commands = [c for c in option.removeprefix(_COMMANDS_OPTION).split(",") if c]
            else:
                raise InvalidArguments(usage=self._commands["/new"].usage("/new"))
        settings = self._settings.current.session  # copied: later changes never reach it
        sysprompt = render_sysprompt(self._sysprompts, settings)
        functions = self._functions.born_with(sources, agents)
        listed = self._functions.commands_born_with(commands, origin)
        session = self._sessions.create(
            self._main_id, settings, sysprompt, chosen, functions, listed
        )
        self._reporter.record(session.id, "session_created", "session created", owner=self._main_id)
        self._profiles.created(session.id)
        self._place(session, background, origin)
        return True

    def _place(self, session: Session, background: bool, origin: Origin) -> None:
        """Open a Session in view, under the Máximo de Ventanas, or in the background."""
        if background:
            self._windows.to_background(session.id)
        else:
            self._windows.show(session, self._turns.streamed(session.id), origin.is_human)

    def _session(self, first: str, rest: str = "", origin: Origin = HUMAN) -> bool:
        target = parse_session_command([first, rest] if rest else [first])
        if target is not None and target.rename:
            return self._rename(target.session_id, target.rest)
        return self._settings.session(first, rest, origin=origin)

    def _rename(self, session_id: str, new_id: str) -> bool:
        """``/session id <sid> <new>`` (#59): the Session takes a new id everywhere Main keeps
        it, and its old one stays as an alias. Never with a Turn under way."""
        session = self._sessions.load_owned(session_id, self._main_id)
        if self._turns.busy(session.id):
            raise TurnActive(
                session_id=session.id,
                advice="interrupt it with Shift+Enter in its Window, or wait for it to end",
            )
        old = session.id
        renamed = self._sessions.rename(session, new_id)  # checks the new id first
        was_open = self._windows.is_open(old)
        if was_open:
            self._windows.close(old, f"renamed to {renamed.id}")
        self._windows.rename(old, renamed.id)
        self._profiles.renamed(old, renamed.id)
        shown = f"renamed from {old}; {old} still reaches it"
        self._reporter.record(renamed.id, "session_renamed", shown, old=old)
        if was_open:  # under its new id, which its title shows
            self._windows.open(renamed, self._turns.streamed(renamed.id))
        return True

    def _open(self, session_id: str, option: str | None = None, origin: Origin = HUMAN) -> bool:
        if option not in (None, _BG_OPTION):
            raise InvalidArguments(usage=self._commands["/open"].usage("/open"))
        session = self._sessions.load_owned(session_id, self._main_id)
        background = option == _BG_OPTION
        if self._windows.is_open(session.id) and not background:
            raise WindowAlreadyOpen(session_id=session.id)
        self._profiles.opened(session.id)  # opening another Profile's Session shares it
        self._place(session, background, origin)
        return True

    def use_organizer(self, organizer: Organizer, rows: Callable[[], list[OrganizerRow]]) -> None:
        """What ``/windows`` opens and lists (#53)."""
        self._organizer, self._organizer_rows = organizer, rows

    def _organize(self, option: str | None = None) -> bool:
        if option is None:
            if self._organizer is None:
                raise WindowLaunchFailed(session_id="organizer", reason="no Organizer here")
            self._organizer.open()
            return True
        if option != "list":
            raise InvalidArguments(usage=self._commands["/windows"].usage("/windows"))
        for line in windows_list(self._organizer_rows()):
            self._reporter.show(None, line)
        return True

    def _background(self, session_id: str) -> bool:
        """``/bg``: the Session's xterm closes and nothing else changes (ADR-0020)."""
        session = self._sessions.load_owned(session_id, self._main_id)
        if not (self._windows.is_open(session.id) or self._windows.is_background(session.id)):
            raise SessionNotOpen(session_id=session.id)
        self._windows.to_background(session.id)
        return True

    def _close(self, session_id: str) -> bool:
        """``/close``: as the Window's X, its running Turn and queue are cancelled, also in
        the background (ADR-0013, ADR-0020)."""
        session = self._sessions.load_owned(session_id, self._main_id)
        if self._windows.is_open(session.id):
            self._windows.close(session.id, CLOSED)
        elif self._windows.is_background(session.id):
            self._windows.forget(session.id)
            self._reporter.record(session.id, "window_closed", f"window closed: {CLOSED}")
        else:
            raise SessionNotOpen(session_id=session.id)
        self._turns.cancel(session.id, "window closed")
        return True

    def _send(self, session_id: str, text: str, origin: Origin = HUMAN, yes: bool = False) -> bool:
        """``yes``: ``-y``, a human's only: its Turn's Confirmations are answered yes."""
        session = self._sessions.load_owned(session_id, self._main_id)
        if not origin.is_human:  # data: @… and /… in it are never run (ADR-0013)
            sent = f"{origin_header(origin)}\n{text}"
            self._turns.push(session, Prompt(session.id, sent, origin=origin))
            return True
        try:
            prompt = self._human_prompt(session.id, text)
        except AllInError as error:
            # It was not sent: its Window says why, and the text is the human's again.
            self._windows.send(session.id, error_entry(str(error)))
            self._windows.put_back(session.id, text)
            raise
        self._turns.push(session, replace(prompt, answered_by="-y" if yes else None))
        return True

    def _human_prompt(self, session_id: SessionId, text: str) -> Prompt:
        """A human's Prompt with its Command prompts done (#14): its overrides taken out and
        its files copied to the Carpeta de trabajo, which the text then names."""
        parsed = parse_prompt(text)
        prompt_id = new_prompt_id() if parsed.references else ""  # names its attachments
        workspace = self._sessions.workspace(session_id)
        names, attachments = attach(parsed.references, workspace, prompt_id, Path.cwd())
        sent = parsed.text_with(names)
        return Prompt(
            session_id,
            sent,
            prompt_id=prompt_id,
            attachments=tuple(attachments),
            overrides=parsed.overrides,
            typed=text if text != sent else "",
        )

    def _rewind(self, session_id: str, count: str = "1") -> bool:
        """Undo the Session's last ``count`` Prompts (#58): never with a Turn under way, and
        never past its start."""
        if not count.isdigit():
            raise InvalidArguments(usage=self._commands["/rewind"].usage("/rewind"))
        n = int(count)
        session = self._sessions.load_owned(session_id, self._main_id)
        if n == 0:
            return True
        if self._turns.busy(session.id):
            raise TurnActive(
                session_id=session.id,
                advice="interrupt it with Shift+Enter in its Window, or wait for it to end",
            )
        conversation = self._sessions.load_conversation(session)
        if conversation is None or not conversation.started:
            self._reporter.show(session.id, "nothing to rewind: the session has no prompts yet")
            return True
        result = rewind(conversation, self._sessions.load_archived(session), n)
        self._sessions.save_conversation(session, result.conversation)
        undone = len(result.undone)
        shown = f"rewound {undone} prompt(s)"
        if undone < n:
            shown += f": only {undone} to undo, back at the start of the session"
        texts: list[JsonValue] = [turn.as_typed for turn in result.undone]
        origins: list[JsonValue] = [list[JsonValue](turn.origin.labels) for turn in result.undone]
        self._reporter.record(
            session.id, "rewound", shown, turn=result.turn, asked=n, texts=texts, origins=origins
        )
        # A human's texts go back to its input, the first one undone shown first; anyone
        # else's stay in the Log only.
        for turn in reversed(result.undone):
            if turn.origin.is_human:
                self._windows.put_back(session.id, turn.as_typed)
        self._windows.rebuild_chat(session)
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


def _ignored(_: str) -> None:
    pass


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
