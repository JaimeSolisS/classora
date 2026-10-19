"""Concurrent failed logins and reauthentications through PgBouncer (#23 QA
Defect 1; #26 D4).

A failed login, and a failed reauthentication, must never hold two
connections at once: the request's transaction ends before the committed
``identity.login_failed`` / ``identity.reauth_failed`` event takes its own
connection. Otherwise more concurrent failures than PgBouncer's server
pool wait on each other until a pool timeout (about 30 s) and answer 500.
The test PgBouncer has ``PGBOUNCER_POOL_SIZE`` (5) server connections.

#27 adds rate-limit counters, each written in its own committed transaction:
the request's transaction always ends first, so a request still never holds
two connections. A burst of wrong passwords for one username gets exactly
``login.username``'s 10 verifications and ``429`` for the rest (D4), and
the reauthentication burst spreads its attempts over users so each stays
within ``reauth.user``'s 5.
"""

import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import make_url

from app.core.config import get_settings
from app.main import build_app, lifespan
from app.modules.identity import passwords
from tests.integration.auth_support import (
    create_user,
    login,
    only_set_cookie,
    signed_headers,
)
from tests.integration.conftest import PGBOUNCER_POOL_SIZE, run_sql, settings_for

CONCURRENT = PGBOUNCER_POOL_SIZE * 3
# Each request costs one Argon2id verification (the API runs them one at a
# time) plus a few queries; a pool deadlock would take about 30 s.
BOUND_SECONDS = 10.0


@pytest.fixture
def pooled_client(seeded_url: str, pgbouncer_url: str) -> Iterator[TestClient]:
    """The real app whose request engine goes through the test PgBouncer."""
    database = make_url(seeded_url).database
    pooled = make_url(pgbouncer_url).set(database=database)
    settings = settings_for(
        pooled.render_as_string(hide_password=False), direct_dsn=seeded_url
    )
    app = build_app(lifespan=lifespan)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_concurrent_failed_logins_never_exhaust_the_pool(seeded_url, pooled_client):
    user = create_user(seeded_url)
    attempts = [
        (user.username, "wrong-password-1") if i % 2 else ("nobody.here", "wrong-1")
        for i in range(CONCURRENT)
    ]

    def attempt(credentials: tuple[str, str]) -> tuple[int, str | None, float]:
        started = time.monotonic()
        response = login(pooled_client, *credentials)
        code = response.json().get("code") if response.content else None
        return response.status_code, code, time.monotonic() - started

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=CONCURRENT) as pool:
        results = list(pool.map(attempt, attempts))
    elapsed = time.monotonic() - started

    assert [(status, code) for status, code, _ in results] == [
        (401, "invalid_credentials")
    ] * CONCURRENT
    assert elapsed < BOUND_SECONDS, f"{CONCURRENT} failed logins took {elapsed:.1f}s"
    rows = run_sql(
        seeded_url,
        "SELECT details->>'reason' AS reason, count(*) AS n FROM audit_events"
        " WHERE action = 'identity.login_failed' GROUP BY 1",
    )
    assert {row.reason: row.n for row in rows} == {
        "invalid_password": CONCURRENT // 2,
        "unknown_username": CONCURRENT - CONCURRENT // 2,
    }
    assert run_sql(seeded_url, "SELECT count(*) FROM auth_sessions")[0][0] == 0


def test_concurrent_failed_reauthentications_never_exhaust_the_pool(
    seeded_url, pooled_client
):
    # reauth.user allows 5 wrong passwords per user (#27): 3 users x 5 sessions.
    users = [create_user(seeded_url, roles=("owner",)) for _ in range(3)]
    cookies = []
    for index in range(CONCURRENT):
        response = login(pooled_client, users[index % len(users)].username)
        assert response.status_code == 200
        cookies.append(only_set_cookie(response))
    pooled_client.cookies.clear()

    def attempt(index: int) -> tuple[int, str | None]:
        response = pooled_client.post(
            "/api/v1/auth/reauthenticate",
            json={"password": f"wrong-password-{index}"},
            headers=signed_headers(cookies[index]),
        )
        code = response.json().get("code") if response.content else None
        return response.status_code, code

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=CONCURRENT) as pool:
        results = list(pool.map(attempt, range(CONCURRENT)))
    elapsed = time.monotonic() - started

    assert results == [(403, "reauth_failed")] * CONCURRENT
    assert elapsed < BOUND_SECONDS, (
        f"{CONCURRENT} failed reauthentications took {elapsed:.1f}s"
    )
    [row] = run_sql(
        seeded_url,
        "SELECT count(*) AS n, count(DISTINCT details->>'auth_session_id') AS s"
        " FROM audit_events WHERE action = 'identity.reauth_failed'",
    )
    assert (row.n, row.s) == (CONCURRENT, CONCURRENT)
    marked = run_sql(
        seeded_url,
        "SELECT count(*) FROM auth_sessions WHERE reauthenticated_at IS NOT NULL",
    )
    assert marked[0][0] == 0


# --- #27 D4: one username, more wrong passwords than its limit ------------------------

BURST = 24
USERNAME_LIMIT = 10


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


def test_a_burst_for_one_username_gets_exactly_the_limit_of_verifications(
    seeded_url, pooled_client, verifications
):
    user = create_user(seeded_url)

    def attempt(index: int) -> tuple[int, str | None]:
        response = login(pooled_client, user.username, f"wrong-password-{index}")
        code = response.json().get("code") if response.content else None
        return response.status_code, code

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=BURST) as pool:
        results = list(pool.map(attempt, range(BURST)))
    elapsed = time.monotonic() - started

    assert sorted(results) == sorted(
        [(401, "invalid_credentials")] * USERNAME_LIMIT
        + [(429, "rate_limited")] * (BURST - USERNAME_LIMIT)
    )
    assert len(verifications) == USERNAME_LIMIT
    assert elapsed < BOUND_SECONDS, f"{BURST} logins took {elapsed:.1f}s"
    [row] = run_sql(
        seeded_url,
        "SELECT (SELECT count(*) FROM audit_events"
        "        WHERE action = 'identity.login_failed') AS failed,"
        " (SELECT count FROM rate_limit_counters WHERE policy = 'login.username')"
        " AS username, (SELECT count FROM rate_limit_counters"
        "               WHERE policy = 'login.ip') AS ip",
    )
    assert (row.failed, row.username, row.ip) == (USERNAME_LIMIT, USERNAME_LIMIT, BURST)
    assert run_sql(seeded_url, "SELECT count(*) FROM auth_sessions")[0][0] == 0
