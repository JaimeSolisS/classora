"""Concurrent failed logins through PgBouncer (#23 QA Defect 1).

A failed login must never hold two connections at once: the request's
transaction ends before the committed ``identity.login_failed`` event takes
its own connection. Otherwise more concurrent failures than PgBouncer's server
pool wait on each other until a pool timeout (about 30 s) and answer 500.
The test PgBouncer has ``PGBOUNCER_POOL_SIZE`` (5) server connections.
"""

import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import make_url

from app.core.config import get_settings
from app.main import build_app, lifespan
from tests.integration.auth_support import create_user, login
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
