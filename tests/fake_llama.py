"""A ``llama serve`` stand-in for the Supervisor's tests: it takes llama's flags, and
``--fake-mode`` says how it behaves.

- ``ok``: answers 503 for ``--fake-load`` seconds, then ``/health`` and ``/props``.
- ``crash``: exits with code 3 at once.
- ``--fake-die-after S``: once ready, exits with code 3 after S seconds (``--fake-die-oom``:
  saying first that it ran out of memory).
- ``--fake-report-env``: prints its folder and whether the environment asks context shift.
- ``vram``: prints what llama.cpp prints when the model does not fit, and exits with 1.
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


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
    parser.add_argument("--fake-die-after", type=float, default=None)  # once ready, exit 3
    parser.add_argument("--fake-die-oom", action="store_true")  # saying it ran out of memory
    parser.add_argument("--fake-report-env", action="store_true")  # its folder and environment
    args, _ = parser.parse_known_args()
    say(f"fake llama on {args.port}")
    if args.fake_report_env:
        say(f"cwd {Path.cwd()}")
        say(f"context shift env {os.environ.get('LLAMA_ARG_CONTEXT_SHIFT')}")
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

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    if args.fake_die_after is not None:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(args.fake_load + args.fake_die_after)
        if args.fake_die_oom:
            say("CUDA error: out of memory")
        os._exit(3)
    server.serve_forever()


if __name__ == "__main__":
    main()
