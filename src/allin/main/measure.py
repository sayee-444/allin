"""Measuring a Step's input before it is admitted (ADR-0004): with the Server's own chat
template (``/apply-template``) and tokenizer (``/tokenize``), so it counts what the Server will
count (checked on build 10909: 63 and 105 tokens, as its ``usage`` said).

Measuring is blocking I/O, so it runs on a thread of its own per Server, which tells the
dispatcher through its inbox (ADR-0001).
"""

import json
import queue
import threading
import urllib.request

from pydantic import BaseModel, TypeAdapter, ValidationError

from allin.core.ids import ServerKey, SessionId
from allin.main.conversation import Item
from allin.main.events import InputMeasured, MainEvent
from allin.main.slots import server_root

_TIMEOUT = 30.0  # a long Conversation takes a moment to tokenize
type ChatMessage = dict[str, str]


def chat_messages(items: list[Item]) -> list[ChatMessage]:
    """The Responses API ``input`` as the chat messages llama's template takes: the
    reasoning goes with the answer that follows it, or alone if none does (a Step cut while it
    reasoned), and an item of another kind is measured as its JSON."""
    messages: list[ChatMessage] = []
    reasoning = ""

    def flush_reasoning() -> None:
        nonlocal reasoning
        if reasoning:
            messages.append({"role": "assistant", "content": "", "reasoning_content": reasoning})
            reasoning = ""

    for item in items:
        kind = item.get("type")
        if kind == "reasoning":
            reasoning += _text(item.get("content"))
        elif kind in {None, "message"} and "role" in item:
            role = "system" if item["role"] == "developer" else str(item["role"])
            message = {"role": role, "content": _text(item.get("content"))}
            if role == "assistant" and reasoning:
                message["reasoning_content"] = reasoning
                reasoning = ""
            flush_reasoning()
            messages.append(message)
        else:
            flush_reasoning()
            messages.append({"role": "assistant", "content": json.dumps(item)})
    flush_reasoning()
    return messages


class _Part(BaseModel):
    text: str | None = None


_Content = TypeAdapter(str | list[_Part])


def _text(content: object) -> str:
    try:
        parsed = _Content.validate_python(content)
    except ValidationError:
        return ""
    if isinstance(parsed, str):
        return parsed
    return "".join(part.text for part in parsed if part.text is not None)


class _Template(BaseModel):
    prompt: str


class _Tokens(BaseModel):
    tokens: list[int]


def measure_input(base_url: str, items: list[Item], timeout: float = _TIMEOUT) -> int | None:
    """How many tokens the Server counts in ``items``; ``None`` if it cannot say, e.g. a
    Server that is not llama's."""
    root = server_root(base_url)
    try:
        template = _post(f"{root}/apply-template", {"messages": chat_messages(items)}, timeout)
        prompt = _Template.model_validate_json(template).prompt
        body = {"content": prompt, "add_special": True}
        return len(_Tokens.model_validate_json(_post(f"{root}/tokenize", body, timeout)).tokens)
    except (OSError, ValidationError):
        return None


def _post(url: str, body: object, timeout: float) -> bytes:
    request = urllib.request.Request(
        url, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return reply.read()


class Measurer:
    """Measures, one at a time on its own thread, the input of Steps waiting for a Server, and
    tells each result as ``InputMeasured``."""

    def __init__(self, server: ServerKey, base_url: str, inbox: queue.Queue[MainEvent]) -> None:
        self._server = server
        self._base_url = base_url
        self._inbox = inbox
        self._jobs: queue.Queue[tuple[SessionId, int, list[Item]] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._work, name=f"measure-{server}", daemon=True)
        self._thread.start()

    def measure(self, session_id: SessionId, ticket: int, items: list[Item]) -> None:
        """Measure ``items``; the result carries ``ticket``, so Main can tell a stale one."""
        self._jobs.put((session_id, ticket, items))

    def stop(self) -> None:
        self._jobs.put(None)
        self._thread.join()

    def _work(self) -> None:
        while (job := self._jobs.get()) is not None:
            session_id, ticket, items = job
            tokens = measure_input(self._base_url, items)
            self._inbox.put(InputMeasured(self._server, session_id, ticket, tokens))
