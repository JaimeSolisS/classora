"""Live notifications over SSE through the real app (#42 A2, C1-C6, D1-D7).

The module's client runs the real lifespan, so its ``NotificationListener``
holds a direct ``LISTEN`` connection to the Testcontainers PostgreSQL.
Streams are read with :class:`tests.authz.streaming.ClientStream` (the
``TestClient`` itself waits for a response to finish). Notifications are
created by ``administration.service.create_notification`` in a committed or
rolled-back Unit of Work, as a producing module would.

Waiting is done on events (a frame arriving, the listener's ``connected``
event), never by sleeping; the stream intervals are shortened through the
hub's ``heartbeat_seconds`` and ``session_check_seconds``.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.administration.live import (
    HUB_STATE,
    MAX_STREAMS_PER_USER,
    NotificationHub,
    NotificationListener,
)
from app.modules.administration.notifications import NOTIFICATION_CHANNEL
from app.modules.administration.notifications_dev import (
    DEV_DEMO_TYPE,
    register_dev_notification_type,
)
from app.modules.identity import sessions
from app.modules.identity.permissions import OWNER
from tests.authz.streaming import ClientStream, SseFrame, StreamEnded
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    cookie_header,
    create_user,
    csrf_for,
)
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import code_of, in_uow, me, signed_in

STREAM = "/api/v1/notifications/stream"
DELIVERY_SECONDS = 2.0


class Deliberate(Exception):
    pass


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    register_dev_notification_type()
    with auth_client(module_seeded_url) as c:
        yield c


@pytest.fixture
def hub(client: TestClient) -> Iterator[NotificationHub]:
    """The app's hub; its intervals are restored after the test."""
    hub: NotificationHub = getattr(client.app.state, HUB_STATE)  # type: ignore[attr-defined]
    heartbeat, check = hub.heartbeat_seconds, hub.session_check_seconds
    yield hub
    hub.heartbeat_seconds, hub.session_check_seconds = heartbeat, check


def listener_of(client: TestClient) -> NotificationListener:
    listener: NotificationListener = client.app.state.notification_listener  # type: ignore[attr-defined]
    return listener


def wait_listening(client: TestClient, timeout: float = 10.0) -> None:
    listener = listener_of(client)

    async def wait() -> None:
        await asyncio.wait_for(listener.connected.wait(), timeout)

    assert client.portal is not None
    client.portal.call(wait)


@pytest.fixture
def user(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


@pytest.fixture
def other(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=("tutor",))


def notify(url: str, recipient: CreatedUser, label: str = "Live") -> uuid.UUID:
    return in_uow(url, create_for(recipient, label))


def create_for(
    recipient: CreatedUser, label: str = "Live"
) -> Callable[[AsyncSession], Any]:
    register_dev_notification_type()

    def call(session: AsyncSession) -> Any:
        return administration.create_notification(
            session,
            recipient_user_id=recipient.id,
            notification_type=DEV_DEMO_TYPE,
            params={"label": label},
        )

    return call


def notify_rolled_back(url: str, recipient: CreatedUser) -> None:
    async def call(session: AsyncSession) -> None:
        await create_for(recipient)(session)
        raise Deliberate()

    with pytest.raises(Deliberate):
        in_uow(url, call)


def open_stream(
    client: TestClient, cookie: SetCookie | None, **headers: str
) -> ClientStream:
    sent = {**(cookie_header(cookie) if cookie else {}), **headers}
    return ClientStream(client, "GET", STREAM, headers=sent)


def opened(stream: ClientStream) -> ClientStream:
    """Check the opening bytes (``retry`` and a comment) and return the stream."""
    assert stream.status == 200
    assert stream.next_frame() == SseFrame(retry=5000)
    assert stream.next_frame().is_comment
    return stream


def notification_id_of(frame: SseFrame) -> str:
    assert frame.event == "notification", frame
    assert frame.data is not None
    data = json.loads(frame.data)
    assert data == {"notification_id": frame.id}
    return str(frame.id)


def is_resync(frame: SseFrame) -> bool:
    return frame.event == "resync" and frame.id is None and frame.data == "{}"


def ended_within(stream: ClientStream, seconds: float) -> bool:
    """Whether the stream ends within ``seconds`` (heartbeats are skipped)."""
    deadline = time.monotonic() + seconds
    try:
        while (remaining := deadline - time.monotonic()) > 0:
            stream.next_frame(remaining)
    except StreamEnded:
        return True
    except TimeoutError:
        pass
    return False


# --- A2: NOTIFY in the creating transaction ----------------------------------------


@pytest.mark.anyio
async def test_a_commit_notifies_once_and_a_rollback_never(
    module_seeded_url: str, user: CreatedUser
) -> None:
    register_dev_notification_type()
    listen_engine = short_lived_engine(module_seeded_url)
    work_engine = short_lived_engine(module_seeded_url)
    payloads: asyncio.Queue[str] = asyncio.Queue()

    async def create(*, fail: bool) -> uuid.UUID:
        async with UnitOfWork(create_sessionmaker(work_engine)) as uow:
            created = await create_for(user)(uow.session)
            if fail:
                raise Deliberate()
            return uuid.UUID(str(created))

    try:
        async with listen_engine.connect() as connection:
            raw = (await connection.get_raw_connection()).driver_connection
            assert raw is not None
            await raw.add_listener(
                NOTIFICATION_CHANNEL, lambda *args: payloads.put_nowait(args[3])
            )
            first = await create(fail=False)
            with pytest.raises(Deliberate):
                await create(fail=True)
            sentinel = await create(fail=False)

            received = [
                await asyncio.wait_for(payloads.get(), 5),
                await asyncio.wait_for(payloads.get(), 5),
            ]
    finally:
        await listen_engine.dispose()
        await work_engine.dispose()

    # Exactly one payload per commit, none for the rollback, in commit order.
    assert [json.loads(p) for p in received] == [
        {"user_id": str(user.id), "notification_id": str(first)},
        {"user_id": str(user.id), "notification_id": str(sentinel)},
    ]
    assert all(", " not in p and len(p.encode()) < 200 for p in received)


# --- C1, D1: delivery -------------------------------------------------------------


def test_the_stream_opens_with_its_headers_and_delivers_a_new_notification(
    client: TestClient, hub: NotificationHub, module_seeded_url: str, user: CreatedUser
) -> None:
    cookie = signed_in(client, user.username)
    wait_listening(client)
    with open_stream(client, cookie) as stream:
        opened(stream)
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["x-accel-buffering"] == "no"
        assert stream.headers["cache-control"] == "no-store"
        assert hub.stream_count(user.id) == 1

        created = notify(module_seeded_url, user)

        frame = stream.next_event(DELIVERY_SECONDS)
        assert notification_id_of(frame) == str(created)
    assert hub.stream_count(user.id) == 0


# --- D2: isolation ----------------------------------------------------------------


def test_each_subscriber_receives_only_its_own_notifications(
    client: TestClient,
    hub: NotificationHub,
    module_seeded_url: str,
    user: CreatedUser,
    other: CreatedUser,
) -> None:
    cookie_a = signed_in(client, user.username)
    cookie_b = signed_in(client, other.username)
    wait_listening(client)
    with (
        open_stream(client, cookie_a) as stream_a,
        open_stream(client, cookie_b) as stream_b,
    ):
        opened(stream_a)
        opened(stream_b)
        for_a = [notify(module_seeded_url, user, f"A{n}") for n in range(3)]
        for_b = notify(module_seeded_url, other, "B")
        # A sentinel for each closes their sequence: anything foreign would
        # arrive before it.
        sentinel_a = notify(module_seeded_url, user, "end")
        sentinel_b = notify(module_seeded_url, other, "end")

        received_a = [notification_id_of(stream_a.next_event()) for _ in range(4)]
        received_b = [notification_id_of(stream_b.next_event()) for _ in range(2)]

    assert received_a == [str(i) for i in (*for_a, sentinel_a)]
    assert received_b == [str(for_b), str(sentinel_b)]


# --- D3: heartbeat ----------------------------------------------------------------


def test_heartbeats_arrive_without_notifications(
    client: TestClient, hub: NotificationHub, user: CreatedUser
) -> None:
    hub.heartbeat_seconds = 0.2
    cookie = signed_in(client, user.username)
    with open_stream(client, cookie) as stream:
        opened(stream)
        beats = [stream.next_frame(2.0) for _ in range(2)]
    assert beats == [SseFrame(comments=("heartbeat",))] * 2


# --- C3, D4: Last-Event-ID --------------------------------------------------------


def test_last_event_id_replays_the_later_notifications_oldest_first(
    client: TestClient, hub: NotificationHub, module_seeded_url: str, user: CreatedUser
) -> None:
    n1, n2, n3 = (notify(module_seeded_url, user, f"N{n}") for n in range(3))
    cookie = signed_in(client, user.username)
    with open_stream(client, cookie, **{"Last-Event-ID": str(n1)}) as stream:
        opened(stream)
        replayed = [notification_id_of(stream.next_event()) for _ in range(2)]
    assert replayed == [str(n2), str(n3)]


def test_last_event_id_of_the_newest_replays_nothing(
    client: TestClient, hub: NotificationHub, module_seeded_url: str, user: CreatedUser
) -> None:
    hub.heartbeat_seconds = 0.1
    newest = notify(module_seeded_url, user)
    cookie = signed_in(client, user.username)
    with open_stream(client, cookie, **{"Last-Event-ID": str(newest)}) as stream:
        opened(stream)
        # Nothing to replay: the next frame is already a heartbeat.
        assert stream.next_frame(2.0).is_comment


@pytest.mark.parametrize("whose", ["other user's", "unknown", "not a uuid"])
def test_a_foreign_unknown_or_invalid_last_event_id_gives_resync_only(
    client: TestClient,
    hub: NotificationHub,
    module_seeded_url: str,
    user: CreatedUser,
    other: CreatedUser,
    whose: str,
) -> None:
    hub.heartbeat_seconds = 0.1
    notify(module_seeded_url, user)  # the caller has notifications
    foreign = notify(module_seeded_url, other)
    last_id = {
        "other user's": str(foreign),
        "unknown": str(uuid.uuid4()),
        "not a uuid": "nope",
    }[whose]
    cookie = signed_in(client, user.username)
    with open_stream(client, cookie, **{"Last-Event-ID": last_id}) as stream:
        opened(stream)
        assert is_resync(stream.next_frame())
        assert stream.next_frame(2.0).is_comment


@pytest.mark.parametrize("later", [100, 101])
def test_up_to_100_later_notifications_replay_and_more_give_one_resync(
    client: TestClient,
    hub: NotificationHub,
    module_seeded_url: str,
    user: CreatedUser,
    later: int,
) -> None:
    """The boundary exactly: 100 later ones are all replayed, 101 are not."""
    hub.heartbeat_seconds = 0.2
    first = notify(module_seeded_url, user)
    created: list[uuid.UUID] = []

    async def many(session: AsyncSession) -> None:
        for n in range(later):
            created.append(uuid.UUID(str(await create_for(user, f"M{n}")(session))))

    in_uow(module_seeded_url, many)
    cookie = signed_in(client, user.username)
    with open_stream(client, cookie, **{"Last-Event-ID": str(first)}) as stream:
        opened(stream)
        if later <= 100:
            replayed = [notification_id_of(stream.next_event()) for _ in range(later)]
            assert replayed == [str(n) for n in created]
        else:
            assert is_resync(stream.next_frame())
        # Nothing else: the next frame is a heartbeat.
        assert stream.next_frame(2.0).is_comment


# --- B2, D5: the listener reconnects -----------------------------------------------


def test_the_listener_reconnects_after_its_backend_is_terminated(
    client: TestClient, hub: NotificationHub, module_seeded_url: str, user: CreatedUser
) -> None:
    hub.heartbeat_seconds = 30.0
    cookie = signed_in(client, user.username)
    wait_listening(client)
    listener = listener_of(client)
    pid = listener.backend_pid
    assert pid is not None
    with open_stream(client, cookie) as stream:
        opened(stream)
        run_sql(module_seeded_url, "SELECT pg_terminate_backend(:pid)", {"pid": pid})

        # The reconnect (backoff 1 s) tells every subscriber it may have
        # missed events.
        assert is_resync(stream.next_event(10.0))
        assert listener.backend_pid not in (None, pid)

        created = notify(module_seeded_url, user)
        assert notification_id_of(stream.next_event(DELIVERY_SECONDS)) == str(created)


# --- C4, D6: the stream never keeps a session alive --------------------------------


def last_seen(url: str, user: CreatedUser) -> Any:
    [row] = run_sql(
        url,
        "SELECT last_seen_at FROM auth_sessions WHERE user_id = :id"
        " AND revoked_at IS NULL",
        {"id": user.id},
    )
    return row[0]


def test_the_stream_never_refreshes_last_seen_at(
    client: TestClient,
    hub: NotificationHub,
    module_seeded_url: str,
    user: CreatedUser,
    clock: Any,
) -> None:
    hub.heartbeat_seconds = 0.05
    hub.session_check_seconds = 0.1
    cookie = signed_in(client, user.username)
    before = last_seen(module_seeded_url, user)
    # Long enough that any other request would refresh it (60 s throttle).
    clock.advance(timedelta(minutes=5))
    with open_stream(client, cookie) as stream:
        opened(stream)
        beats = [stream.next_frame(2.0) for _ in range(4)]
        assert all(beat.is_comment for beat in beats)
        assert not stream.ended
    assert last_seen(module_seeded_url, user) == before
    # The control: a regular request does refresh it.
    assert code_of(me(client, cookie)) == (200, None)
    assert last_seen(module_seeded_url, user) > before


@pytest.mark.parametrize(
    "ending", ["revoked", "expired", "logged out", "suspended, not revoked"]
)
def test_an_ended_session_closes_the_stream(
    client: TestClient,
    hub: NotificationHub,
    module_seeded_url: str,
    user: CreatedUser,
    clock: Any,
    ending: str,
) -> None:
    hub.heartbeat_seconds = 0.05
    hub.session_check_seconds = 0.1
    cookie = signed_in(client, user.username)
    with open_stream(client, cookie) as stream:
        opened(stream)
        assert not ended_within(stream, 0.3)
        if ending == "revoked":
            in_uow(
                module_seeded_url,
                lambda session: sessions.revoke_user_sessions(session, user.id),
            )
        elif ending == "expired":
            # Past the staff idle limit (30 minutes).
            clock.advance(timedelta(minutes=31))
        elif ending == "suspended, not revoked":
            # Only the state changes (the lifecycle service would also
            # revoke): the session check still ends the stream (D-c).
            run_sql(
                module_seeded_url,
                "UPDATE users SET state = 'suspended' WHERE id = :id",
                {"id": user.id},
            )
        else:
            client.cookies.clear()
            response = client.post(
                "/api/v1/auth/logout",
                headers={
                    **cookie_header(cookie),
                    "X-CSRF-Token": csrf_for(cookie),
                },
            )
            assert response.status_code == 204, response.text
        assert ended_within(stream, 3.0)
    assert hub.stream_count(user.id) == 0


# --- D7: refusals -----------------------------------------------------------------


def test_anonymous_gets_401(client: TestClient) -> None:
    with open_stream(client, None) as stream:
        response = stream.response()
    assert code_of(response) == (401, "authentication_required")


def test_a_user_who_has_not_accepted_the_notice_gets_403(
    client: TestClient, module_seeded_url: str
) -> None:
    pending = create_user(
        module_seeded_url, roles=(OWNER,), privacy_notice_accepted=False
    )
    cookie = signed_in(client, pending.username)
    with open_stream(client, cookie) as stream:
        response = stream.response()
    assert code_of(response) == (403, "privacy_notice_required")


def test_the_eleventh_stream_gets_429_and_a_closed_one_frees_a_place(
    client: TestClient, hub: NotificationHub, user: CreatedUser
) -> None:
    cookie = signed_in(client, user.username)
    streams = [open_stream(client, cookie) for _ in range(MAX_STREAMS_PER_USER)]
    try:
        for stream in streams:
            opened(stream.__enter__())
        with open_stream(client, cookie) as eleventh:
            response = eleventh.response()
        assert code_of(response) == (429, "too_many_streams")
        assert response.headers["retry-after"] == "30"
        assert response.headers["content-type"] == "application/problem+json"

        streams.pop().close()
        with open_stream(client, cookie) as again:
            opened(again)
    finally:
        for stream in streams:
            stream.close()
    assert hub.stream_count(user.id) == 0


def test_a_rolled_back_notification_never_reaches_the_stream(
    client: TestClient, hub: NotificationHub, module_seeded_url: str, user: CreatedUser
) -> None:
    hub.heartbeat_seconds = 30.0
    cookie = signed_in(client, user.username)
    wait_listening(client)
    with open_stream(client, cookie) as stream:
        opened(stream)
        notify_rolled_back(module_seeded_url, user)
        sentinel = notify(module_seeded_url, user)
        assert notification_id_of(stream.next_event(DELIVERY_SECONDS)) == str(sentinel)
