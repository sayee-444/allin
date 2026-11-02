"""Running a Turn: its Steps against the LLM, streaming what it says as it arrives.

A Step that ends calling tools is followed by another one, which reads what they gave back;
the Turn ends with the first Step that calls none (#106).
"""

import socket
import threading
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, cast

import httpx2
import openai
from openai import OpenAI
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFailedEvent,
    ResponseIncompleteEvent,
    ResponseInputParam,
    ResponseReasoningTextDeltaEvent,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
)

from allin.core.errors import LlmRequestFailed, LlmUnavailable, TurnCancelled
from allin.main.conversation import Item, Step

type OutputKind = Literal["reasoning", "answer"]


class LlmStream(Protocol):
    def __iter__(self) -> Iterator[ResponseStreamEvent]: ...
    def close(self) -> None: ...


class Tools(Protocol):
    """How a Turn offers tools and runs the ones a Step called (``tool_steps.ToolGate``)."""

    @property
    def schemas(self) -> list[Item]: ...
    def run(self, calls: list[Item]) -> list[Item]: ...


class Llm(Protocol):
    def stream(self, model: str, input: list[Item], params: dict[str, Any]) -> LlmStream: ...


class OpenAiLlm:
    """The LLM behind an OpenAI-compatible server (llama-server), through the OpenAI SDK."""

    def __init__(self, base_url: str) -> None:
        self._client = OpenAI(
            base_url=base_url,
            api_key="None",
            max_retries=0,  # a server that is down pauses Main's queue until /health says OK
        )

    def stream(self, model: str, input: list[Item], params: dict[str, Any]) -> LlmStream:
        return _SdkStream(
            self._client.responses.create(
                model=model,
                input=cast(ResponseInputParam, input),
                stream=True,
                **params,
            )
        )


class _SdkStream:
    """The SDK's stream, whose ``close`` also wakes a reader blocked on a Server gone silent:
    closing its socket does not, shutting it down does. A stalled Step ends at once and lets
    its slot go, instead of when the Server sends again (ADR-0023)."""

    def __init__(self, stream: openai.Stream[ResponseStreamEvent]) -> None:
        self._stream = stream

    def __iter__(self) -> Iterator[ResponseStreamEvent]:
        return iter(self._stream)

    def close(self) -> None:
        network: object = self._stream.response.extensions.get("network_stream")
        get_extra_info: object = getattr(network, "get_extra_info", None)
        connection = get_extra_info("socket") if callable(get_extra_info) else None
        if isinstance(connection, socket.socket):
            with suppress(OSError):  # already shut down or closed
                connection.shutdown(socket.SHUT_RDWR)
        self._stream.close()


class Cancellation:
    """Stops a running Turn from any thread.

    The Turn reads no more events, and its active stream is closed at once, so llama.cpp stops
    generating even while the Step waits for the next token.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reason: str | None = None
        self._stream: LlmStream | None = None

    def cancel(self, reason: str) -> None:
        """Only the first reason counts; cancelling a finished Turn does nothing."""
        with self._lock:
            if self._reason is not None:
                return
            self._reason = reason
            stream = self._stream
        if stream is not None:
            stream.close()

    @property
    def reason(self) -> str | None:
        return self._reason

    def check(self) -> None:
        """Raise ``TurnCancelled`` if the Turn was cancelled."""
        if self._reason is not None:
            raise TurnCancelled(reason=self._reason)

    @contextmanager
    def watching(self, stream: LlmStream) -> Generator[None]:
        """While a Step reads ``stream``, ``cancel`` closes it; then it is closed anyway.

        Reading a stream closed by ``cancel`` fails however the SDK makes it fail, so any error
        raised inside becomes ``TurnCancelled`` once the Turn is cancelled.
        """
        with self._lock:
            self._stream = stream
        try:
            yield
        except Exception:
            self.check()
            raise
        finally:
            with self._lock:
                self._stream = None
            stream.close()  # also stops the server generating for a reader that is gone


@dataclass(frozen=True)
class TurnRequest:
    model: str
    input: list[Item]  # the flattened Conversation plus the Prompt
    params: dict[str, Any]  # sampling parameters to send; unset ones are left out
    # The Steps it had ended before a fall suspended it, already in ``input``: it goes on from
    # there, and the tools they called are not run again (ADR-0023).
    done: list[Step] = field(default_factory=list[Step])


def run_turn(
    llm: Llm,
    request: TurnRequest,
    on_output: Callable[[OutputKind, str], None],
    cancellation: Cancellation,
    on_raw: Callable[[dict[str, Any]], None],
    on_step: Callable[[Step], None],
    tools: Tools | None = None,
) -> list[Step]:
    """Run the Turn's Steps, all of them with ``tools``' schemas, and the tools each one calls
    before the next; raise ``LlmRequestFailed`` or ``TurnCancelled`` if it stops early.

    ``LlmUnavailable``, a kind of ``LlmRequestFailed``, means the server could not be reached
    or is still loading its model.

    ``on_raw`` gets every stream event as JSON, ``on_output`` only the text it streams and
    ``on_step`` each Step as soon as it ends, with what its tools gave back.
    """
    steps = list(request.done)
    schemas = tools.schemas if tools is not None else []
    params = {**request.params, "tools": schemas} if schemas else request.params
    step_request = replace(request, params=params)
    while True:
        step = _run_step(llm, step_request, on_output, cancellation, on_raw)
        if step.calls:
            results = tools.run(step.calls) if tools is not None else []
            step = step.model_copy(update={"results": results})
        on_step(step)
        steps.append(step)
        if not step.calls or tools is None:
            return steps
        step_request = replace(step_request, input=[*step_request.input, *step.items])


def _run_step(
    llm: Llm,
    request: TurnRequest,
    on_output: Callable[[OutputKind, str], None],
    cancellation: Cancellation,
    on_raw: Callable[[dict[str, Any]], None],
) -> Step:
    try:
        stream = llm.stream(request.model, request.input, request.params)
        with cancellation.watching(stream):
            cancellation.check()
            for event in stream:
                cancellation.check()
                on_raw(event.model_dump(mode="json", exclude_none=True, warnings=False))
                match event:
                    case ResponseReasoningTextDeltaEvent(delta=delta):
                        on_output("reasoning", delta)
                    case ResponseTextDeltaEvent(delta=delta):
                        on_output("answer", delta)
                    case ResponseCompletedEvent(response=response):
                        return _step(response, (event.model_extra or {}).get("timings"))
                    case ResponseFailedEvent(response=response):
                        raise LlmRequestFailed(reason=str(response.error))
                    case ResponseIncompleteEvent(response=response):
                        raise LlmRequestFailed(reason=f"incomplete: {response.incomplete_details}")
                    case _:
                        pass
            cancellation.check()  # a stream closed by ``cancel`` may also just end
    except openai.APITimeoutError as error:  # a slow server is up: only this Step fails
        raise LlmRequestFailed(reason=str(error)) from error
    except openai.APIConnectionError as error:  # the SDK's own, also for a stream cut short
        # Refused: the Step never reached the Server. Else it fell with the Step in it, maybe
        # while it read the input, before anything streamed (ADR-0023).
        reached = not isinstance(error.__cause__, httpx2.ConnectError)
        raise LlmUnavailable(reason=str(error), reached=reached) from error
    except openai.APIStatusError as error:
        if error.status_code == 503:  # llama-server is loading the model: as good as down
            raise LlmUnavailable(reason=str(error), reached=False) from error
        raise LlmRequestFailed(reason=str(error)) from error
    except openai.OpenAIError as error:
        raise LlmRequestFailed(reason=str(error)) from error
    raise LlmRequestFailed(reason="stream ended without a completed response")


def _step(response: Response, timings: dict[str, Any] | None) -> Step:
    """``timings`` is not in the Responses API: llama-server adds it to the completed event."""
    # Items are kept as the JSON the server sent, which it accepts back as ``input``.
    output = [item.model_dump(mode="json", exclude_none=True) for item in response.output]
    usage = response.usage.model_dump(mode="json") if response.usage else None
    return Step(output=output, usage=usage, timings=timings)
