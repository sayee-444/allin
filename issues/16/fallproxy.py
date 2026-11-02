"""A Server for manual tests: proxies to the llama on 9931, but while <scratch>/fall exists every
Step reaches it and the connection is cut before any answer (a fall, ADR-0023)."""
import socket
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FALL = Path(sys.argv[1]) / "fall"
REAL = "http://127.0.0.1:9931"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args): pass

    def _proxy(self, body=None):
        request = urllib.request.Request(REAL + self.path, data=body, method=self.command)
        for key in ("Content-Type", "Authorization", "Accept"):
            if self.headers.get(key):
                request.add_header(key, self.headers[key])
        try:
            reply = urllib.request.urlopen(request, timeout=600)
        except urllib.error.HTTPError as error:
            reply = error
        self.send_response(reply.status if hasattr(reply, "status") else reply.code)
        for key, value in reply.headers.items():
            if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(key, value)
        self.end_headers()
        while chunk := reply.read1(4096):
            self.wfile.write(chunk); self.wfile.flush()

    def do_GET(self): self._proxy()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if FALL.exists():
            with open(FALL.parent / "falls.log", "a") as log: log.write(f"fall {self.path}\n")
            self.connection.shutdown(socket.SHUT_RDWR); self.close_connection = True
            return
        self._proxy(body)


ThreadingHTTPServer(("127.0.0.1", 9951), Handler).serve_forever()
