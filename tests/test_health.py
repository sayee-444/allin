import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Self

from allin.main.events import LlmAvailable, MainEvent
from allin.main.health import HealthWatch


class LoadingServer:
    """A local llama-server stand-in whose ``/health`` says 503 (loading) ``loading`` times."""

    def __init__(self, loading: int) -> None:
        self.asked = 0

        def handle(handler: BaseHTTPRequestHandler) -> None:
            if handler.path != "/health":
                handler.send_response(404)
            else:
                self.asked += 1
                handler.send_response(503 if self.asked <= loading else 200)
            handler.end_headers()
            handler.wfile.write(b'{"status": "ok"}')

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


def test_the_watch_says_the_llm_is_available_once_health_is_ok_and_then_stops():
    inbox: queue.Queue[MainEvent] = queue.Queue()
    with LoadingServer(loading=5) as server:
        watch = HealthWatch(server.base_url, inbox, interval=0.01)
        watch.start()
        watch.start()  # already watching: still one watch, one event
        assert inbox.get(timeout=5) == LlmAvailable()
        watch.stop()
        assert server.asked == 6
    assert inbox.empty()


def test_a_server_that_never_answers_is_watched_until_main_stops():
    inbox: queue.Queue[MainEvent] = queue.Queue()
    watch = HealthWatch("http://127.0.0.1:9/v1", inbox, interval=0.01)  # nothing listens there
    watch.start()
    watch.stop()
    assert inbox.empty()


def test_a_watch_started_right_after_one_reported_watches_again():
    inbox: queue.Queue[MainEvent] = queue.Queue()
    with LoadingServer(loading=0) as server:
        watch = HealthWatch(server.base_url, inbox, interval=0.01)
        for _ in range(20):  # the server died again as soon as the queue resumed
            watch.start()
            assert inbox.get(timeout=5) == LlmAvailable()
            watch.start()
            assert inbox.get(timeout=5) == LlmAvailable()
        watch.stop()
