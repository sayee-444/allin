"""Function tools in a Turn (ADR-0004, ADR-0005, #106): what a Session's LLM gets in
``tools=[...]``, and how the calls of a Step run before its next one.

A Step that calls tools hands them to its ``ToolGate``, in the Turn's thread. The gate tells
Main, which gives the Turn's slot back while they run, so other Sessions use the Server; each
call runs in its own confined process (#100) with the Alcance its description read and its
Approval kept; and what each one gives back, or the error it raised, goes back to the LLM as
data. Once they all answered, the next Step is a Continuation: it waits for a slot ahead of
new Prompts, and the gate lets the Turn go on only when Main says it holds one.
"""

import json
import queue
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue, TypeAdapter, ValidationError

from allin.confinement.launcher import OnCommand, ToolCall, ToolLauncher, ToolOutcome
from allin.core.errors import (
    AllInError,
    SessionCommandNotDeclared,
    ToolCancelled,
    ToolNotStarted,
)
from allin.core.ids import NO_PROMPT_ID, AttachedName, PieceId, PromptId, SessionId
from allin.core.origin import Origin, from_process
from allin.decorators import CONFIRM, Reach
from allin.main.confirmation import ConfirmationGate
from allin.main.conversation import TOOL_ERROR, Item, Step
from allin.main.events import (
    MainEvent,
    MethodAnnounced,
    SessionCommandAsked,
    SessionCommandResult,
    ToolsEnded,
    ToolsStarted,
)
from allin.main.prompt_commands import session_command_name
from allin.main.quarantine import OfferedFunction
from allin.main.turn import Cancellation

_POLL = 0.1  # how often a Turn waiting for its slot looks whether it was cancelled
_SHOWN_ARGUMENTS = 600  # of a call's arguments, what its Confirmation shows
# What the LLM reads of a call a human said no to: it did not run (ADR-0009).
REFUSED = "the user refused this call: it did not run"

type Emit = Callable[[MainEvent], None]


@dataclass(frozen=True)
class CallFrame:
    """What a method's call knows of the Turn it runs in (#116): its Prompt's id, and where
    what the method announces goes."""

    prompt_id: PromptId
    on_announce: Callable[[str], None]


# Calls a Proceso adherido's method with its arguments, its timeout, what cancels the call,
# where its Command sessions go and what it knows of its Turn.
type MethodCall = Callable[
    [dict[str, JsonValue], float, Callable[[], bool], OnCommand, CallFrame], ToolOutcome
]
# What a method of a Proceso adherido announced during a Turn: the process and the text.
type Announced = Callable[[AttachedName, str], None]


def _no_announce(process: AttachedName, text: str) -> None:
    pass


@dataclass(frozen=True)
class AttachedMethod:
    """A tool that is a Proceso adherido's method (#114): its process runs it, not a new one."""

    process: AttachedName  # a link of its Command sessions' Origin
    call: MethodCall


@dataclass(frozen=True)
class SessionTool:
    """One Function tool a Session's LLM may call, as Main runs it."""

    name: str
    package: Path
    module: str
    reach: Reach  # as its description read it, or as its Approval reduced it
    schema: Item  # as ``tools=[...]`` takes it
    confirm: bool = False  # it asks a Confirmation before each call: ``@confirm`` or its Approval
    method: AttachedMethod | None = None  # an fproc's method, which its Proceso adherido runs


def session_tools(
    offered: Iterable[OfferedFunction], functions: Iterable[PieceId]
) -> list[SessionTool]:
    """The tools of the approved pieces among a Session's ``functions``, but one whose name
    another tool took first."""
    wanted = set(functions)
    tools: dict[str, SessionTool] = {}
    for function in offered:
        described = function.description
        if function.piece.id not in wanted or described.kind != "function":
            continue  # an fproc's methods arrive with the Procesos adheridos (#113)
        for tool in described.tools:
            if tool.name in tools:
                continue
            schema: Item = {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            confirm = tool.name in function.confirm or tool.marks.get(CONFIRM) is True
            tools[tool.name] = SessionTool(
                tool.name, function.package, described.module, described.reach, schema, confirm
            )
    return list(tools.values())


# Runs a Command session a tool asked for, with its Turn's Origin, and gives back how it went;
# the second argument is the Proceso adherido whose method asked, a link of its Origin.
type AskMain = Callable[[str, AttachedName | None], JsonValue]


def _no_main(text: str, _: AttachedName | None) -> JsonValue:
    return {"error": f"no Main to run {text[:40]!r}"}


def refused_command(reach: Reach, text: str, tool: str) -> JsonValue | None:
    """Why Main does not run ``text`` for ``tool``: only the Command sessions its function, or
    its method's fproc, declares in its Alcance run (ADR-0025)."""
    name = session_command_name(text)
    if name is None or name not in reach.commands:
        refused = SessionCommandNotDeclared(command=name or text[:40], tool=tool)
        return {"error": str(refused)}
    return None


def _declared(tool: SessionTool, text: str, ask_main: AskMain) -> JsonValue:
    refused = refused_command(tool.reach, text, tool.name)
    if refused is not None:
        return refused
    return ask_main(text, None if tool.method is None else tool.method.process)


class SessionTools:
    """A Session's tools for one Turn, and how to run a call to one of them."""

    def __init__(
        self,
        tools: list[SessionTool],
        launcher: ToolLauncher,
        workspace: Path,
        timeout: float,
    ) -> None:
        """``timeout``: seconds each call may run (the Session's ``tools.timeout``)."""
        self._tools = {tool.name: tool for tool in tools}
        self._launcher = launcher
        self._workspace = workspace
        self._timeout = timeout
        self._prompt_id = NO_PROMPT_ID  # of the call that runs, as ``run`` was given it
        self._announced: Announced = _no_announce

    @property
    def schemas(self) -> list[Item]:
        return [tool.schema for tool in self._tools.values()]

    def confirms(self, call: Item) -> bool:
        """Whether a human answers yes before ``call`` runs (ADR-0009)."""
        tool = self._tools.get(_name(call))
        return tool is not None and tool.confirm

    def run(
        self,
        call: Item,
        stop: Callable[[], bool],
        ask_main: AskMain = _no_main,
        prompt_id: PromptId = NO_PROMPT_ID,
        announced: Announced = _no_announce,
    ) -> Item:
        """What a ``function_call`` gave back, as its ``function_call_output``: its value as
        JSON, or ``error …`` with why it failed, which the LLM reads as data. Each Command
        session it asks for that its function declares goes to ``ask_main``, and what a method
        announces to ``announced``; ``prompt_id``: the Turn's Prompt, which the tool may ask
        for. Raises ``ToolCancelled`` if ``stop`` took it down."""
        self._prompt_id, self._announced = prompt_id, announced
        return call_output(call, self._output(call, stop, ask_main))

    def _output(self, call: Item, stop: Callable[[], bool], ask_main: AskMain) -> str:
        name: object = call.get("name")
        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            return _no_tool(name)
        try:
            arguments = _arguments(call.get("arguments"))
        except ValidationError:
            return f"{TOOL_ERROR}its arguments are not a JSON object"
        try:
            outcome = self._outcome(tool, arguments, stop, ask_main)
        except ToolCancelled:
            raise
        except AllInError as error:  # timed out, died, could not be confined or started
            return f"{TOOL_ERROR}{error}"
        if outcome.error is not None:
            return f"{TOOL_ERROR}{outcome.error}"
        return json.dumps(outcome.value, ensure_ascii=False)

    def _outcome(
        self,
        tool: SessionTool,
        arguments: dict[str, JsonValue],
        stop: Callable[[], bool],
        ask_main: AskMain,
    ) -> ToolOutcome:
        """Run the call: a method in its Proceso adherido, anything else in a process of its
        own, in the Session's Carpeta de trabajo. Raises ``AllInError``."""
        on_command: OnCommand = lambda text: _declared(tool, text, ask_main)
        if tool.method is not None:
            process, announced = tool.method.process, self._announced
            frame = CallFrame(self._prompt_id, lambda text: announced(process, text))
            return tool.method.call(arguments, self._timeout, stop, on_command, frame)
        try:
            self._workspace.mkdir(parents=True, exist_ok=True)  # made when first needed
        except OSError as error:
            raise ToolNotStarted(cause=f"its Carpeta de trabajo cannot be made: {error}") from error
        call = ToolCall(
            tool.package,
            tool.module,
            tool.name,
            arguments,
            self._workspace,
            tool.reach,
            self._prompt_id,
        )
        return self._launcher.run(call, self._timeout, stop, on_command)


_ARGUMENTS = TypeAdapter(dict[str, JsonValue])


def call_output(call: Item, output: str) -> Item:
    """``output`` as what gave back the ``function_call`` ``call``."""
    call_id: object = call.get("call_id")
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def _no_tool(name: object) -> str:
    return f"{TOOL_ERROR}there is no tool named {name!r}"


def _arguments(text: object) -> dict[str, JsonValue]:
    """The LLM's arguments for a call; raises ``ValidationError`` if they are not a JSON
    object."""
    if not isinstance(text, str) or not text.strip():
        return {}
    return _ARGUMENTS.validate_json(text)


@dataclass(frozen=True)
class _Turn:
    session_id: SessionId
    cancellation: Cancellation
    emit: Emit
    confirmation: ConfirmationGate


class ToolGate:
    """How a running Turn runs the tools a Step called and waits for a slot to go on."""

    def __init__(
        self, tools: SessionTools | None, origin: Origin, prompt_id: PromptId = NO_PROMPT_ID
    ) -> None:
        """``origin``: the chain its tools act with, the Turn's Prompt's and its Session;
        ``prompt_id``: the Turn's Prompt's, which each tool may ask for (#116)."""
        self._tools = tools
        self._origin = origin
        self._prompt_id = prompt_id
        self._turn: _Turn | None = None
        # Main's word that the Turn holds a slot again: what the human sent meanwhile.
        self._resumed: queue.Queue[list[str]] = queue.Queue()
        self._yielded = False

    @property
    def schemas(self) -> list[Item]:
        """What goes in ``tools=[...]`` at each Step; none without tools."""
        return self._tools.schemas if self._tools is not None else []

    def connect(
        self,
        session_id: SessionId,
        cancellation: Cancellation,
        emit: Emit,
        confirmation: ConfirmationGate,
    ) -> None:
        """The Turn it belongs to, where its events go and how it asks a Confirmation; its
        executor connects it."""
        self._turn = _Turn(session_id, cancellation, emit, confirmation)

    def run(self, step: Step) -> Step:
        """From the Turn's thread: run the calls ``step`` made, in order, and give it back
        with their outputs once the Turn holds a slot again, and with what the human sent from
        its Window meanwhile (ADR-0024). The ones marked ``@confirm`` ask first, all before any
        runs, while the Turn still holds its slot; one a human says no to does not run. Raises
        ``TurnCancelled`` if the Turn is cancelled meanwhile, which also takes down the call
        that runs."""
        assert self._turn is not None, "run() before the executor connected the gate"
        turn = self._turn
        calls = step.calls
        allowed = [self._may_run(call, turn) for call in calls]
        names = tuple(_name(call) for call in calls)
        turn.emit(ToolsStarted(turn.session_id, names, step))
        results: list[Item] = []
        for call, may_run in zip(calls, allowed, strict=True):
            turn.cancellation.check()
            if self._tools is None:  # a Turn without tools: the LLM called one it does not have
                results.append(call_output(call, _no_tool(_name(call))))
                continue
            if not may_run:
                results.append(call_output(call, REFUSED))
                continue
            try:
                results.append(
                    self._tools.run(
                        call,
                        lambda: turn.cancellation.reason is not None,
                        lambda text, process: self._ask_main(turn, text, process),
                        self._prompt_id,
                        lambda process, text: turn.emit(
                            MethodAnnounced(process, text, from_process(self._origin, process))
                        ),
                    )
                )
            except ToolCancelled:
                turn.cancellation.check()
                raise
        done = step.model_copy(update={"results": results})
        grows = step.generated + sum(len(_output_text(result)) for result in results)
        turn.emit(ToolsEnded(turn.session_id, done, grows))
        while True:
            turn.cancellation.check()
            try:
                interjected = self._resumed.get(timeout=_POLL)
            except queue.Empty:
                continue
            return done.model_copy(update={"interjected": interjected})

    def resume(self, interjected: Sequence[str] = ()) -> None:
        """From Main: the Turn holds a slot again, so its next Step may start, reading
        ``interjected`` after the tools' outputs: what the human sent meanwhile."""
        self._resumed.put(list(interjected))

    def yield_turn(self) -> None:
        """From Main: the Turn stops after these tools, for its Session to compact first; it
        goes on from its Steps once that is done (ADR-0017)."""
        self._yielded = True
        self._resumed.put([])

    @property
    def yielded(self) -> bool:
        """Whether Main stopped the Turn after its tools, to compact before its next Step."""
        return self._yielded

    def _ask_main(self, turn: _Turn, text: str, process: AttachedName | None) -> JsonValue:
        """From the call's thread: Main runs the Command session with the Turn's Origin, after
        the Proceso adherido ``process`` if a method of one asks; wait for how it went. Raises
        ``ToolCancelled`` if the Turn is cancelled meanwhile."""
        origin = self._origin if process is None else from_process(self._origin, process)
        cancelled = lambda: turn.cancellation.reason is not None
        return ask_main(turn.emit, turn.session_id, text, origin, cancelled)

    def _may_run(self, call: Item, turn: _Turn) -> bool:
        """Whether ``call`` may run: yes, unless it asks a Confirmation and a human says no."""
        if self._tools is None or not self._tools.confirms(call):
            return True
        return turn.confirmation.ask(
            f"run {_name(call)}?", f"{_name(call)} {_arguments_shown(call)}"
        )


def ask_main(
    emit: Emit,
    session_id: SessionId,
    text: str,
    origin: Origin,
    cancelled: Callable[[], bool] = lambda: False,
) -> JsonValue:
    """From a call's thread: Main runs the Command session ``text`` in ``session_id`` with
    ``origin``; wait for how it went. Raises ``ToolCancelled`` once ``cancelled`` says so, and
    Main leaves the answer nobody waits for as a Notificación instead."""
    answers: queue.Queue[SessionCommandResult] = queue.Queue()
    lock = threading.Lock()
    waiting = [True]

    def answer(result: SessionCommandResult) -> bool:
        with lock:
            if waiting[0]:
                answers.put(result)
            return waiting[0]

    emit(SessionCommandAsked(session_id, text, origin, answer))
    while True:
        if cancelled():
            with lock:
                waiting[0] = False
            raise ToolCancelled
        try:
            return answers.get(timeout=_POLL).as_json()
        except queue.Empty:
            continue


def _arguments_shown(call: Item) -> str:
    """A call's arguments as its Confirmation shows them, cut if long."""
    arguments: object = call.get("arguments")
    shown = str(arguments)
    if len(shown) <= _SHOWN_ARGUMENTS:
        return shown
    return shown[:_SHOWN_ARGUMENTS] + "\N{HORIZONTAL ELLIPSIS}"


def _name(call: Item) -> str:
    name: object = call.get("name")
    return str(name)


def _output_text(result: Item) -> str:
    output: object = result.get("output")
    return str(output)
