"""A fake LLM that replays Responses API stream events, built from the SDK's own types."""

import threading
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


def completed(
    thinking: str, text: str, total_tokens: int = 10, timings: dict[str, Any] | None = None
) -> ResponseCompletedEvent:
    """``timings`` is the extra field llama-server adds to the event."""
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
    event = ResponseCompletedEvent.model_construct(type="response.completed", response=response)
    if timings is not None:
        event.__pydantic_extra__ = {"timings": timings}
    return event


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


class HangingStream(FakeStream):
    """Streams its events, then waits as if the server were still generating.

    Closing it releases the reader: reading on fails, as with the SDK, or with ``ends_quietly``
    the stream just ends, as an SSE reader may when its connection is closed.
    """

    def __init__(self, events: list[ResponseStreamEvent], ends_quietly: bool = False) -> None:
        super().__init__(events)
        self._ends_quietly = ends_quietly
        self.waiting = threading.Event()  # set once every event has been read
        self._released = threading.Event()

    def __iter__(self) -> Iterator[ResponseStreamEvent]:
        yield from super().__iter__()
        self.waiting.set()
        self._released.wait()
        if not self._ends_quietly:
            raise RuntimeError("read from a closed stream")

    def close(self) -> None:
        super().close()
        self._released.set()


class FakeLlm:
    """Answers each Step with the next scripted stream and records what it was asked."""

    def __init__(self, *streams: list[ResponseStreamEvent] | Exception | FakeStream) -> None:
        self._streams = list(streams)
        self.requests: list[dict[str, Any]] = []
        self.opened: list[FakeStream] = []

    def stream(self, model: str, input: list[Item], params: dict[str, Any]) -> FakeStream:
        self.requests.append({"model": model, "input": input, **params})
        script = self._streams.pop(0)
        stream = script if isinstance(script, FakeStream) else FakeStream(script)
        self.opened.append(stream)
        return stream
