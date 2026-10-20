"""Reading a streaming response from the app under a ``TestClient`` (#42).

``TestClient`` (like httpx's ``ASGITransport``) returns a response only once
the app has finished it, so an endless Server-Sent Events stream would hang
it. :class:`AsgiStream` drives the ASGI app itself: it sends the request,
returns once ``http.response.start`` arrives, hands out the body as it comes,
and on close sends ``http.disconnect`` (what a browser closing the
connection does).

:class:`ClientStream` runs an :class:`AsgiStream` on the ``TestClient``'s
event loop (``client.portal``; the app's lifespan, its engines and the
notification listener live there), so sync tests can open a stream, do
other requests and read events in any order. The request is built with the
client's ``build_request``, so its default headers (``Origin``) apply.
"""

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message

OPEN_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class SseFrame:
    """One block of an event stream (fields as sent; ``comments`` without
    the leading ``: ``)."""

    event: str | None = None
    id: str | None = None
    data: str | None = None
    retry: int | None = None
    comments: tuple[str, ...] = field(default=())

    @property
    def is_comment(self) -> bool:
        return self.event is None and self.data is None and self.retry is None


def parse_frame(block: str) -> SseFrame:
    values: dict[str, Any] = {}
    comments: list[str] = []
    for line in block.split("\n"):
        if line.startswith(":"):
            comments.append(line[1:].strip())
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "retry":
            values["retry"] = int(value)
        elif name in ("event", "id", "data"):
            values[name] = value
    return SseFrame(**values, comments=tuple(comments))


class StreamEnded(Exception):
    """The response body ended (the server closed the stream)."""


class AsgiStream:
    """One request whose response body is read as it arrives."""

    def __init__(self, app: ASGIApp, request: httpx.Request) -> None:
        self._app = app
        self._request = request
        self._messages: asyncio.Queue[Message] = asyncio.Queue()
        self._disconnect = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._buffer = ""
        self.ended = False
        self.status: int | None = None
        self.headers = httpx.Headers()
        self.body = b""

    def _scope(self) -> dict[str, Any]:
        url = self._request.url
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": self._request.method,
            "scheme": url.scheme,
            "path": url.path,
            "raw_path": url.raw_path.split(b"?")[0],
            "query_string": url.query,
            "root_path": "",
            "headers": [
                (name.lower(), value) for name, value in self._request.headers.raw
            ],
            "client": ("127.0.0.1", 50000),
            "server": (url.host, url.port or 80),
            "extensions": {},
        }

    async def open(self) -> None:
        """Send the request; return once the response has started."""
        body = self._request.read()
        sent_body = False

        async def receive() -> Message:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await self._disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            await self._messages.put(message)

        self._task = asyncio.create_task(self._app(self._scope(), receive, send))
        start = await asyncio.wait_for(self._messages.get(), OPEN_TIMEOUT_SECONDS)
        assert start["type"] == "http.response.start", start
        self.status = int(start["status"])
        self.headers = httpx.Headers(
            [
                (name.decode("latin-1"), value.decode("latin-1"))
                for name, value in start["headers"]
            ]
        )

    async def read_chunk(self, timeout: float) -> bytes:
        """The next body chunk; :class:`StreamEnded` once the body ended."""
        if self.ended:
            raise StreamEnded()
        message = await asyncio.wait_for(self._messages.get(), timeout)
        chunk = bytes(message.get("body", b""))
        self.body += chunk
        if not message.get("more_body", False):
            self.ended = True
            if not chunk:
                raise StreamEnded()
        return chunk

    async def read_body(self, timeout: float) -> bytes:
        """Everything until the body ends (for a finished, non-stream answer)."""
        try:
            while True:
                await self.read_chunk(timeout)
        except StreamEnded:
            return self.body

    async def next_frame(self, timeout: float) -> SseFrame:
        """The next SSE block (``asyncio.TimeoutError`` after ``timeout``)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while "\n\n" not in self._buffer:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError()
            self._buffer += (await self.read_chunk(remaining)).decode()
        block, self._buffer = self._buffer.split("\n\n", 1)
        return parse_frame(block)

    async def close(self) -> None:
        """Disconnect and wait for the app to finish the request."""
        self._disconnect.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, OPEN_TIMEOUT_SECONDS)
            except TimeoutError:  # pragma: no cover - a hung app fails the test
                self._task.cancel()
                raise


class ClientStream:
    """An :class:`AsgiStream` driven from sync code through ``client.portal``."""

    def __init__(
        self,
        client: TestClient,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._client = client
        request = client.build_request(method, path, headers=dict(headers or {}))
        self._stream = AsgiStream(client.app, request)

    def _call(self, function: Any, *args: Any) -> Any:
        portal = self._client.portal
        assert portal is not None, "use the TestClient as a context manager"
        return portal.call(function, *args)

    def __enter__(self) -> ClientStream:
        self._call(self._stream.open)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def status(self) -> int | None:
        return self._stream.status

    @property
    def headers(self) -> httpx.Headers:
        return self._stream.headers

    @property
    def ended(self) -> bool:
        return self._stream.ended

    def close(self) -> None:
        self._call(self._stream.close)

    def read_body(self, timeout: float = OPEN_TIMEOUT_SECONDS) -> bytes:
        body: bytes = self._call(self._stream.read_body, timeout)
        return body

    def next_frame(self, timeout: float = 5.0) -> SseFrame:
        frame: SseFrame = self._call(self._stream.next_frame, timeout)
        return frame

    def next_event(self, timeout: float = 5.0) -> SseFrame:
        """The next frame that is an event (comments are skipped)."""
        deadline = time.monotonic() + timeout
        while True:
            frame = self.next_frame(max(0.0, deadline - time.monotonic()))
            if frame.event is not None:
                return frame

    def response(self, timeout: float = OPEN_TIMEOUT_SECONDS) -> httpx.Response:
        """A finished response (status, headers, body), for answers that are
        not streams (``401``, ``403``, ``429``)."""
        return httpx.Response(
            self.status or 0, headers=self.headers, content=self.read_body(timeout)
        )
