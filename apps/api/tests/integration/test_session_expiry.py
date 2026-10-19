"""Session limits at login, expiry, ``session_expired`` and the throttled
``last_seen_at`` (#25 B4, C, D) with the ``clock`` fixture."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.modules.identity.permissions import (
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.conftest import CLOCK_START
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    LOGIN,
    LOGOUT,
    CreatedUser,
    auth_client,
    cookie_header,
    create_user,
    only_set_cookie,
    signed_headers,
    token_of,
)
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import (
    FAIL,
    code_of,
    me,
    ping_router,
    post_ping,
    session_rows,
    signed_in,
)

OK = (200, None)
EXPIRED = (401, "session_expired")
UNAUTHENTICATED = (401, "authentication_required")

STAFF_IDLE = timedelta(minutes=30)
EXTENDED_IDLE = timedelta(days=7)
SECOND = timedelta(seconds=1)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url, routers=[ping_router]) as test_client:
        yield test_client


def user(url: str, *roles: str) -> CreatedUser:
    return create_user(url, roles=roles)


# --- B4: the stored limits ----------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "idle", "absolute"),
    [
        ((OWNER,), 1800, timedelta(hours=12)),
        ((TUTOR,), 604800, timedelta(days=30)),
        ((TUTOR, GUARDIAN), 604800, timedelta(days=30)),
        ((TUTOR, FINANCE_STAFF), 1800, timedelta(hours=12)),
        ((STUDENT,), 604800, timedelta(days=30)),
    ],
    ids=["owner", "tutor", "tutor+guardian", "tutor+finance_staff", "student"],
)
def test_login_stores_the_limits_of_the_role_class(
    module_seeded_url, client, clock, roles, idle, absolute
):
    someone = user(module_seeded_url, *roles)

    signed_in(client, someone.username)

    [row] = session_rows(module_seeded_url, someone.id)
    assert row.last_seen_at == CLOCK_START
    assert row.expires_at == CLOCK_START + absolute
    assert row.idle_timeout_seconds == idle
    assert row.revoked_at is None


# --- C6: idle limits (one session per check, so a check is not activity) ---------


def check_idle(client, url, someone, idle, clock) -> None:
    fine_me, fine_ping, late_me, late_ping = (
        signed_in(client, someone.username) for _ in range(4)
    )

    clock.set(CLOCK_START + idle - SECOND)
    assert code_of(me(client, fine_me)) == OK
    assert code_of(post_ping(client, fine_ping)) == OK

    clock.set(CLOCK_START + idle)
    assert code_of(me(client, late_me)) == EXPIRED
    assert code_of(post_ping(client, late_ping)) == EXPIRED


def test_staff_idle_limit(module_seeded_url, client, clock):
    check_idle(
        client, module_seeded_url, user(module_seeded_url, OWNER), STAFF_IDLE, clock
    )


def test_extended_idle_limit(module_seeded_url, client, clock):
    check_idle(
        client, module_seeded_url, user(module_seeded_url, TUTOR), EXTENDED_IDLE, clock
    )


def test_a_tutor_who_is_also_finance_staff_gets_the_staff_idle_limit(
    module_seeded_url, client, clock
):
    check_idle(
        client,
        module_seeded_url,
        user(module_seeded_url, TUTOR, FINANCE_STAFF),
        STAFF_IDLE,
        clock,
    )


# --- C6: absolute limits ------------------------------------------------------------


def test_staff_absolute_limit_despite_activity_every_ten_minutes(
    module_seeded_url, client, clock
):
    owner = user(module_seeded_url, OWNER)
    cookie = signed_in(client, owner.username)
    limit = timedelta(hours=12)

    step = 0
    while clock.now() + timedelta(minutes=10) < CLOCK_START + limit:
        clock.advance(timedelta(minutes=10))
        call = me if step % 2 == 0 else post_ping
        assert code_of(call(client, cookie)) == OK, clock.now()
        step += 1
    clock.set(CLOCK_START + limit - SECOND)
    assert code_of(me(client, cookie)) == OK
    assert code_of(post_ping(client, cookie)) == OK

    clock.set(CLOCK_START + limit)
    assert code_of(me(client, cookie)) == EXPIRED
    assert code_of(post_ping(client, cookie)) == EXPIRED


def test_extended_absolute_limit_despite_activity_every_day(
    module_seeded_url, client, clock
):
    tutor = user(module_seeded_url, TUTOR)
    cookie = signed_in(client, tutor.username)

    for day in range(1, 30):
        clock.set(CLOCK_START + timedelta(days=day))
        assert code_of(me(client, cookie)) == OK, day
    clock.set(CLOCK_START + timedelta(days=30) - SECOND)
    assert code_of(post_ping(client, cookie)) == OK

    clock.set(CLOCK_START + timedelta(days=30))
    assert code_of(me(client, cookie)) == EXPIRED
    assert code_of(post_ping(client, cookie)) == EXPIRED


# --- C3: the order ------------------------------------------------------------------


def test_revoked_beats_expired(module_seeded_url, client, clock):
    owner = user(module_seeded_url, OWNER)
    cookie = signed_in(client, owner.username)
    client.cookies.clear()
    assert client.post(LOGOUT, headers=signed_headers(cookie)).status_code == 204

    clock.advance(STAFF_IDLE)

    assert code_of(me(client, cookie)) == UNAUTHENTICATED
    assert code_of(post_ping(client, cookie)) == UNAUTHENTICATED


def test_expired_beats_a_missing_csrf_token(module_seeded_url, client, clock):
    owner = user(module_seeded_url, OWNER)
    live, late = signed_in(client, owner.username), signed_in(client, owner.username)
    assert code_of(post_ping(client, live, csrf=False)) == (403, "csrf_failed")

    clock.advance(STAFF_IDLE)

    assert code_of(post_ping(client, late, csrf=False)) == EXPIRED
    client.cookies.clear()
    logout = client.post(LOGOUT, headers=cookie_header(late))
    assert code_of(logout) == EXPIRED  # not 403 csrf_failed


def test_expired_beats_a_suspended_user(module_seeded_url, client, clock):
    owner = user(module_seeded_url, OWNER)
    cookie = signed_in(client, owner.username)
    # Suspended behind the service's back, so the session is not revoked.
    run_sql(
        module_seeded_url,
        "UPDATE users SET state = 'suspended' WHERE id = :id",
        {"id": owner.id},
    )
    assert code_of(me(client, cookie)) == UNAUTHENTICATED

    clock.advance(STAFF_IDLE)

    assert code_of(me(client, cookie)) == EXPIRED


# --- C4, C5: expired sessions stay expired; login and logout -----------------------


def test_an_expired_session_is_never_revived_and_sends_no_cookie(
    module_seeded_url, client, clock
):
    owner = user(module_seeded_url, OWNER)
    cookie = signed_in(client, owner.username)
    clock.advance(STAFF_IDLE)

    responses = [me(client, cookie), post_ping(client, cookie)]
    clock.advance(timedelta(minutes=5))
    responses += [me(client, cookie), post_ping(client, cookie)]

    assert [code_of(r) for r in responses] == [EXPIRED] * 4
    assert all("set-cookie" not in r.headers for r in responses)
    body = responses[0].json()
    assert body["detail"] == "Your session has expired. Sign in again."
    [row] = session_rows(module_seeded_url, owner.id)
    assert row.last_seen_at == CLOCK_START  # never touched
    assert row.revoked_at is None  # expired, not revoked


def test_login_over_an_expired_cookie_revokes_it_and_the_new_cookie_works(
    module_seeded_url, client, clock
):
    owner = user(module_seeded_url, OWNER)
    old = signed_in(client, owner.username)
    clock.advance(STAFF_IDLE)
    assert code_of(me(client, old)) == EXPIRED

    client.cookies.clear()
    response = client.post(
        LOGIN,
        json={"username": owner.username, "password": TEST_USER_PASSWORD},
        headers=cookie_header(old),
    )

    assert response.status_code == 200
    new = only_set_cookie(response)
    assert token_of(new) != token_of(old)
    old_row, new_row = session_rows(module_seeded_url, owner.id)
    assert old_row.revoked_at == CLOCK_START + STAFF_IDLE
    assert new_row.revoked_at is None
    assert new_row.last_seen_at == CLOCK_START + STAFF_IDLE
    assert code_of(me(client, new)) == OK
    assert code_of(me(client, old)) == UNAUTHENTICATED  # now revoked


# --- D: throttled last_seen_at ------------------------------------------------------


@pytest.fixture
def statements(client: TestClient) -> Iterator[list[str]]:
    """Every SQL statement the app's request engine runs during the test."""
    engine = client.app.state.engine.sync_engine  # type: ignore[attr-defined]
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def session_updates(statements: list[str]) -> list[str]:
    return [
        s for s in statements if s.lstrip().upper().startswith("UPDATE AUTH_SESSIONS")
    ]


def test_last_seen_at_is_written_at_most_once_a_minute(
    module_seeded_url, client, clock, statements
):
    owner = user(module_seeded_url, OWNER)
    fine, late = signed_in(client, owner.username), signed_in(client, owner.username)

    statements.clear()
    for at in (timedelta(0), timedelta(seconds=59)):
        clock.set(CLOCK_START + at)
        assert code_of(me(client, fine)) == OK
        assert code_of(post_ping(client, late)) == OK
    assert session_updates(statements) == []
    assert {row.last_seen_at for row in session_rows(module_seeded_url, owner.id)} == {
        CLOCK_START
    }

    clock.set(CLOCK_START + timedelta(seconds=60))
    assert code_of(me(client, fine)) == OK
    assert code_of(post_ping(client, late)) == OK
    assert len(session_updates(statements)) == 2
    assert {row.last_seen_at for row in session_rows(module_seeded_url, owner.id)} == {
        CLOCK_START + timedelta(seconds=60)
    }

    # The idle limit now counts from +60s.
    clock.set(CLOCK_START + timedelta(seconds=60) + STAFF_IDLE - SECOND)
    assert code_of(me(client, fine)) == OK
    clock.set(CLOCK_START + timedelta(seconds=60) + STAFF_IDLE)
    assert code_of(me(client, late)) == EXPIRED


def test_a_failed_request_does_not_record_activity(module_seeded_url, client, clock):
    owner = user(module_seeded_url, OWNER)
    cookie = signed_in(client, owner.username)
    clock.advance(timedelta(minutes=5))

    client.cookies.clear()
    response = client.post(FAIL, headers=signed_headers(cookie))

    assert code_of(response) == (409, "conflict")
    [row] = session_rows(module_seeded_url, owner.id)
    assert row.last_seen_at == CLOCK_START  # rolled back with the request
    assert code_of(me(client, cookie)) == OK
    [row] = session_rows(module_seeded_url, owner.id)
    assert row.last_seen_at == CLOCK_START + timedelta(minutes=5)
