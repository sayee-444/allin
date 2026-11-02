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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue, TypeAdapter, ValidationError

from allin.confinement.launcher import ToolCall, ToolLauncher
from allin.core.errors import AllInError, SessionCommandNotDeclared, ToolCancelled
from allin.core.ids import PieceId, SessionId
from allin.core.origin import MAIN, Origin
from allin.decorators import CONFIRM, Reach
from allin.main.confirmation import ConfirmationGate
from allin.main.conversation import TOOL_ERROR, Item, Step
from allin.main.events import MainEvent, SessionCommandAsked, ToolsEnded, ToolsStarted
from allin.main.prompt_commands import session_command_name
from allin.main.quarantine import OfferedFunction
from allin.main.turn import Cancellation

_POLL = 0.1  # how often a Turn waiting for its slot looks whether it was cancelled
_SHOWN_ARGUMENTS = 600  # of a call's arguments, what its Confirmation shows
# What the LLM reads of a call a human said no to: it did not run (ADR-0009).
REFUSED = "the user refused this call: it did not run"

type Emit = Callable[[MainEvent], None]


@dataclass(frozen=True)
class SessionTool:
    """One Function tool a Session's LLM may call, as Main runs it."""

    name: str
    package: Path
    module: str
    reach: Reach  # as its description read it, or as its Approval reduced it
    schema: Item  # as ``tools=[...]`` takes it
    confirm: bool = False  # it asks a Confirmation before each call: ``@confirm`` or its Approval


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


# Runs a Command session a tool asked for, with its Turn's Origin, and gives back how it went.
type AskMain = Callable[[str], JsonValue]


def _no_main(text: str) -> JsonValue:
    return {"error": f"no Main to run {text[:40]!r}"}


def _declared(tool: SessionTool, text: str, ask_main: AskMain) -> JsonValue:
    """Main runs only the Command sessions a function declares in its Alcance (ADR-0025)."""
    name = session_command_name(text)
    if name is None or name not in tool.reach.commands:
        refused = SessionCommandNotDeclared(command=name or text[:40], tool=tool.name)
        return {"error": str(refused)}
    return ask_main(text)


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

    @property
    def schemas(self) -> list[Item]:
        return [tool.schema for tool in self._tools.values()]

    def confirms(self, call: Item) -> bool:
        """Whether a human answers yes before ``call`` runs (ADR-0009)."""
        tool = self._tools.get(_name(call))
        return tool is not None and tool.confirm

    def run(self, call: Item, stop: Callable[[], bool], ask_main: AskMain = _no_main) -> Item:
        """What a ``function_call`` gave back, as its ``function_call_output``: its value as
        JSON, or ``error …`` with why it failed, which the LLM reads as data. Each Command
        session it asks for that its function declares goes to ``ask_main``. Raises
        ``ToolCancelled`` if ``stop`` took it down."""
        return _call_output(call, self._output(call, stop, ask_main))

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
            self._workspace.mkdir(parents=True, exist_ok=True)  # made when first needed
        except OSError as error:
            return f"{TOOL_ERROR}its Carpeta de trabajo cannot be made: {error}"
        try:
            outcome = self._launcher.run(
                ToolCall(
                    tool.package, tool.module, tool.name, arguments, self._workspace, tool.reach
                ),
                self._timeout,
                stop,
                lambda text: _declared(tool, text, ask_main),
            )
        except ToolCancelled:
            raise
        except AllInError as error:  # timed out, died, could not be confined or started
            return f"{TOOL_ERROR}{error}"
        if outcome.error is not None:
            return f"{TOOL_ERROR}{outcome.error}"
        return json.dumps(outcome.value, ensure_ascii=False)


_ARGUMENTS = TypeAdapter(dict[str, JsonValue])


def _call_output(call: Item, output: str) -> Item:
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

    def __init__(self, tools: SessionTools | None = None, origin: Origin = MAIN) -> None:
        """``origin``: the chain its tools act with, the Turn's Prompt's and its Session."""
        self._tools = tools
        self._origin = origin
        self._turn: _Turn | None = None
        self._resumed: queue.Queue[None] = queue.Queue()

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

    def run(self, step: Step) -> list[Item]:
        """From the Turn's thread: run the calls ``step`` made, in order, and give back their
        outputs once the Turn holds a slot again. The ones marked ``@confirm`` ask first, all
        before any runs, while the Turn still holds its slot; one a human says no to does not
        run. Raises ``TurnCancelled`` if the Turn is cancelled meanwhile, which also takes down
        the call that runs."""
        assert self._turn is not None, "run() before the executor connected the gate"
        turn = self._turn
        calls = step.calls
        allowed = [self._may_run(call, turn) for call in calls]
        names = tuple(_name(call) for call in calls)
        turn.emit(ToolsStarted(turn.session_id, names))
        results: list[Item] = []
        for call, may_run in zip(calls, allowed, strict=True):
            turn.cancellation.check()
            if self._tools is None:  # a Turn without tools: the LLM called one it does not have
                results.append(_call_output(call, _no_tool(_name(call))))
                continue
            if not may_run:
                results.append(_call_output(call, REFUSED))
                continue
            try:
                results.append(
                    self._tools.run(
                        call,
                        lambda: turn.cancellation.reason is not None,
                        lambda text: self._ask_main(turn, text),
                    )
                )
            except ToolCancelled:
                turn.cancellation.check()
                raise
        done = step.model_copy(update={"results": results})
        grows = step.generated + sum(len(_output_text(result)) for result in results)
        turn.emit(ToolsEnded(turn.session_id, grows, tuple(done.failures())))
        while True:
            turn.cancellation.check()
            try:
                self._resumed.get(timeout=_POLL)
            except queue.Empty:
                continue
            return results

    def resume(self) -> None:
        """From Main: the Turn holds a slot again, so its next Step may start."""
        self._resumed.put(None)

    def _ask_main(self, turn: _Turn, text: str) -> JsonValue:
        """From the call's thread: Main runs the Command session with the Turn's Origin; wait
        for how it went. Raises ``ToolCancelled`` if the Turn is cancelled meanwhile."""
        answers: queue.Queue[JsonValue] = queue.Queue()
        turn.emit(SessionCommandAsked(turn.session_id, text, self._origin, answers.put))
        while True:
            if turn.cancellation.reason is not None:
                raise ToolCancelled
            try:
                return answers.get(timeout=_POLL)
            except queue.Empty:
                continue

    def _may_run(self, call: Item, turn: _Turn) -> bool:
        """Whether ``call`` may run: yes, unless it asks a Confirmation and a human says no."""
        if self._tools is None or not self._tools.confirms(call):
            return True
        return turn.confirmation.ask(
            f"run {_name(call)}?", f"{_name(call)} {_arguments_shown(call)}"
        )


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
