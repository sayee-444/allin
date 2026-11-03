"""``/call``: a human calls a Proceso adherido's method (#114, ADR-0021).

``/call <name> [<method> [key=value…]]`` lists the methods, or calls one in its own thread, so
Main goes on meanwhile; ``@call`` does the same from a Window. A value that reads as JSON is
JSON (``n=3``); else it is the text as written, which may be quoted (``text='two words'``).
What the call gives back shows in the Chat it was typed in and stays in Main's Log. A human's
``@call`` also leaves it in the Session's Log, with a one-line Notificación so its LLM does not
go on from a state that changed, which ``@conversation_logs off call`` turns off.

A method marked ``@human`` is a human's alone, and one marked ``@confirm`` asks first, which
``-y`` skips for a human only. A Session calls only the methods it has, for itself.

While it serves a call, a method runs the Command sessions its fproc declares that every Session
of the chain has, with the Proceso adherido as a link that never counts as human (ADR-0013):
neither ``-y`` nor ``--devastation`` reaches through it, and what is delicate asks first. They
run in the Session of the Window it was typed in; from Main's Chat, there is none, and they are
refused.
"""

import json
import shlex
import threading
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue

from allin.confinement.launcher import OnCommand, ToolOutcome
from allin.core.errors import (
    AllInError,
    AttachedCallsItself,
    AttachedMethodFailed,
    AttachedMethodsNotGiven,
    CommandSessionWithoutSession,
    HumanOnly,
    InvalidArguments,
)
from allin.core.ids import AttachedName, SessionId, parse_attached_name, parse_session_id
from allin.core.origin import HUMAN, Link, Origin, from_process
from allin.decorators import CONFIRM, HUMAN_ONLY
from allin.main.attached import AttachedFproc, AttachedProcesses, human_only
from allin.main.attached_methods import held_names
from allin.main.chat_rebuild import logs_entry
from allin.main.events import (
    CommandOutcome,
    MainEvent,
    MethodAnnounced,
    MethodAnswered,
    MethodProgressed,
    OnOutcome,
)
from allin.main.notifications import CALL, Notifications
from allin.main.open_windows import OpenWindows
from allin.main.reporting import Reporter
from allin.main.session_commands import IN
from allin.main.sessions import SessionStore
from allin.main.tool_steps import ask_main, refused_command

CALL_USAGE = "/call <name> [<method> [key=value…]]"
_SHOWN_RESULT = 2000  # of what a call gave back, what a Chat shows
_TOLD_RESULT = 200  # of it, what the one-line Notificación tells


def _ignored(_: CommandOutcome) -> None:
    pass


@dataclass(frozen=True)
class _Asked:
    name: AttachedName
    method: str | None
    arguments: dict[str, JsonValue]
    window: SessionId | None  # where it was typed, with ``@call``


class MethodCalls:
    """``/call`` and ``@call``: run in Main's dispatcher, each call in a thread of its own."""

    def __init__(
        self,
        attached: AttachedProcesses,
        sessions: SessionStore,
        notifications: Notifications,
        windows: OpenWindows,
        reporter: Reporter,
        emit: Callable[[MainEvent], None],
        timeout: Callable[[SessionId | None], float],
    ) -> None:
        """``emit`` takes events from a call's thread; ``timeout`` gives how long a call may
        run, by the Session it was typed in, if any."""
        self._attached = attached
        self._sessions = sessions
        self._notifications = notifications
        self._windows = windows
        self._reporter = reporter
        self._emit = emit
        self._timeout = timeout

    def delicate(self, args: list[str]) -> bool:
        """Whether ``/call``'s method asks a Confirmation first."""
        try:
            asked = _parse(" ".join(args))
            if asked.method is None:
                return False
            attached = self._attached.attached_fproc(asked.name)
            return attached.confirms(attached.method(asked.method))
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
        attached = self._attached.attached_fproc(asked.name)
        self._check_caller(asked, origin)
        if asked.method is None:
            self._list(attached, asked.window)
            answer("ok")
            return True
        method = attached.method(asked.method)
        if human_only(method) and not origin.is_human:
            raise HumanOnly(action=f"/call {asked.name} {asked.method}", origin=_labels(origin))
        timeout = self._timeout(asked.window)
        on_command = self._on_command(attached, asked, origin)

        def call() -> None:
            outcome: ToolOutcome | AllInError
            progressed = f"{attached.name}.{method.name}: "
            try:
                outcome = attached.process.call(
                    method.name,
                    asked.arguments,
                    timeout,
                    on_command,
                    on_progress=lambda text: self._emit(
                        MethodProgressed(asked.window, progressed + text)
                    ),
                    on_announce=lambda text: self._emit(
                        MethodAnnounced(attached.name, text, from_process(origin, attached.name))
                    ),
                )
            except AllInError as error:
                outcome = error
            self._emit(MethodAnswered(lambda: self._answered(asked, outcome, origin, answer)))

        threading.Thread(target=call, name=f"call-{attached.name}", daemon=True).start()
        return True

    def _check_caller(self, asked: _Asked, origin: Origin) -> None:
        """A human calls any; a Session only the methods it has, for itself; and a method never
        its own Proceso adherido, which would wait for itself."""
        if Link("process", asked.name) in origin.chain:
            raise AttachedCallsItself(name=asked.name)
        if origin.is_human:
            return
        sender = origin.sender
        if asked.window is not None and asked.window != sender:
            raise HumanOnly(action=f"/call {IN}", origin=_labels(origin))
        if sender is None or asked.name not in held_names(self._sessions.load(SessionId(sender))):
            raise AttachedMethodsNotGiven(name=asked.name, session_id=sender)

    def _on_command(self, attached: AttachedFproc, asked: _Asked, origin: Origin) -> OnCommand:
        """Where the call's Command sessions go: to Main, with the Proceso adherido after the
        caller in their Origin, run in the Session the call came through."""
        session_id = asked.window or (None if origin.sender is None else SessionId(origin.sender))
        chain = from_process(origin, attached.name)
        reach = attached.offered.description.reach
        tool = attached.tool_name(attached.method(asked.method or ""))

        def on_command(text: str) -> JsonValue:
            refused = refused_command(reach, text, tool)
            if refused is not None:
                return refused
            if session_id is None:
                return {"error": str(CommandSessionWithoutSession(command=text[:40]))}
            return ask_main(self._emit, session_id, text, chain)

        return on_command

    def _answered(
        self,
        asked: _Asked,
        outcome: ToolOutcome | AllInError,
        origin: Origin,
        answer: OnOutcome,
    ) -> None:
        """In Main's thread: tell what the call gave back where it was typed, and in Main's
        Log; a human's ``@call``, in its Session's Log too, with a Notificación."""
        called = f"{asked.name}.{asked.method}"
        failure: AllInError | None = None
        match outcome:
            case AllInError() as error:
                failure, result = error, str(error)
            case ToolOutcome(error=str() as error):
                failure = AttachedMethodFailed(name=asked.name, method=asked.method, error=error)
                result = error
            case ToolOutcome(value=value):
                result = json.dumps(value, ensure_ascii=False)
        shown = f"{called}: {'error ' if failure else ''}{_cut(result, _SHOWN_RESULT)}"
        fields: dict[str, JsonValue] = {
            "name": asked.name,
            "method": asked.method,
            "arguments": asked.arguments,
            "error" if failure else "result": result,
            "origin": _labels(origin),
        }
        self._reporter.report(asked.window, "method_called", shown, **fields)
        if asked.window is not None:
            self._windows.send(asked.window, logs_entry(shown))
            if origin.is_human:
                self._reporter.log_session(asked.window, "method_called", **fields)
                told = f"@call {called} by {origin}: {_cut(result, _TOLD_RESULT)}"
                self._notifications.leave(asked.window, CALL, " ".join(told.split()))
        answer(failure or "ok")

    def _list(self, attached: AttachedFproc, window: SessionId | None) -> None:
        lines = [f"{attached.name} ({attached.offered.description.module}):"]
        for method in attached.methods:
            marks = [HUMAN_ONLY] if human_only(method) else []
            marks += [CONFIRM] if attached.confirms(method) else []
            properties = method.parameters.get("properties")
            parameters = ", ".join(properties) if isinstance(properties, dict) else ""
            shown_marks = "".join(f" @{mark}" for mark in marks)
            lines.append(f"  {method.name}({parameters}){shown_marks}: {method.description}")
        for line in lines:
            self._reporter.show(window, line)
            if window is not None:
                self._windows.send(window, logs_entry(line))


def _parse(text: str) -> _Asked:
    """``<name> [<method> [key=value…]]``, maybe with ``--in=<sid>`` anywhere after the name."""
    try:
        first, *words = shlex.split(text)
    except ValueError as error:  # unclosed quotes, or nothing at all
        raise InvalidArguments(usage=CALL_USAGE) from error
    name = parse_attached_name(first)
    if name is None:
        raise InvalidArguments(usage=CALL_USAGE)
    window: SessionId | None = None
    rest: list[str] = []
    for word in words:
        if word.startswith(IN):
            window = parse_session_id(word.removeprefix(IN))
            if window is None:
                raise InvalidArguments(usage=CALL_USAGE)
        else:
            rest.append(word)
    arguments: dict[str, JsonValue] = {}
    for word in rest[1:]:
        key, equals, value = word.partition("=")
        if not equals or not key:
            raise InvalidArguments(usage=CALL_USAGE)
        arguments[key] = _value(value)
    return _Asked(name, rest[0] if rest else None, arguments, window)


def _value(text: str) -> JsonValue:
    try:
        value: JsonValue = json.loads(text)
    except ValueError:
        return text
    return value


def _labels(origin: Origin) -> list[JsonValue]:
    return list[JsonValue](origin.labels)


def _cut(text: str, most: int) -> str:
    return text if len(text) <= most else text[:most] + "\N{HORIZONTAL ELLIPSIS}"
