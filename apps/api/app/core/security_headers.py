"""Security headers on every API response (#24 D; ``_docs/security-headers.md``).

:class:`SecurityHeadersMiddleware` is a pure ASGI middleware that
``install_conventions`` installs **outside** ``RequestContextMiddleware``, so
it also sees the last-resort ``500`` that middleware writes. Every HTTP
response gets exactly one of each header in :data:`API_SECURITY_HEADERS`; a
value a route set is replaced:

- ``Cache-Control: no-store``: API responses carry personal data and are never
  stored by a browser or a shared cache;
- ``X-Content-Type-Options: nosniff``: a JSON body is never sniffed as HTML
  or script.

The SPA's own headers (Content Security Policy, framing) are set where the
SPA is served, not here (``apps/web/security-headers.ts``).
"""

from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_SECURITY_HEADERS: Final[tuple[tuple[bytes, bytes], ...]] = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
)
_NAMES: Final = frozenset(name for name, _ in API_SECURITY_HEADERS)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in _NAMES
                ]
                headers.extend(API_SECURITY_HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
