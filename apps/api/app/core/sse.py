"""Server-Sent Events framing and the event-stream response (#42).

The wire format (WHATWG HTML "Server-sent events"): UTF-8 text, one event per
block of ``field: value`` lines ended by an empty line. A line starting with
``:`` is a comment (ignored by ``EventSource``; used as a heartbeat), and
``retry:`` sets the browser's reconnection delay.

:class:`EventStreamResponse` is a :class:`~starlette.responses.StreamingResponse`
with the ``text/event-stream`` media type and ``X-Accel-Buffering: no`` (so a
proxy that honors it doesn't buffer the stream). ``Cache-Control`` is left
to ``app.core.security_headers``, which sets ``no-store`` on every API
response. ``on_close`` runs once the response ends for any reason, even when
the client disconnects before the body starts, so a subscription taken by
the route is always released.
"""

import json
from collections.abc import AsyncIterable, Callable, Mapping
from typing import Any, Final

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

EVENT_STREAM: Final = "text/event-stream"
EVENT_STREAM_HEADERS: Final[Mapping[str, str]] = {"X-Accel-Buffering": "no"}


def _single_line(value: str, field: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError(f"an SSE {field} must be a single line")
    return value


def sse_event(
    event: str, data: Mapping[str, Any], *, event_id: str | None = None
) -> str:
    """One event: ``event``, an optional ``id`` and ``data`` as compact JSON."""
    lines = [f"event: {_single_line(event, 'event name')}"]
    if event_id is not None:
        lines.append(f"id: {_single_line(event_id, 'id')}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


def sse_comment(text: str) -> str:
    """A comment line (a heartbeat, or bytes that make a proxy flush)."""
    return f": {_single_line(text, 'comment')}\n\n"


def sse_retry(milliseconds: int) -> str:
    """The browser's reconnection delay."""
    return f"retry: {int(milliseconds)}\n\n"


class EventStreamResponse(StreamingResponse):
    """A ``text/event-stream`` response; ``on_close`` runs when it ends."""

    media_type = EVENT_STREAM

    def __init__(
        self,
        content: AsyncIterable[str],
        *,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(content, headers=dict(EVENT_STREAM_HEADERS))
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._on_close is not None:
                self._on_close()
