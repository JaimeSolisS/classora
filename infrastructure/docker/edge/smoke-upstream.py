"""A stand-in for the API behind the edge in edge/smoke.sh (#610).

Standard library only; runs in a plain python image on port 8000.

- GET /api/v1/notifications/stream: a text/event-stream that stays open for
  two minutes, like the API's (#42): a comment at once, then a heartbeat
  comment every 2 s. A buffering edge delivers nothing within the test's 20 s.
- GET /health/*: 200 "upstream", so a 404 at the edge proves the edge
  blocked it.
- Any other GET: 200 JSON echoing the X-Forwarded-For the edge sent, with
  the API's own headers (Cache-Control: no-store, X-Content-Type-Options).
- Any POST: reads the whole body (Content-Length) and answers 200 JSON with
  the number of bytes received, so the edge's body cap (#150 SR-02) shows.

Every response carries Python's own ``Server`` header, which the edge must
drop (#150 SR-03).
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STREAM_PATH = "/api/v1/notifications/stream"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == STREAM_PATH:
            self._stream()
            return
        if self.path.startswith("/health/"):
            body = b"upstream"
            content_type = "text/plain"
        else:
            payload = {
                "path": self.path,
                "x_forwarded_for": self.headers.get_all("X-Forwarded-For") or [],
            }
            # Padded past Caddy's 512-byte minimum, so encoding would apply.
            payload["padding"] = "x" * 2048
            body = json.dumps(payload).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Smoke-Upstream", "1")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        remaining = int(self.headers.get("Content-Length") or 0)
        received = 0
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            received += len(chunk)
            remaining -= len(chunk)
        body = json.dumps({"received": received}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Smoke-Upstream", "1")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 5000\n\n: stream open\n\n")
            self.wfile.flush()
            for _ in range(60):
                time.sleep(2)
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()  # noqa: S104
