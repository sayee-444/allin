"""A fake LLM that replays Responses API stream events, built from the SDK's own types."""

from collections.abc import Iterator
from typing import Any

from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFailedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
    ResponseReasoningTextDeltaEvent,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_reasoning_item import Content

from allin.main.conversation import Item


def reasoning(delta: str) -> ResponseReasoningTextDeltaEvent:
    return ResponseReasoningTextDeltaEvent.model_construct(
        type="response.reasoning_text.delta", delta=delta, item_id="rs_1"
    )


def answer(delta: str) -> ResponseTextDeltaEvent:
    return ResponseTextDeltaEvent.model_construct(
        type="response.output_text.delta", delta=delta, item_id="msg_1"
    )


def completed(thinking: str, text: str, total_tokens: int = 10) -> ResponseCompletedEvent:
    output = [
        ResponseReasoningItem(
            id="rs_1",
            type="reasoning",
            summary=[],
            content=[Content(type="reasoning_text", text=thinking)],
        ),
        ResponseOutputMessage(
            id="msg_1",
            type="message",
            role="assistant",
            status="completed",
            content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
        ),
    ]
    usage = ResponseUsage.model_construct(
        input_tokens=total_tokens // 2,
        output_tokens=total_tokens - total_tokens // 2,
        total_tokens=total_tokens,
    )
    response = Response.model_construct(id="resp_1", output=output, usage=usage)
    return ResponseCompletedEvent.model_construct(type="response.completed", response=response)


def failed(message: str) -> ResponseFailedEvent:
    response = Response.model_construct(id="resp_1", output=[], error={"message": message})
    return ResponseFailedEvent.model_construct(type="response.failed", response=response)


def full_reply(thinking: str, text: str) -> list[ResponseStreamEvent]:
    return [reasoning(thinking), answer(text), completed(thinking, text)]


class FakeStream:
    def __init__(self, events: list[ResponseStreamEvent] | Exception) -> None:
        self._events = events
        self.closed = False

    def __iter__(self) -> Iterator[ResponseStreamEvent]:
        if isinstance(self._events, Exception):
            raise self._events
        yield from self._events

    def close(self) -> None:
        self.closed = True


class FakeLlm:
    """Answers each Step with the next scripted stream and records what it was asked."""

    def __init__(self, *streams: list[ResponseStreamEvent] | Exception) -> None:
        self._streams = list(streams)
        self.requests: list[dict[str, Any]] = []
        self.opened: list[FakeStream] = []

    def stream(self, model: str, input: list[Item], params: dict[str, Any]) -> FakeStream:
        self.requests.append({"model": model, "input": input, **params})
        stream = FakeStream(self._streams.pop(0))
        self.opened.append(stream)
        return stream
