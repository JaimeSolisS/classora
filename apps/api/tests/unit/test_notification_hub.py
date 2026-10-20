"""The live-notification hub, its subscribers, payloads and SSE framing (#42
B3, C2, C5). No database: the listener and the endpoint are covered by
``tests/integration/test_live_notifications.py``."""

import asyncio
import json
import logging
import uuid

import pytest

from app.core.sse import sse_comment, sse_event, sse_retry
from app.modules.administration.errors import TooManyStreamsError
from app.modules.administration.live import (
    RESYNC,
    LiveEvent,
    NotificationHub,
    Subscriber,
    parse_payload,
    stream_events,
)
from app.modules.administration.notifications import notify_payload

A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
B = uuid.UUID("00000000-0000-4000-8000-00000000000b")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def ids(n: int) -> list[uuid.UUID]:
    return [uuid.UUID(int=i + 1) for i in range(n)]


async def drain(subscriber: Subscriber) -> list[LiveEvent]:
    """Every buffered event, without waiting."""
    events: list[LiveEvent] = []
    while True:
        try:
            event = await asyncio.wait_for(subscriber.next(), 0.01)
        except TimeoutError:
            return events
        assert event is not None
        events.append(event)


# --- Payloads -------------------------------------------------------------------


def test_the_payload_round_trips_and_is_compact() -> None:
    payload = notify_payload(A, B)
    assert payload == f'{{"user_id":"{A}","notification_id":"{B}"}}'
    assert parse_payload(payload) == (A, B)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "not json",
        "[]",
        json.dumps({"user_id": str(A)}),
        json.dumps({"user_id": str(A), "notification_id": str(B), "extra": 1}),
        json.dumps({"user_id": "x", "notification_id": str(B)}),
        json.dumps({"user_id": 1, "notification_id": str(B)}),
    ],
)
def test_a_malformed_payload_parses_to_none(payload: str) -> None:
    assert parse_payload(payload) is None


@pytest.mark.anyio
async def test_a_malformed_payload_is_logged_without_its_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = NotificationHub()
    subscriber = hub.subscribe(A)
    with caplog.at_level(logging.WARNING):
        hub.dispatch('{"secret-ish": "x"}')
    assert "malformed" in caplog.text
    assert "secret-ish" not in caplog.text
    assert await drain(subscriber) == []


# --- Fan-out ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_event_goes_only_to_its_recipients_subscribers() -> None:
    hub = NotificationHub()
    a1, a2, b = hub.subscribe(A), hub.subscribe(A), hub.subscribe(B)
    n = uuid.uuid4()
    hub.dispatch(notify_payload(A, n))
    expected = [LiveEvent("notification", n)]
    assert await drain(a1) == expected
    assert await drain(a2) == expected
    assert await drain(b) == []


@pytest.mark.anyio
async def test_a_full_buffer_is_replaced_by_one_resync() -> None:
    hub = NotificationHub(queue_size=3)
    subscriber = hub.subscribe(A)
    for n in ids(5):
        hub.deliver(A, n)
    assert await drain(subscriber) == [RESYNC]
    # Afterwards it buffers again.
    [n] = ids(1)
    hub.deliver(A, n)
    assert await drain(subscriber) == [LiveEvent("notification", n)]


@pytest.mark.anyio
async def test_resync_all_supersedes_buffered_events() -> None:
    hub = NotificationHub()
    a, b = hub.subscribe(A), hub.subscribe(B)
    hub.deliver(A, uuid.uuid4())
    hub.resync_all()
    assert await drain(a) == [RESYNC]
    assert await drain(b) == [RESYNC]


@pytest.mark.anyio
async def test_closing_the_hub_ends_current_and_later_subscribers() -> None:
    hub = NotificationHub()
    current = hub.subscribe(A)
    hub.close()
    later = hub.subscribe(B)
    assert await current.next() is None
    assert await later.next() is None


# --- Limits ------------------------------------------------------------------------


def test_the_eleventh_stream_of_a_user_is_refused() -> None:
    hub = NotificationHub()
    subscribers = [hub.subscribe(A) for _ in range(10)]
    with pytest.raises(TooManyStreamsError) as refused:
        hub.subscribe(A)
    assert refused.value.status == 429
    assert refused.value.headers == {"Retry-After": "30"}
    # Another user is not affected, and a released place can be taken.
    hub.subscribe(B)
    hub.unsubscribe(subscribers[0])
    hub.unsubscribe(subscribers[0])  # again: no change
    hub.subscribe(A)
    assert hub.stream_count(A) == 10


# --- SSE framing and the stream body ------------------------------------------------


def test_sse_framing() -> None:
    assert sse_retry(5000) == "retry: 5000\n\n"
    assert sse_comment("heartbeat") == ": heartbeat\n\n"
    assert (
        sse_event("notification", {"notification_id": "x"}, event_id="x")
        == 'event: notification\nid: x\ndata: {"notification_id":"x"}\n\n'
    )
    assert sse_event("resync", {}) == "event: resync\ndata: {}\n\n"
    with pytest.raises(ValueError):
        sse_event("bad\nevent", {})


@pytest.mark.anyio
async def test_the_stream_body_opens_replays_and_ends_with_the_subscriber() -> None:
    hub = NotificationHub()
    hub.heartbeat_seconds = 60
    subscriber = hub.subscribe(A)
    first, second, live = ids(3)

    async def alive() -> bool:
        return True

    body = stream_events(
        hub=hub, subscriber=subscriber, replay=[first, second], session_alive=alive
    )
    chunks = [await anext(body) for _ in range(3)]
    hub.deliver(A, live)
    chunks.append(await anext(body))
    subscriber.close()
    assert [chunk async for chunk in body] == []
    assert chunks[0] == "retry: 5000\n\n: stream open\n\n"
    assert [
        json.loads(c.split("data: ")[1])["notification_id"] for c in chunks[1:]
    ] == [
        str(first),
        str(second),
        str(live),
    ]


@pytest.mark.anyio
async def test_without_a_replay_it_resyncs_and_a_dead_session_ends_it() -> None:
    hub = NotificationHub()
    hub.session_check_seconds = 0.01
    subscriber = hub.subscribe(A)
    checks: list[bool] = []

    async def dead() -> bool:
        checks.append(False)
        return False

    body = stream_events(
        hub=hub, subscriber=subscriber, replay=None, session_alive=dead
    )
    chunks = [chunk async for chunk in body]
    assert chunks[1] == "event: resync\ndata: {}\n\n"
    assert len(chunks) == 2
    assert checks == [False]
