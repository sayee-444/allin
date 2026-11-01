"""A ``llama serve`` stand-in for the Supervisor's tests: it takes llama's flags, and
``--fake-mode`` says how it behaves.

- ``ok``: answers 503 for ``--fake-load`` seconds, then ``/health`` and ``/props``.
- ``crash``: exits with code 3 at once.
- ``vram``: prints what llama.cpp prints when the model does not fit, and exits with 1.
"""

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def say(line: str) -> None:
    """What llama prints: the Supervisor keeps it in the Server's log."""
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("-c", type=int, default=4096)
    parser.add_argument("-np", type=int, default=1)
    parser.add_argument("-m", default="m.gguf")
    parser.add_argument("--kv-unified", action="store_true")
    parser.add_argument("--fake-mode", default="ok")
    parser.add_argument("--fake-load", type=float, default=0.0)
    args, _ = parser.parse_known_args()
    say(f"fake llama on {args.port}")
    if args.fake_mode == "crash":
        sys.exit(3)
    if args.fake_mode == "vram":
        say("ggml_backend_cuda_buffer_type_alloc_buffer: allocating 9000 MiB on device 0:")
        say("cudaMalloc failed: out of memory")
        sys.exit(1)
    ready_at = time.monotonic() + args.fake_load
    n_ctx = args.c if args.kv_unified or args.np == 1 else args.c // args.np
    props = {
        "total_slots": args.np,
        "model_path": args.m,
        "default_generation_settings": {"n_ctx": n_ctx},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in ("/health", "/props"):
                self.send_response(404)
                self.end_headers()
                return
            if time.monotonic() < ready_at:
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            body = props if self.path == "/props" else {"status": "ok"}
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
