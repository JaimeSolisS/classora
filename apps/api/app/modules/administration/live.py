"""Live notifications: one ``LISTEN`` per API process, fanned out over SSE (#42).

Spec §10 (SSE and ``LISTEN``/``NOTIFY``), architecture §7.2 and ISSUE-N1,
ADR-0003 (the one direct ``LISTEN`` connection per API process).

- :func:`~app.modules.administration.notifications.create_notification`
  issues ``NOTIFY notifications`` with ``{user_id, notification_id}`` in the
  creating transaction.
- :class:`NotificationListener` (started by the app lifespan, never by
  ``build_app``) holds one **direct** connection to PostgreSQL
  (``DATABASE_DIRECT_URL``, never PgBouncer) through a dedicated SQLAlchemy
  engine, and ``LISTEN``\\ s with asyncpg's ``add_listener``. It checks the
  connection every :data:`LIVENESS_SECONDS` (and learns of a closed one at
  once); a lost or refused connection is retried with backoff 1, 2, 4 ... 30
  s, and after a reconnect every subscriber gets ``resync``. The API serves
  requests while it is down.
- :class:`NotificationHub` (one per app, created by ``build_app``) keeps
  ``user_id -> subscribers`` in memory and hands each notification only to
  its recipient's subscribers. A subscriber's buffer holds
  :data:`QUEUE_SIZE` events; when it is full the subscriber's events are
  dropped and it gets one ``resync`` instead. At most
  :data:`MAX_STREAMS_PER_USER` streams per user per process.
- :func:`stream_events` is the body of ``GET /api/v1/notifications/stream``:
  ``retry`` and a comment first, then the replay, then events and a heartbeat
  comment every :attr:`NotificationHub.heartbeat_seconds`. Every
  :attr:`NotificationHub.session_check_seconds` it asks the registered
  :data:`StreamSessionCheck` whether the session is still live (read-only:
  the stream never refreshes ``last_seen_at``), and ends when it isn't.

Logs name no user, notification or payload.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.sse import sse_comment, sse_event, sse_retry
from app.modules.administration.errors import TooManyStreamsError
from app.modules.administration.notifications import NOTIFICATION_CHANNEL

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS: Final = 15.0
SESSION_CHECK_SECONDS: Final = 60.0
LIVENESS_SECONDS: Final = 15.0
LIVENESS_TIMEOUT_SECONDS: Final = 5.0
BACKOFF_FIRST_SECONDS: Final = 1.0
BACKOFF_MAX_SECONDS: Final = 30.0
MAX_STREAMS_PER_USER: Final = 10
QUEUE_SIZE: Final = 100
REPLAY_LIMIT: Final = 100
RETRY_MILLISECONDS: Final = 5000

NOTIFICATION_EVENT: Final = "notification"
RESYNC_EVENT: Final = "resync"

HUB_STATE: Final = "notification_hub"
SESSION_CHECK_STATE: Final = "stream_session_check"

StreamSessionCheck = Callable[[AsyncSession, uuid.UUID], Awaitable[bool]]
"""Whether the session with this id is still live (unrevoked, not timed out,
its user active). Read-only: it must never refresh ``last_seen_at``.
Registered by the composition root (``identity`` provides it), so
``administration`` imports no module (#40 E4)."""


# --- Payloads -------------------------------------------------------------------


def parse_payload(payload: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """``(user_id, notification_id)`` of a ``NOTIFY`` payload, or ``None``
    for anything else than an object with exactly those two UUIDs."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != {"user_id", "notification_id"}:
        return None
    user_id, notification_id = data["user_id"], data["notification_id"]
    if not isinstance(user_id, str) or not isinstance(notification_id, str):
        return None
    try:
        return uuid.UUID(user_id), uuid.UUID(notification_id)
    except ValueError:
        return None


# --- Subscribers and the hub ------------------------------------------------------


@dataclass(frozen=True)
class LiveEvent:
    kind: Literal["notification", "resync"]
    notification_id: uuid.UUID | None = None


RESYNC: Final = LiveEvent("resync")


class Subscriber:
    """One open stream's buffer of events (at most ``capacity``)."""

    def __init__(self, user_id: uuid.UUID, capacity: int = QUEUE_SIZE) -> None:
        self.user_id = user_id
        self._capacity = capacity
        self._events: deque[uuid.UUID] = deque()
        self._resync = False
        self._closed = False
        self._wake = asyncio.Event()

    @property
    def closed(self) -> bool:
        return self._closed

    def push(self, notification_id: uuid.UUID) -> None:
        """Buffer one notification. A full buffer is dropped and replaced by
        one ``resync``; later events are dropped until it is sent."""
        if self._closed or self._resync:
            return
        if len(self._events) >= self._capacity:
            self._events.clear()
            self._resync = True
        else:
            self._events.append(notification_id)
        self._wake.set()

    def push_resync(self) -> None:
        """Anything buffered or missed is superseded by one ``resync``."""
        if self._closed:
            return
        self._events.clear()
        self._resync = True
        self._wake.set()

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    async def next(self) -> LiveEvent | None:
        """The next event, waiting for one; ``None`` once closed."""
        while True:
            if self._closed:
                return None
            if self._resync:
                self._resync = False
                return RESYNC
            if self._events:
                return LiveEvent("notification", self._events.popleft())
            self._wake.clear()
            await self._wake.wait()


class NotificationHub:
    """The process's subscribers, by user. Not thread-safe: everything runs
    on the event loop (asyncpg calls the listener callback there too)."""

    def __init__(
        self,
        *,
        max_streams_per_user: int = MAX_STREAMS_PER_USER,
        queue_size: int = QUEUE_SIZE,
    ) -> None:
        # Injectable for tests (#42 C2, C4): read by stream_events.
        self.heartbeat_seconds: float = HEARTBEAT_SECONDS
        self.session_check_seconds: float = SESSION_CHECK_SECONDS
        self._max_streams = max_streams_per_user
        self._queue_size = queue_size
        self._subscribers: dict[uuid.UUID, set[Subscriber]] = {}
        self._closed = False

    def subscribe(self, user_id: uuid.UUID) -> Subscriber:
        """A new subscriber for ``user_id``; ``429 too_many_streams`` when the
        user already holds the maximum. On a closed hub (shutdown) the
        subscriber is closed at once, so its stream ends."""
        current = self._subscribers.setdefault(user_id, set())
        if len(current) >= self._max_streams:
            if not current:  # pragma: no cover - max_streams is at least 1
                del self._subscribers[user_id]
            raise TooManyStreamsError()
        subscriber = Subscriber(user_id, self._queue_size)
        if self._closed:
            subscriber.close()
        current.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        """Release ``subscriber`` (again: no change)."""
        current = self._subscribers.get(subscriber.user_id)
        if current is None:
            return
        current.discard(subscriber)
        if not current:
            del self._subscribers[subscriber.user_id]

    def stream_count(self, user_id: uuid.UUID) -> int:
        return len(self._subscribers.get(user_id, ()))

    def deliver(self, user_id: uuid.UUID, notification_id: uuid.UUID) -> None:
        """Hand the notification to ``user_id``'s subscribers only."""
        for subscriber in self._subscribers.get(user_id, ()):
            subscriber.push(notification_id)

    def resync_all(self) -> None:
        for subscribers in self._subscribers.values():
            for subscriber in subscribers:
                subscriber.push_resync()

    def dispatch(self, payload: str) -> None:
        """Deliver one ``NOTIFY`` payload; a malformed one is logged (without
        its content) and dropped."""
        parsed = parse_payload(payload)
        if parsed is None:
            logger.warning("dropped a malformed notification payload")
            return
        self.deliver(*parsed)

    def close(self) -> None:
        """End every stream (app shutdown); later subscribers close at once."""
        self._closed = True
        for subscribers in self._subscribers.values():
            for subscriber in subscribers:
                subscriber.close()


def install_notification_hub(app: FastAPI) -> NotificationHub:
    """Give ``app`` its hub (``build_app``). Opens no connection."""
    hub = NotificationHub()
    setattr(app.state, HUB_STATE, hub)
    return hub


def notification_hub_of(app: FastAPI) -> NotificationHub:
    """``app``'s hub; installed now if ``app`` has none (an app assembled
    without ``build_app`` that still runs the lifespan)."""
    hub: NotificationHub | None = getattr(app.state, HUB_STATE, None)
    return hub if hub is not None else install_notification_hub(app)


def notification_hub(request: Request) -> NotificationHub:
    return notification_hub_of(request.app)


def register_stream_session_check(app: FastAPI, check: StreamSessionCheck) -> None:
    """Make ``check`` the way ``app``'s streams re-check their session."""
    setattr(app.state, SESSION_CHECK_STATE, check)


def registered_stream_session_check(request: Request) -> StreamSessionCheck | None:
    check: StreamSessionCheck | None = getattr(
        request.app.state, SESSION_CHECK_STATE, None
    )
    return check


# --- The stream body --------------------------------------------------------------


def _format(event: LiveEvent) -> str:
    if event.kind == NOTIFICATION_EVENT and event.notification_id is not None:
        notification_id = str(event.notification_id)
        return sse_event(
            NOTIFICATION_EVENT,
            {"notification_id": notification_id},
            event_id=notification_id,
        )
    return sse_event(RESYNC_EVENT, {})


async def stream_events(
    *,
    hub: NotificationHub,
    subscriber: Subscriber,
    replay: list[uuid.UUID] | None,
    session_alive: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    """The SSE body of one stream (#42 C1-C4).

    ``replay``: the notifications to send first, oldest first (``[]``: none),
    or ``None`` for a ``resync``. Ends when the subscriber is closed (app
    shutdown) or ``session_alive`` answers ``False``.
    """
    yield sse_retry(RETRY_MILLISECONDS) + sse_comment("stream open")
    if replay is None:
        yield _format(RESYNC)
    else:
        for notification_id in replay:
            yield _format(LiveEvent("notification", notification_id))
    loop = asyncio.get_running_loop()
    next_heartbeat = loop.time() + hub.heartbeat_seconds
    next_check = loop.time() + hub.session_check_seconds
    while True:
        now = loop.time()
        if now >= next_check:
            if not await session_alive():
                return
            next_check = loop.time() + hub.session_check_seconds
        if now >= next_heartbeat:
            yield sse_comment("heartbeat")
            next_heartbeat = loop.time() + hub.heartbeat_seconds
        wait = max(0.0, min(next_heartbeat, next_check) - loop.time())
        try:
            event = await asyncio.wait_for(subscriber.next(), timeout=wait)
        except TimeoutError:
            continue
        if event is None:
            return
        yield _format(event)


# --- The listener ---------------------------------------------------------------


class NotificationListener:
    """The process's one direct ``LISTEN`` connection (ADR-0003).

    ``engine`` is a direct engine (``app.core.db.create_direct_engine``, pool
    size 1); the listener disposes it on :meth:`stop`. The intervals are
    injectable for tests.
    """

    def __init__(
        self,
        hub: NotificationHub,
        engine: AsyncEngine,
        *,
        liveness_seconds: float = LIVENESS_SECONDS,
        backoff_first_seconds: float = BACKOFF_FIRST_SECONDS,
        backoff_max_seconds: float = BACKOFF_MAX_SECONDS,
    ) -> None:
        self._hub = hub
        self._engine = engine
        self._liveness_seconds = liveness_seconds
        self._backoff_first = backoff_first_seconds
        self._backoff_max = backoff_max_seconds
        self._task: asyncio.Task[None] | None = None
        self.connected = asyncio.Event()
        """Set while the ``LISTEN`` is active (tests wait on it)."""
        self.backend_pid: int | None = None
        """The PostgreSQL backend of the current connection (tests)."""

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="notification-listener")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.connected.clear()
        await self._engine.dispose()

    def _on_notify(
        self, _connection: Any, _pid: int, _channel: str, payload: str
    ) -> None:
        self._hub.dispatch(payload)

    async def _run(self) -> None:
        failures = 0  # consecutive attempts that never connected
        first = True
        while True:
            try:
                await self._listen(resync=not first)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The exception's type only: its text could name the host.
                logger.warning(
                    "notification listener connection failed (%s)", type(exc).__name__
                )
            else:
                logger.warning("notification listener lost its connection")
            first = False
            if self.connected.is_set():
                failures = 0
            self.connected.clear()
            self.backend_pid = None
            delay = min(self._backoff_first * 2**failures, self._backoff_max)
            failures = min(failures + 1, 16)
            logger.info("notification listener reconnecting in %.1f s", delay)
            await asyncio.sleep(delay)

    async def _listen(self, *, resync: bool) -> None:
        """Hold one ``LISTEN`` until the connection is lost (returns) or
        fails (raises)."""
        connection = await self._engine.connect()
        try:
            raw = (await connection.get_raw_connection()).driver_connection
            if raw is None:  # pragma: no cover - asyncpg always has one
                raise RuntimeError("no driver connection")
            lost = asyncio.Event()
            raw.add_termination_listener(lambda _connection: lost.set())
            await raw.add_listener(NOTIFICATION_CHANNEL, self._on_notify)
            self.backend_pid = raw.get_server_pid()
            self.connected.set()
            logger.info("notification listener connected")
            if resync:
                # Anything sent while the connection was down was missed.
                self._hub.resync_all()
            while not lost.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(lost.wait(), timeout=self._liveness_seconds)
                    break
                await asyncio.wait_for(
                    raw.fetchval("SELECT 1"), timeout=LIVENESS_TIMEOUT_SECONDS
                )
        finally:
            # Never back to the pool: the connection holds the LISTEN state.
            with contextlib.suppress(Exception):
                await connection.invalidate()
            with contextlib.suppress(Exception):
                await connection.close()
