"""The methods of the Procesos adheridos (#114, ADR-0021, ADR-0012): who gets them, how the LLM
calls them as tools and how a human calls them with ``/call``.

A Session gets a Proceso adherido's methods as a block, by its name: when it attaches it, when a
human attaches it ``--to`` it, or when a human gives them with ``/func add <sid> attached:<name>``.
Anyone takes them away with ``/func remove``, and detaching the process takes them from every
Session. In ``tools=[...]`` each method is ``<name>_<method>``, since a Session may have two
Procesos adheridos of the same fproc; one marked ``@human`` never goes there.

``/call <name> [<method> [key=value…]]`` lists the methods, or calls one in its own thread, so
Main goes on meanwhile; ``@call`` does the same from a Window. What the call gives back shows in
the Chat it was typed in and stays in Main's Log; with ``@call``, also in the Session's Log, with
a one-line Notificación so its LLM does not go on from a state that changed, which
``@conversation_logs off call`` turns off.

While it serves a call, a method runs the Command sessions its fproc declares that every Session
of the chain has, with the Proceso adherido as a link that never counts as human (ADR-0013):
neither ``-y`` nor ``--devastation`` reaches through it, and what is delicate asks first.
"""

import json
import queue
import shlex
import threading
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue

from allin.confinement.launcher import ToolOutcome
from allin.core.errors import (
    AllInError,
    AttachedMethodNotFound,
    AttachedMethodsNotGiven,
    HumanOnly,
    InvalidArguments,
)
from allin.core.ids import AttachedName, SessionId, parse_attached_name
from allin.core.origin import HUMAN, Origin, from_process
from allin.decorators import CONFIRM, HUMAN_ONLY
from allin.main.attached import AttachedProcesses, Attachment
from allin.main.chat_rebuild import logs_entry
from allin.main.conversation import Item
from allin.main.descriptions import ToolSchema
from allin.main.events import (
    CommandOutcome,
    MainEvent,
    MethodAnswered,
    OnOutcome,
    SessionCommandAsked,
    SessionCommandResult,
)
from allin.main.func_commands import ATTACHED
from allin.main.notifications import CALL, FUNC, Notifications, told_line
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.session_commands import IN
from allin.main.sessions import Change, Session, SessionStore
from allin.main.tool_steps import AttachedMethod, SessionTool, refused_command

CALL_USAGE = "/call <name> [<method> [key=value…]]"
_SHOWN_RESULT = 2000  # of what a call gave back, what a Chat shows
_TOLD_RESULT = 200  # of it, what the one-line Notificación tells


def _ignored(_: CommandOutcome) -> None:
    pass


@dataclass(frozen=True)
class _Asked:
    name: str
    method: str | None
    arguments: dict[str, JsonValue]
    window: SessionId | None  # where a human typed it, with ``@call``


class AttachedMethods:
    """The methods of the active Profile's Procesos adheridos; only Main's dispatcher changes
    who has them."""

    def __init__(
        self,
        attached: AttachedProcesses,
        sessions: SessionStore,
        in_profile: Callable[[SessionId], bool],
        notifications: Notifications,
        windows: OpenWindows,
        reporter: Reporter,
        emit: Callable[[MainEvent], None],
        timeout: Callable[[SessionId | None], float],
    ) -> None:
        """``emit`` takes events from a call's thread; ``timeout`` gives how long a human's
        call may run, by the Session it was typed in, if any."""
        self._attached = attached
        self._sessions = sessions
        self._in_profile = in_profile
        self._notifications = notifications
        self._windows = windows
        self._reporter = reporter
        self._emit = emit
        self._timeout = timeout

    # Who has them

    def given(self, name: AttachedName, to: tuple[SessionId, ...]) -> None:
        """``name`` was attached for ``to``: each of them gets its methods."""
        for session_id in to:
            self._give(self._sessions.load(session_id), name)

    def taken(self, name: AttachedName) -> None:
        """``name`` was detached: no Session of the active Profile keeps its methods."""
        for session_id in self._sessions.ids():
            if self._in_profile(session_id):
                session = self._sessions.load(session_id)
                if name in session.attached:
                    self._take(session, name)

    def change(self, session: Session, named: list[str], *, given: bool, origin: Origin) -> None:
        """``/func add|remove <sid> attached:<name>…``: give a Session the methods, a human's
        alone, or take them away, anyone's (ADR-0021)."""
        names = [_name(word.removeprefix(ATTACHED)) for word in named]
        for name in names:
            if given:
                self._attached.attachment(name)  # it must be attached in this Profile
                self._give(session, name)
            elif name in session.attached:
                self._take(session, name)
            session = self._sessions.load(session.id)
        self._reporter.record(
            session.id,
            "methods_given" if given else "methods_taken",
            f"Procesos adheridos: {', '.join(session.attached) or 'none'}",
            names=list[JsonValue](names),
            origin=list[JsonValue](origin.labels),
        )

    # As tools

    def tools(self, session: Session) -> list[SessionTool]:
        """The methods its LLM gets, as tools: those of each Proceso adherido it has that is
        attached, or crashed, whose calls fail; never one marked ``@human``."""
        tools: list[SessionTool] = []
        for name in session.attached:
            try:
                attachment = self._attached.attachment(name)
            except AllInError:
                continue  # detached meanwhile, or another Profile's
            tools.extend(
                _tool(attachment, method)
                for method in _methods(attachment)
                if method.marks.get(HUMAN_ONLY) is not True
            )
        return tools

    # /call

    def delicate(self, args: list[str]) -> bool:
        """Whether ``/call``'s method asks a Confirmation first: ``@confirm``, or its
        Approval."""
        try:
            asked = _parse(" ".join(args))
            if asked.method is None:
                return False
            attachment = self._attached.attachment(asked.name)
            return _confirms(attachment, _method(attachment, asked.method))
        except AllInError:
            return False  # running it raises why

    def session_of(self, args: list[str]) -> str | None:
        """The Session whose Window ``@call`` was typed in."""
        try:
            return _parse(" ".join(args)).window
        except AllInError:
            return None

    def run(self, text: str = "", *, origin: Origin = HUMAN, answer: OnOutcome = _ignored) -> bool:
        """``/call``: list a Proceso adherido's methods, or call one in its own thread;
        ``answer`` hears how it went once it did."""
        asked = _parse(text)
        attachment = self._attached.attachment(asked.name)
        self._check_caller(asked, origin)
        if asked.method is None:
            self._list(attachment, asked.window)
            answer("ok")
            return True
        schema = _method(attachment, asked.method)
        if schema.marks.get(HUMAN_ONLY) is True and not origin.is_human:
            raise HumanOnly(action=f"/call {asked.name} {asked.method}", origin=_labels(origin))
        timeout = self._timeout(asked.window)
        on_command = self._on_command(attachment, asked, origin)

        def call() -> None:
            outcome: ToolOutcome | AllInError
            try:
                outcome = attachment.process.call(schema.name, asked.arguments, timeout, on_command)
            except AllInError as error:
                outcome = error
            self._emit(MethodAnswered(lambda: self._answered(asked, outcome, origin, answer)))

        threading.Thread(target=call, name=f"call-{attachment.name}", daemon=True).start()
        return True

    def _check_caller(self, asked: _Asked, origin: Origin) -> None:
        """A human calls any; a Session only the methods it has, and only for itself."""
        if origin.is_human:
            return
        sender = origin.sender
        if asked.window is not None and asked.window != sender:
            raise HumanOnly(action=f"/call {IN}", origin=_labels(origin))
        if sender is None or asked.name not in self._sessions.load(SessionId(sender)).attached:
            raise AttachedMethodsNotGiven(name=asked.name, session_id=sender)

    def _on_command(
        self, attachment: Attachment, asked: _Asked, origin: Origin
    ) -> Callable[[str], JsonValue]:
        """Where the call's Command sessions go: to Main, with the Proceso adherido after the
        caller in their Origin, as if typed in the Session the call came through."""
        session_id = asked.window or (None if origin.sender is None else SessionId(origin.sender))
        chain = from_process(origin, attachment.name)
        tool = f"{attachment.name}_{asked.method}"
        reach = attachment.offered.description.reach

        def on_command(text: str) -> JsonValue:
            refused = refused_command(reach, text, tool)
            if refused is not None:
                return refused
            if session_id is None:
                return {"error": "a Command session runs in a Session: call it with @call"}
            answers: queue.Queue[SessionCommandResult] = queue.Queue()

            def reply(result: SessionCommandResult) -> bool:
                answers.put(result)
                return True

            self._emit(SessionCommandAsked(session_id, text, chain, reply))
            return answers.get().as_json()

        return on_command

    def _answered(
        self,
        asked: _Asked,
        outcome: ToolOutcome | AllInError,
        origin: Origin,
        answer: OnOutcome,
    ) -> None:
        """In Main's thread: tell what the call gave back where it was typed, and in Main's
        Log; with ``@call``, in its Session's Log too, with a Notificación."""
        called = f"{asked.name}.{asked.method}"
        match outcome:
            case AllInError() as error:
                result, failed = str(error), True
            case ToolOutcome(error=str() as error):
                result, failed = error, True
            case ToolOutcome(value=value):
                result, failed = json.dumps(value, ensure_ascii=False), False
        shown = f"{called}: {'error ' if failed else ''}{_cut(result, _SHOWN_RESULT)}"
        fields: dict[str, JsonValue] = {
            "name": asked.name,
            "method": asked.method,
            "arguments": asked.arguments,
            "result" if not failed else "error": result,
            "origin": _labels(origin),
        }
        self._reporter.report(asked.window, "method_called", shown, **fields)
        if asked.window is not None:
            self._reporter.log_session(asked.window, "method_called", **fields)
            self._windows.send(asked.window, logs_entry(shown))
            told = f"@call {called} by {origin}: {_cut(result, _TOLD_RESULT)}"
            self._notifications.leave(asked.window, CALL, " ".join(told.split()))
        answer(outcome if isinstance(outcome, AllInError) else "ok")

    def _list(self, attachment: Attachment, window: SessionId | None) -> None:
        lines = [f"{attachment.name} ({attachment.offered.description.module}):"]
        for method in _methods(attachment):
            marks = [m for m in (HUMAN_ONLY, CONFIRM) if method.marks.get(m) is True]
            if method.name in attachment.offered.confirm and CONFIRM not in marks:
                marks.append(CONFIRM)
            shown_marks = "".join(f" @{mark}" for mark in marks)
            parameters = ", ".join(_parameters(method))
            lines.append(f"  {method.name}({parameters}){shown_marks}: {method.description}")
        for line in lines:
            self._reporter.show(window, line)
            if window is not None:
                self._windows.send(window, logs_entry(line))

    def _give(self, session: Session, name: AttachedName) -> None:
        if name in session.attached:
            return
        self._sessions.save_attached(session, (*session.attached, name))
        self._tell(session, name, given=True)

    def _take(self, session: Session, name: AttachedName) -> None:
        self._sessions.save_attached(session, tuple(n for n in session.attached if n != name))
        self._tell(session, name, given=False)

    def _tell(self, session: Session, name: AttachedName, *, given: bool) -> None:
        change = Change(subject=f"{ATTACHED}{name}", given=given)
        self._notifications.leave(session.id, FUNC, told_line(change), change)


def _parse(text: str) -> _Asked:
    """``<name> [<method> [key=value…]]``, maybe with ``--in=<sid>`` anywhere after the name.
    A value is JSON when it reads as JSON (``n=3``, ``ok=true``), else the text as written,
    which may be quoted (``text='two words'``)."""
    try:
        name, *words = shlex.split(text)
    except ValueError as error:  # unclosed quotes, or nothing at all
        raise InvalidArguments(usage=CALL_USAGE) from error
    window: SessionId | None = None
    rest: list[str] = []
    for word in words:
        if word.startswith(IN):
            window = SessionId(word.removeprefix(IN))
        else:
            rest.append(word)
    method = rest[0] if rest else None
    arguments: dict[str, JsonValue] = {}
    for word in rest[1:]:
        key, equals, value = word.partition("=")
        if not equals or not key:
            raise InvalidArguments(usage=CALL_USAGE)
        arguments[key] = _value(value)
    return _Asked(name, method, arguments, window)


def _value(text: str) -> JsonValue:
    try:
        value: JsonValue = json.loads(text)
    except ValueError:
        return text
    return value


def _methods(attachment: Attachment) -> tuple[ToolSchema, ...]:
    return attachment.offered.description.tools


def _method(attachment: Attachment, method: str) -> ToolSchema:
    for schema in _methods(attachment):
        if schema.name == method:
            return schema
    raise AttachedMethodNotFound(name=attachment.name, method=method)


def _confirms(attachment: Attachment, method: ToolSchema) -> bool:
    return method.name in attachment.offered.confirm or method.marks.get(CONFIRM) is True


def _tool(attachment: Attachment, method: ToolSchema) -> SessionTool:
    name = f"{attachment.name}_{method.name}"
    schema: Item = {
        "type": "function",
        "name": name,
        "description": method.description,
        "parameters": method.parameters,
    }
    process = attachment.process
    described = attachment.offered.description
    return SessionTool(
        name,
        attachment.offered.package,
        described.module,
        described.reach,
        schema,
        _confirms(attachment, method),
        AttachedMethod(
            attachment.name,
            lambda arguments, timeout, stop, on_command: process.call(
                method.name, arguments, timeout, on_command, stop
            ),
        ),
    )


def _parameters(method: ToolSchema) -> list[str]:
    properties = method.parameters.get("properties")
    return list(properties) if isinstance(properties, dict) else []


def _name(text: str) -> AttachedName:
    name = parse_attached_name(text)
    if name is None:
        raise InvalidArguments(usage=f"{ATTACHED}<name>")
    return name


def _labels(origin: Origin) -> list[JsonValue]:
    return list[JsonValue](origin.labels)


def _cut(text: str, most: int) -> str:
    return text if len(text) <= most else text[:most] + "\N{HORIZONTAL ELLIPSIS}"
