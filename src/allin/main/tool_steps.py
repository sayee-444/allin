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
from allin.core.errors import AllInError, ToolCancelled
from allin.core.ids import PieceId, SessionId
from allin.decorators import Reach
from allin.main.conversation import TOOL_ERROR, Item, Step
from allin.main.events import MainEvent, ToolsEnded, ToolsStarted
from allin.main.quarantine import OfferedFunction
from allin.main.turn import Cancellation

_POLL = 0.1  # how often a Turn waiting for its slot looks whether it was cancelled

type Emit = Callable[[MainEvent], None]


@dataclass(frozen=True)
class SessionTool:
    """One Function tool a Session's LLM may call, as Main runs it."""

    name: str
    package: Path
    module: str
    reach: Reach  # as its description read it, or as its Approval reduced it
    schema: Item  # as ``tools=[...]`` takes it


def session_tools(
    offered: Iterable[OfferedFunction], functions: Iterable[PieceId]
) -> list[SessionTool]:
    """The tools of the approved pieces among a Session's ``functions``. A tool its Approval
    marked to ask a Confirmation is left out until ``@confirm`` asks it (#107); so is one
    whose name another tool took first."""
    wanted = set(functions)
    tools: dict[str, SessionTool] = {}
    for function in offered:
        described = function.description
        if function.piece.id not in wanted or described.kind != "function":
            continue  # an fproc's methods arrive with the Procesos adheridos (#113)
        for tool in described.tools:
            if tool.name in tools or tool.name in function.confirm:
                continue
            schema: Item = {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            tools[tool.name] = SessionTool(
                tool.name, function.package, described.module, described.reach, schema
            )
    return list(tools.values())


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

    def run(self, call: Item, stop: Callable[[], bool]) -> Item:
        """What a ``function_call`` gave back, as its ``function_call_output``: its value as
        JSON, or ``error …`` with why it failed, which the LLM reads as data. Raises
        ``ToolCancelled`` if ``stop`` took it down."""
        return _call_output(call, self._output(call, stop))

    def _output(self, call: Item, stop: Callable[[], bool]) -> str:
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


class ToolGate:
    """How a running Turn runs the tools a Step called and waits for a slot to go on."""

    def __init__(self, tools: SessionTools | None = None) -> None:
        self._tools = tools
        self._turn: _Turn | None = None
        self._resumed: queue.Queue[None] = queue.Queue()

    @property
    def schemas(self) -> list[Item]:
        """What goes in ``tools=[...]`` at each Step; none without tools."""
        return self._tools.schemas if self._tools is not None else []

    def connect(self, session_id: SessionId, cancellation: Cancellation, emit: Emit) -> None:
        """The Turn it belongs to and where its events go; its executor connects it."""
        self._turn = _Turn(session_id, cancellation, emit)

    def run(self, step: Step) -> list[Item]:
        """From the Turn's thread: run the calls ``step`` made, in order, and give back their
        outputs once the Turn holds a slot again. Raises ``TurnCancelled`` if the Turn is
        cancelled meanwhile, which also takes down the call that runs."""
        assert self._turn is not None, "run() before the executor connected the gate"
        turn = self._turn
        calls = step.calls
        names = tuple(_name(call) for call in calls)
        turn.emit(ToolsStarted(turn.session_id, names))
        results: list[Item] = []
        for call in calls:
            turn.cancellation.check()
            if self._tools is None:  # a Turn without tools: the LLM called one it does not have
                results.append(_call_output(call, _no_tool(_name(call))))
                continue
            try:
                results.append(self._tools.run(call, lambda: turn.cancellation.reason is not None))
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


def _name(call: Item) -> str:
    name: object = call.get("name")
    return str(name)


def _output_text(result: Item) -> str:
    output: object = result.get("output")
    return str(output)
