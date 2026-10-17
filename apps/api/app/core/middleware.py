"""Request ID, access log and last-resort error handling (architecture §4.3, step 2).

:class:`RequestContextMiddleware` is a pure ASGI middleware. For each HTTP
request it:

* accepts the inbound ``X-Request-ID`` if it matches :data:`REQUEST_ID_PATTERN`,
  otherwise generates a UUID4 (an invalid value is never echoed or logged, and
  the request is not rejected);
* stores the ID in a context variable, so every log line emitted while handling
  the request carries it;
* sets exactly one ``X-Request-ID`` response header;
* turns any unhandled exception into a generic ``500 internal_error`` Problem
  and logs it once, with the traceback, at ``ERROR``;
* writes one access line to ``app.access`` (method, path without query string,
  status, duration). Successful health probes are not logged.
"""

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import internal_error_response
from app.core.logging import request_id_var

REQUEST_ID_HEADER = b"x-request-id"
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")
# 2xx responses on these paths are not access-logged (Compose healthcheck noise).
QUIET_PATHS = frozenset({"/health/live", "/health/ready"})

access_logger = logging.getLogger("app.access")
error_logger = logging.getLogger("app.errors")


def _inbound_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == REQUEST_ID_HEADER:
            candidate: str = value.decode("latin-1")
            if REQUEST_ID_PATTERN.fullmatch(candidate):
                return candidate
            return None
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope) or str(uuid.uuid4())
        header_value = request_id.encode("ascii")
        status: int | None = None
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != REQUEST_ID_HEADER
                ]
                headers.append((REQUEST_ID_HEADER, header_value))
                message = {**message, "headers": headers}
            await send(message)

        token = request_id_var.set(request_id)
        try:
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                # Logged once here; not re-raised, so neither Starlette nor
                # uvicorn logs it again or renders their own error page.
                error_logger.exception("Unhandled exception while handling a request")
                if status is None:
                    response = internal_error_response()
                    await response(scope, receive, send_with_request_id)
            final_status = status if status is not None else 500
            path = str(scope.get("path", ""))
            if not (200 <= final_status < 300 and path in QUIET_PATHS):
                access_logger.info(
                    "request completed",
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": final_status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )
        finally:
            request_id_var.reset(token)
