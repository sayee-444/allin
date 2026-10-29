"""Running a Turn: its Steps against the LLM, streaming what it says as it arrives.

In F1 a Turn has a single Step; with tools (F2) a Step that ends with pending tool calls is
followed by another one.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

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

from allin.core.errors import LlmRequestFailed, TurnCancelled
from allin.main.conversation import Item, Step

type OutputKind = Literal["reasoning", "answer"]


class LlmStream(Protocol):
    def __iter__(self) -> Iterator[ResponseStreamEvent]: ...
    def close(self) -> None: ...


class Llm(Protocol):
    def stream(self, model: str, input: list[Item], params: dict[str, Any]) -> LlmStream: ...


class OpenAiLlm:
    """The LLM behind an OpenAI-compatible server (llama-server), through the OpenAI SDK."""

    def __init__(self, base_url: str) -> None:
        self._client = OpenAI(
            base_url=base_url,
            api_key="None",
        )

    def stream(self, model: str, input: list[Item], params: dict[str, Any]) -> LlmStream:
        stream = self._client.responses.create(
            model=model,
            input=cast(ResponseInputParam, input),
            stream=True,
            **params,
        )
        return stream


@dataclass(frozen=True)
class TurnRequest:
    model: str
    input: list[Item]  # the flattened Conversation plus the Prompt
    params: dict[str, Any]  # sampling parameters to send; unset ones are left out


def run_turn(
    llm: Llm,
    request: TurnRequest,
    on_output: Callable[[OutputKind, str], None],
    cancelled: Callable[[], bool],
) -> list[Step]:
    """Run the Turn's Steps; raise ``LlmRequestFailed`` or ``TurnCancelled`` if it stops early."""
    return [_run_step(llm, request, on_output, cancelled)]


def _run_step(
    llm: Llm,
    request: TurnRequest,
    on_output: Callable[[OutputKind, str], None],
    cancelled: Callable[[], bool],
) -> Step:
    try:
        stream = llm.stream(request.model, request.input, request.params)
        try:
            for event in stream:
                if cancelled():
                    raise TurnCancelled(reason="main is shutting down")
                match event:
                    case ResponseReasoningTextDeltaEvent(delta=delta):
                        on_output("reasoning", delta)
                    case ResponseTextDeltaEvent(delta=delta):
                        on_output("answer", delta)
                    case ResponseCompletedEvent(response=response):
                        return _step(response)
                    case ResponseFailedEvent(response=response):
                        raise LlmRequestFailed(reason=str(response.error))
                    case ResponseIncompleteEvent(response=response):
                        raise LlmRequestFailed(reason=f"incomplete: {response.incomplete_details}")
                    case _:
                        pass
        finally:
            stream.close()  # also stops the server generating for a reader that is gone
    except openai.OpenAIError as error:
        raise LlmRequestFailed(reason=str(error)) from error
    raise LlmRequestFailed(reason="stream ended without a completed response")


def _step(response: Response) -> Step:
    # Items are kept as the JSON the server sent, which it accepts back as ``input``.
    output = [item.model_dump(mode="json", exclude_none=True) for item in response.output]
    usage = response.usage.model_dump(mode="json") if response.usage else None
    return Step(output=output, usage=usage)
