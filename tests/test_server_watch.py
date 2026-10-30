import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Self

from allin.main.events import MainEvent, ServerDown, ServerLoading, ServerReady
from allin.main.server_watch import ServerWatch, probe
from allin.main.slots import ServerProps

NOBODY = "http://127.0.0.1:9/v1"  # nothing listens there


class FakeServer:
    """A local llama-server stand-in; change ``loading`` or ``props`` while it runs."""

    def __init__(self, props: dict[str, Any], loading: bool = False) -> None:
        self.loading = loading
        self.props = props

        def handle(handler: BaseHTTPRequestHandler) -> None:
            if handler.path not in ("/health", "/props"):
                handler.send_response(404)
            elif self.loading:
                handler.send_response(503)
            else:
                handler.send_response(200)
            handler.end_headers()
            body = self.props if handler.path == "/props" else {"status": "ok"}
            handler.wfile.write(json.dumps(body).encode())

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


def props(slots: int, n_ctx: int) -> dict[str, Any]:
    return {
        "total_slots": slots,
        "model_path": "m.gguf",
        "default_generation_settings": {"n_ctx": n_ctx},
    }


def ready(slots: int, n_ctx: int) -> ServerReady:
    return ServerReady(ServerProps(total_slots=slots, slot_context=n_ctx, model_path="m.gguf"))


def test_a_server_that_does_not_answer_is_down():
    assert probe(NOBODY, timeout=0.5) == ServerDown()


def test_a_server_that_says_503_is_loading_its_model():
    with FakeServer(props(2, 64000), loading=True) as server:
        assert probe(server.base_url) == ServerLoading()


def test_a_ready_server_tells_its_props():
    with FakeServer(props(2, 64000)) as server:
        assert probe(server.base_url) == ready(2, 64000)


def test_a_server_whose_props_say_nothing_usable_is_down():
    with FakeServer({"model_path": "m.gguf"}) as server:
        assert probe(server.base_url) == ServerDown()


def next_event(inbox: queue.Queue[MainEvent]) -> MainEvent:
    return inbox.get(timeout=5)


def test_the_watch_reports_each_change_once_until_main_stops():
    inbox: queue.Queue[MainEvent] = queue.Queue()
    with FakeServer(props(2, 64000), loading=True) as server:
        watch = ServerWatch(server.base_url, inbox, interval=0.01)
        watch.start()
        try:
            assert next_event(inbox) == ServerLoading()
            server.loading = False
            assert next_event(inbox) == ready(2, 64000)
            server.props = props(1, 128000)  # restarted with -np 1 between two looks
            assert next_event(inbox) == ready(1, 128000)
        finally:
            watch.stop()
    assert inbox.empty()  # nothing changed after that, so nothing else was said


def test_a_watch_asked_to_recheck_says_again_what_it_sees():
    inbox: queue.Queue[MainEvent] = queue.Queue()
    with FakeServer(props(2, 64000)) as server:
        watch = ServerWatch(server.base_url, inbox, interval=0.01)
        watch.start()
        try:
            assert next_event(inbox) == ready(2, 64000)
            watch.recheck()  # a Turn found it unavailable, though it looked ready
            assert next_event(inbox) == ready(2, 64000)
        finally:
            watch.stop()


def test_a_server_that_never_answers_is_said_down_once():
    inbox: queue.Queue[MainEvent] = queue.Queue()
    watch = ServerWatch(NOBODY, inbox, interval=0.01)
    watch.start()
    assert next_event(inbox) == ServerDown()
    watch.stop()
    assert inbox.empty()
