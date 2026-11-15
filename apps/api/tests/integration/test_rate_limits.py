"""Rate limits on login and reauthentication (#27 B3, B4, B7, D1-D3) with
real logins, a Testcontainers database and the ``clock`` fixture.

``TestClient`` requests come from the peer ``testclient``, which is not an IP
address, so they all share the ``unknown`` IP key; the tests that need
distinct addresses build their client with ``client=(address, port)``.
"""

import asyncio
import ipaddress
import logging
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Annotated, Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from httpx import Response

from app.core.authz import public
from app.core.config import get_settings
from app.core.db import create_sessionmaker
from app.core.ratelimit import (
    RATE_LIMIT_RESPONSES,
    RateLimiter,
    Reservation,
    key_hash,
    rate_limit,
)
from app.core.uow import UnitOfWork, UnitOfWorkDep
from app.main import build_app, lifespan
from app.modules.identity import passwords, sessions
from app.modules.identity.permissions import OWNER
from tests.conftest import CLOCK_START
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    LOGIN,
    ME,
    cookie_header,
    create_user,
    login,
    signed_headers,
    token_of,
)
from tests.integration.conftest import (
    reset_rate_limits,
    run_sql,
    settings_for,
    short_lived_engine,
)
from tests.integration.lifecycle_support import (
    code_of,
    events,
    no_secret_in_audit,
    signed_in,
)

REAUTHENTICATE = "/api/v1/auth/reauthenticate"
WRONG = "not-the-password"
PEER = "203.0.113.5"
OTHER_PEER = "203.0.113.6"
WINDOW = timedelta(minutes=15)
UNAUTHORIZED = (401, "invalid_credentials")
LIMITED = (429, "rate_limited")
REAUTH_FAILED = (403, "reauth_failed")


@contextmanager
def limited_client(
    url: str,
    *,
    peer: str = PEER,
    trusted: Iterable[str] = (),
    routers: Iterable[APIRouter] = (),
) -> Iterator[TestClient]:
    """The real app on ``url``; requests come from ``peer``."""
    app = build_app(lifespan=lifespan)
    for router in routers:
        app.include_router(router)
    settings = settings_for(url, session_cookie_secure=False).model_copy(
        update={"trusted_proxies": tuple(ipaddress.ip_network(t) for t in trusted)}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(
        app,
        raise_server_exceptions=False,
        headers={"Origin": settings.app_origin},
        client=(peer, 50000),
    ) as client:
        yield client


@pytest.fixture
def verifications(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every Argon2id verification the app runs (the #23 D5 spy)."""
    calls: list[str] = []
    real = passwords.verify_password

    def spy(stored: str, password: str) -> bool:
        calls.append(password)
        return real(stored, password)

    monkeypatch.setattr(passwords, "verify_password", spy)
    return calls


def attempt(client: TestClient, username: str, password: str = WRONG) -> Response:
    client.cookies.clear()
    response = login(client, username, password)
    client.cookies.clear()
    return response


def counters(url: str, policy: str | None = None) -> list[Any]:
    sql = (
        "SELECT policy, key_hash, window_start, window_end, count"
        " FROM rate_limit_counters"
    )
    if policy is None:
        return run_sql(url, sql + " ORDER BY policy, count")
    return run_sql(url, sql + " WHERE policy = :p ORDER BY count", {"p": policy})


def count_of(
    url: str, policy: str, key: str, window_start: datetime = CLOCK_START
) -> int | None:
    rows = run_sql(
        url,
        "SELECT count FROM rate_limit_counters"
        " WHERE policy = :p AND key_hash = :h AND window_start = :s",
        {"p": policy, "h": key_hash(policy, key), "s": window_start},
    )
    return rows[0].count if rows else None


def without_request_id(response: Response) -> dict[str, Any]:
    body = dict(response.json())
    body.pop("request_id")
    return body


# --- D3: the username limit -----------------------------------------------------------


def test_eleventh_attempt_is_429_even_with_the_right_password(
    seeded_url, clock, verifications
):
    user = create_user(seeded_url)
    with limited_client(seeded_url) as client:
        codes = [code_of(attempt(client, user.username)) for _ in range(10)]
        assert codes == [UNAUTHORIZED] * 10
        assert len(verifications) == 10

        eleventh = attempt(client, user.username, TEST_USER_PASSWORD)

        assert code_of(eleventh) == LIMITED
        assert eleventh.headers["retry-after"] == "900"
        assert eleventh.headers["content-type"] == "application/problem+json"
        assert len(verifications) == 10
        assert "set-cookie" not in eleventh.headers

        # The window ends at 12:15; one second before, still limited.
        clock.advance(WINDOW - timedelta(seconds=1))
        last = attempt(client, user.username, TEST_USER_PASSWORD)
        assert code_of(last) == LIMITED
        assert last.headers["retry-after"] == "1"
        clock.advance(timedelta(seconds=1))
        assert attempt(client, user.username, TEST_USER_PASSWORD).status_code == 200
    assert run_sql(seeded_url, "SELECT count(*) FROM auth_sessions")[0][0] == 1


def test_a_429_does_not_reveal_whether_the_account_exists(seeded_url, clock):
    user = create_user(seeded_url)
    with limited_client(seeded_url) as client:
        for _ in range(10):
            attempt(client, user.username)
            attempt(client, "nobody.here")
        clock.advance(timedelta(minutes=4, seconds=17))

        existing = attempt(client, user.username, TEST_USER_PASSWORD)
        unknown = attempt(client, "nobody.here", TEST_USER_PASSWORD)

    assert code_of(existing) == code_of(unknown) == LIMITED
    assert without_request_id(existing) == without_request_id(unknown)
    assert set(existing.headers) - {"content-length"} == set(unknown.headers) - {
        "content-length"
    }
    assert existing.headers["retry-after"] == unknown.headers["retry-after"] == "643"
    assert user.username not in existing.text


def test_the_username_is_counted_normalized(seeded_url, clock):
    create_user(seeded_url, username="demo.user")
    with limited_client(seeded_url) as client:
        for raw in ["Demo.User", " demo.user ", "demo.user", "DEMO.USER"] * 2:
            assert code_of(attempt(client, raw)) == UNAUTHORIZED
        attempt(client, "Demo.User ")
        attempt(client, "demo.USER")

        assert code_of(attempt(client, "demo.user", TEST_USER_PASSWORD)) == LIMITED
        assert count_of(seeded_url, "login.username", "demo.user") == 10


def test_an_invalid_username_is_counted_under_its_own_key(seeded_url, clock):
    # "K" (Kelvin sign) lower-cases to "k" but is rejected as non-ASCII.
    create_user(seeded_url, username="kim.user")
    invalid = "Kim.user"
    with limited_client(seeded_url) as client:
        for _ in range(10):
            assert code_of(attempt(client, invalid)) == UNAUTHORIZED
        assert code_of(attempt(client, f" {invalid} ")) == LIMITED
        assert code_of(attempt(client, "not valid!")) == UNAUTHORIZED

        assert attempt(client, "kim.user", TEST_USER_PASSWORD).status_code == 200
    assert count_of(seeded_url, "login.username", "kim.user") == 0
    assert count_of(seeded_url, "login.username", "!Kim.user".lower()) == 10
    assert count_of(seeded_url, "login.username", "!not valid!") == 1


def test_a_success_releases_only_its_own_reservation(seeded_url, clock):
    user = create_user(seeded_url)
    with limited_client(seeded_url) as client:
        for _ in range(9):
            attempt(client, user.username)
        assert attempt(client, user.username, TEST_USER_PASSWORD).status_code == 200
        assert count_of(seeded_url, "login.username", user.username) == 9

        assert code_of(attempt(client, user.username)) == UNAUTHORIZED
        assert code_of(attempt(client, user.username)) == LIMITED


def test_the_count_is_committed_before_the_401(seeded_url, clock):
    user = create_user(seeded_url)
    with limited_client(seeded_url) as client:
        assert code_of(attempt(client, user.username)) == UNAUTHORIZED

    # Read with a separate connection, after the request's Unit of Work rolled back.
    assert count_of(seeded_url, "login.username", user.username) == 1
    assert count_of(seeded_url, "login.ip", PEER) == 1


def test_a_validation_error_still_counts_against_the_ip(seeded_url, clock):
    with limited_client(seeded_url) as client:
        response = client.post(LOGIN, json={})

    assert response.status_code == 422
    assert count_of(seeded_url, "login.ip", PEER) == 1
    assert counters(seeded_url, "login.username") == []


# --- D3: the IP limit -----------------------------------------------------------------


def test_fifty_logins_per_ip_then_429_for_any_username(seeded_url, clock):
    users = [create_user(seeded_url) for _ in range(5)]
    with limited_client(seeded_url) as client:
        codes = []
        for index in range(50):
            if index % 10 == 0:
                user = users[index // 10]
                codes.append(attempt(client, user.username, TEST_USER_PASSWORD))
            else:
                codes.append(attempt(client, f"someone.{index}"))
        assert [r.status_code for r in codes].count(200) == 5
        assert [r.status_code for r in codes].count(401) == 45

        for username in (users[0].username, "fresh.name"):
            response = attempt(client, username, TEST_USER_PASSWORD)
            assert code_of(response) == LIMITED
            assert response.headers["retry-after"] == "900"

    # Another address (here through another app instance on the same database).
    with limited_client(seeded_url, peer=OTHER_PEER) as other:
        assert attempt(other, users[0].username, TEST_USER_PASSWORD).status_code == 200
    assert count_of(seeded_url, "login.ip", PEER) == 50
    assert count_of(seeded_url, "login.ip", OTHER_PEER) == 1
    # The 429s took no username reservation.
    assert count_of(seeded_url, "login.username", "fresh.name") is None


def test_an_ipv6_client_is_counted_by_its_64(seeded_url, clock):
    with limited_client(seeded_url, peer="2001:db8:1:2::10") as client:
        for _ in range(50):
            client.post(LOGIN, json={})
    with limited_client(seeded_url, peer="2001:db8:1:2:ffff::1") as neighbour:
        assert code_of(neighbour.post(LOGIN, json={})) == LIMITED
    with limited_client(seeded_url, peer="2001:db8:1:3::1") as other_network:
        assert other_network.post(LOGIN, json={}).status_code == 422
    assert count_of(seeded_url, "login.ip", "2001:db8:1:2::/64") == 50


def test_a_spoofed_forwarded_for_is_ignored_without_trusted_proxies(seeded_url, clock):
    with limited_client(seeded_url) as client:
        for index in range(50):
            response = client.post(
                LOGIN, json={}, headers={"X-Forwarded-For": f"198.51.100.{index}"}
            )
            assert response.status_code == 422

        response = client.post(
            LOGIN, json={}, headers={"X-Forwarded-For": "198.51.100.200"}
        )

    assert code_of(response) == LIMITED
    assert [row.count for row in counters(seeded_url, "login.ip")] == [50]
    assert count_of(seeded_url, "login.ip", PEER) == 50


def test_a_trusted_proxy_gives_each_forwarded_client_its_own_counter(seeded_url, clock):
    with limited_client(seeded_url, trusted=[PEER]) as client:
        for _ in range(50):
            client.post(
                LOGIN,
                json={},
                headers={"X-Forwarded-For": "1.1.1.1, 198.51.100.7"},
            )
        same = client.post(
            LOGIN, json={}, headers={"X-Forwarded-For": "2.2.2.2, 198.51.100.7"}
        )
        other = client.post(LOGIN, json={}, headers={"X-Forwarded-For": "198.51.100.8"})

    assert code_of(same) == LIMITED
    assert other.status_code == 422
    assert count_of(seeded_url, "login.ip", "198.51.100.7") == 50
    assert count_of(seeded_url, "login.ip", "198.51.100.8") == 1
    assert count_of(seeded_url, "login.ip", PEER) is None


# --- D1: the order --------------------------------------------------------------------


def test_a_foreign_origin_is_refused_before_the_limiter(seeded_url, clock):
    with limited_client(seeded_url) as client:
        for _ in range(50):
            client.post(LOGIN, json={})
        before = counters(seeded_url)

        response = client.post(
            LOGIN,
            json={"username": "fresh.name", "password": WRONG},
            headers={"Origin": "http://evil.example"},
        )

    assert code_of(response) == (403, "csrf_failed")
    assert counters(seeded_url) == before


def test_the_ip_limit_comes_before_validation(seeded_url, clock):
    with limited_client(seeded_url) as client:
        for _ in range(50):
            client.post(LOGIN, json={})

        response = client.post(LOGIN, json={"username": ""})

    assert code_of(response) == LIMITED


# --- D2: reauthentication -------------------------------------------------------------


def reauthenticate(client: TestClient, cookie: Any, password: str) -> Response:
    client.cookies.clear()
    return client.post(
        REAUTHENTICATE, json={"password": password}, headers=signed_headers(cookie)
    )


def reauthenticated_at(url: str, cookie: Any) -> Any:
    [row] = run_sql(
        url,
        "SELECT reauthenticated_at, revoked_at FROM auth_sessions"
        " WHERE token_hash = :h",
        {"h": sessions.hash_token(token_of(cookie))},
    )
    return row


def test_the_sixth_wrong_reauthentication_is_429(seeded_url, clock, verifications):
    owner = create_user(seeded_url, roles=(OWNER,))
    other = create_user(seeded_url, roles=(OWNER,))
    with limited_client(seeded_url) as client:
        cookie = signed_in(client, owner.username)
        other_cookie = signed_in(client, other.username)
        assert reauthenticate(client, cookie, TEST_USER_PASSWORD).status_code == 200
        window = reauthenticated_at(seeded_url, cookie).reauthenticated_at
        clock.advance(timedelta(minutes=1))
        verifications.clear()

        codes = [code_of(reauthenticate(client, cookie, WRONG)) for _ in range(5)]
        assert codes == [REAUTH_FAILED] * 5
        sixth = reauthenticate(client, cookie, TEST_USER_PASSWORD)

        assert code_of(sixth) == LIMITED
        assert sixth.headers["retry-after"] == str(14 * 60)
        assert len(verifications) == 5
        failed = events(seeded_url, action="identity.reauth_failed")
        assert len(failed) == 5
        row = reauthenticated_at(seeded_url, cookie)
        assert (row.reauthenticated_at, row.revoked_at) == (window, None)
        client.cookies.clear()
        assert client.get(ME, headers=cookie_header(cookie)).status_code == 200
        # Another user of the same IP is not affected.
        assert code_of(reauthenticate(client, other_cookie, WRONG)) == REAUTH_FAILED

        clock.advance(timedelta(minutes=14))
        assert reauthenticate(client, cookie, TEST_USER_PASSWORD).status_code == 200
    assert count_of(seeded_url, "reauth.user", str(owner.id)) == 5
    next_window = CLOCK_START + WINDOW
    assert count_of(seeded_url, "reauth.user", str(owner.id), next_window) == 0


def test_a_successful_reauthentication_releases_its_reservation(seeded_url, clock):
    owner = create_user(seeded_url, roles=(OWNER,))
    with limited_client(seeded_url) as client:
        cookie = signed_in(client, owner.username)
        for _ in range(4):
            reauthenticate(client, cookie, WRONG)
        assert reauthenticate(client, cookie, TEST_USER_PASSWORD).status_code == 200
        assert count_of(seeded_url, "reauth.user", str(owner.id)) == 4

        assert code_of(reauthenticate(client, cookie, WRONG)) == REAUTH_FAILED
        assert code_of(reauthenticate(client, cookie, WRONG)) == LIMITED


def test_reauthentication_keeps_the_activity_update(seeded_url, clock):
    """The session lookup's ``last_seen_at`` update is committed before the
    counter's own transaction (#25 activity, one connection at a time)."""
    owner = create_user(seeded_url, roles=(OWNER,))
    with limited_client(seeded_url) as client:
        cookie = signed_in(client, owner.username)
        clock.advance(timedelta(minutes=10))

        assert code_of(reauthenticate(client, cookie, WRONG)) == REAUTH_FAILED

    [row] = run_sql(seeded_url, "SELECT last_seen_at FROM auth_sessions")
    assert row.last_seen_at == CLOCK_START + timedelta(minutes=10)


# --- B7: log lines and no audit -------------------------------------------------------


def test_each_429_logs_one_security_warning_without_personal_data(
    seeded_url, clock, caplog
):
    user = create_user(seeded_url, roles=(OWNER,))
    secret = "Sup3r-secret-guess"
    with limited_client(seeded_url) as client:
        cookie = signed_in(client, user.username)
        for _ in range(10):
            attempt(client, user.username, secret)
        for _ in range(5):
            reauthenticate(client, cookie, secret)
        audit_before = run_sql(seeded_url, "SELECT count(*) FROM audit_events")[0][0]
        caplog.clear()
        caplog.set_level(logging.WARNING, logger="app.security")

        assert code_of(attempt(client, user.username, secret)) == LIMITED
        assert code_of(reauthenticate(client, cookie, secret)) == LIMITED

    records = [r for r in caplog.records if r.name == "app.security"]
    assert [(r.levelno, r.policy, r.method, r.path) for r in records] == [  # type: ignore[attr-defined]
        (logging.WARNING, "login.username", "POST", LOGIN),
        (logging.WARNING, "reauth.user", "POST", REAUTHENTICATE),
    ]
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in records)
    for value in (user.username, str(user.id), PEER, secret):
        assert value not in text
    assert key_hash("login.username", user.username).hex() not in text
    assert (
        run_sql(seeded_url, "SELECT count(*) FROM audit_events")[0][0] == audit_before
    )
    no_secret_in_audit(seeded_url, secret)


# --- B4: the reservation and the one-connection rule ----------------------------------

guard_router = APIRouter()
GUARD = "/test-only/rate-limit/open-transaction"


@guard_router.post(
    GUARD,
    responses=RATE_LIMIT_RESPONSES,
    dependencies=[public(reason="test-only")],
)
async def open_transaction(
    limiter: Annotated[RateLimiter, rate_limit("login.ip", "login.username")],
    uow: UnitOfWorkDep,
) -> None:
    """Reserves while the request's transaction holds a connection (wrong)."""
    await uow.session.connection()
    await limiter.reserve("login.username", "someone")


def test_reserving_with_an_open_request_transaction_is_refused(seeded_url, clock):
    with limited_client(seeded_url, routers=[guard_router]) as client:
        response = client.post(GUARD)

    assert code_of(response) == (500, "internal_error")
    assert counters(seeded_url, "login.username") == []


def test_a_release_never_goes_below_zero(seeded_url, clock):
    digest = key_hash("login.username", "someone")
    run_sql(
        seeded_url,
        "INSERT INTO rate_limit_counters"
        " (policy, key_hash, window_start, window_end, count)"
        " VALUES ('login.username', :h, :s, :e, 0)",
        {"h": digest, "s": CLOCK_START, "e": CLOCK_START + WINDOW},
    )
    reservation = Reservation("login.username", digest, CLOCK_START)

    async def release_twice() -> None:
        engine = short_lived_engine(seeded_url)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                await reservation.release(uow.session)
                await reservation.release(uow.session)
        finally:
            await engine.dispose()

    asyncio.run(release_twice())

    assert count_of(seeded_url, "login.username", "someone") == 0


def test_the_reset_helper_empties_the_counters(seeded_url, clock):
    with limited_client(seeded_url) as client:
        for _ in range(50):
            client.post(LOGIN, json={})
        assert client.post(LOGIN, json={}).status_code == 429

        reset_rate_limits(seeded_url)

        assert client.post(LOGIN, json={}).status_code == 422


def test_counter_rows_hold_no_key_in_clear_text(seeded_url, clock):
    user = create_user(seeded_url)
    with limited_client(seeded_url) as client:
        attempt(client, user.username)

    rows = run_sql(seeded_url, "SELECT * FROM rate_limit_counters")
    assert {row.policy for row in rows} == {"login.ip", "login.username"}
    text = " ".join(str(tuple(row)) for row in rows)
    assert user.username not in text and PEER not in text
    assert all(len(row.key_hash) == 32 for row in rows)
    assert all(row.window_start == CLOCK_START for row in rows)
    assert all(row.window_end == CLOCK_START + WINDOW for row in rows)
    assert all(isinstance(row.id, uuid.UUID) for row in rows)
