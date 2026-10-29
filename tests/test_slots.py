import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Self

import pytest

from allin.main.slots import Slots, fetch_total_slots, resolve_slots


def test_the_servers_slots_are_used_when_parallel_is_not_set():
    assert resolve_slots(server_slots=4, parallel=None) == Slots(
        count=4, source="server", server_slots=4
    )


def test_parallel_set_by_hand_wins_over_the_servers_slots():
    slots = resolve_slots(server_slots=4, parallel=2)
    assert slots == Slots(count=2, source="setting", server_slots=4)
    assert slots.warning() is None


def test_parallel_above_the_servers_slots_is_used_but_warned_about():
    slots = resolve_slots(server_slots=1, parallel=3)
    assert slots.count == 3
    warning = slots.warning()
    assert warning is not None and "3" in warning and "1" in warning


def test_without_an_answer_from_the_server_parallel_is_used():
    assert resolve_slots(server_slots=None, parallel=2) == Slots(
        count=2, source="setting", server_slots=None
    )


def test_without_an_answer_from_the_server_nor_parallel_one_turn_runs_at_a_time():
    slots = resolve_slots(server_slots=None, parallel=None)
    assert slots == Slots(count=1, source="default", server_slots=None)
    assert slots.warning() is None


class FakeServer:
    """A local HTTP server that answers ``GET /props`` with ``body``."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        def handle(handler: BaseHTTPRequestHandler) -> None:
            handler.send_response(status if handler.path == "/props" else 404)
            handler.end_headers()
            handler.wfile.write(body)

        handler = type("Handler", (BaseHTTPRequestHandler,), {"do_GET": handle})
        handler.log_message = lambda *_: None  # type: ignore[method-assign]
        self._server = HTTPServer(("127.0.0.1", 0), handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/v1"

    def __enter__(self) -> Self:
        threading.Thread(target=self._server.serve_forever, args=(0.05,), daemon=True).start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()


def test_the_servers_slots_come_from_total_slots_in_props():
    with FakeServer(json.dumps({"total_slots": 3, "model_path": "m.gguf"}).encode()) as server:
        assert fetch_total_slots(server.base_url) == 3


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b"not json", 200),
        (json.dumps({"model_path": "m.gguf"}).encode(), 200),
        (json.dumps({"total_slots": 0}).encode(), 200),
        (json.dumps({"total_slots": "two"}).encode(), 200),
        (b"{}", 500),
    ],
)
def test_props_that_say_nothing_usable_give_no_slots(body: bytes, status: int):
    with FakeServer(body, status) as server:
        assert fetch_total_slots(server.base_url) is None


def test_a_server_that_does_not_answer_gives_no_slots():
    assert fetch_total_slots("http://127.0.0.1:9/v1", timeout=0.5) is None
