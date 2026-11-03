import json
import queue
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from allin.core.ids import ServerKey, SessionId
from allin.main.conversation import Item
from allin.main.events import InputMeasured, MainEvent
from allin.main.measure import Measurer, chat_messages, measure_input

REASONING: Item = {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "hmm"}]}
ANSWER: Item = {
    "type": "message",
    "role": "assistant",
    "content": [{"type": "output_text", "text": "red, "}, {"type": "output_text", "text": "blue"}],
}


def test_the_conversation_becomes_the_chat_messages_its_template_takes() -> None:
    items: list[Item] = [
        {"role": "developer", "content": "be terse"},
        {"role": "user", "content": "colors?"},
        REASONING,
        ANSWER,
        {"role": "user", "content": "animals?"},
    ]
    assert chat_messages(items) == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "colors?"},
        {"role": "assistant", "content": "red, blue", "reasoning_content": "hmm"},
        {"role": "user", "content": "animals?"},
    ]


def test_a_step_s_tool_calls_and_their_outputs_are_messages_the_template_takes() -> None:
    # llama's template refuses two assistant messages at the end, as a Turn that goes on from
    # its Steps has them (ADR-0023, #119), and its Server reads them this way when it runs.
    items: list[Item] = [
        {"role": "user", "content": "read both"},
        REASONING,
        {"type": "function_call", "call_id": "c1", "name": "read", "arguments": '{"n": 1}'},
        {"type": "function_call", "call_id": "c2", "name": "read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "mango"},
        {"type": "function_call_output", "call_id": "c2", "output": [{"type": "input_text"}]},
    ]
    assert chat_messages(items) == [
        {"role": "user", "content": "read both"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "hmm",
            "tool_calls": [
                {
                    "type": "function",
                    "id": "c1",
                    "function": {"name": "read", "arguments": '{"n": 1}'},
                },
                {"type": "function", "id": "c2", "function": {"name": "read", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "mango"},
        {"role": "tool", "tool_call_id": "c2", "content": '[{"type": "input_text"}]'},
    ]


def test_an_answer_and_the_tools_its_step_called_are_one_message() -> None:
    call: Item = {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"}
    [message] = chat_messages([ANSWER, call])
    assert message["content"] == "red, blue"
    assert message["tool_calls"] == [
        {"type": "function", "id": "c1", "function": {"name": "f", "arguments": "{}"}}
    ]


def test_an_item_it_does_not_know_is_measured_as_its_text() -> None:
    other: Item = {"type": "image_generation_call", "id": "i1"}
    assert chat_messages([other]) == [{"role": "assistant", "content": json.dumps(other)}]


class _Llama(BaseHTTPRequestHandler):
    """``/apply-template`` joins the contents; ``/tokenize`` gives a token per character."""

    asked: ClassVar[list[tuple[str, object]]] = []
    broken: ClassVar[bool] = False

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).asked.append((self.path, body))
        if self.broken:
            self.send_response(500)
            self.end_headers()
            return
        if self.path == "/apply-template":
            reply: object = {"prompt": "|".join(m["content"] for m in body["messages"])}
        elif self.path == "/tokenize":
            reply = {"tokens": list(range(len(body["content"])))}
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(reply).encode())

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def llama() -> Generator[str]:
    _Llama.asked = []
    _Llama.broken = False
    server = HTTPServer(("127.0.0.1", 0), _Llama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    server.server_close()


def test_the_input_is_measured_by_the_server_s_own_template_and_tokenizer(llama: str) -> None:
    items: list[Item] = [{"role": "developer", "content": "abc"}, {"role": "user", "content": "de"}]
    assert measure_input(llama, items) == len("abc|de")
    assert _Llama.asked[1] == ("/tokenize", {"content": "abc|de", "add_special": True})


def test_a_server_that_cannot_measure_gives_none(llama: str) -> None:
    _Llama.broken = True
    assert measure_input(llama, [{"role": "user", "content": "x"}]) is None
    assert measure_input("http://127.0.0.1:1/v1", [{"role": "user", "content": "x"}]) is None


def test_the_measurer_tells_main_on_its_own_thread(llama: str) -> None:
    inbox: queue.Queue[MainEvent] = queue.Queue()
    measurer = Measurer(ServerKey("9931"), llama, inbox)
    measurer.measure(SessionId("s1"), 7, [{"role": "user", "content": "hello"}])
    assert inbox.get(timeout=5) == InputMeasured(ServerKey("9931"), SessionId("s1"), 7, 5)
    measurer.stop()


def test_reasoning_with_no_answer_after_it_is_measured_too() -> None:
    items: list[Item] = [
        {"role": "user", "content": "go"},
        REASONING,
        {"role": "user", "content": "on"},
    ]
    assert chat_messages(items) == [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "reasoning_content": "hmm"},
        {"role": "user", "content": "on"},
    ]
    assert chat_messages(items[:2])[-1] == {
        "role": "assistant",
        "content": "",
        "reasoning_content": "hmm",
    }
