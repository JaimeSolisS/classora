"""The general in-process API limit on the real app and database (#163 AC9).

``build_app(api_rate_limit_per_minute=N)`` with the test settings and the
``clock`` fixture. The client has its own peer address, and each app has its
own limiter, so nothing is shared with other tests.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import build_app, lifespan
from tests.integration.auth_support import LOGIN, ME, create_user
from tests.integration.conftest import run_sql, settings_for

LIMIT = 3
PEER = "198.51.100.163"


@contextmanager
def general_limit_client(url: str) -> Iterator[TestClient]:
    app = build_app(lifespan=lifespan, api_rate_limit_per_minute=LIMIT)
    settings = settings_for(url, session_cookie_secure=False)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(
        app,
        raise_server_exceptions=False,
        headers={"Origin": settings.app_origin},
        client=(PEER, 50000),
    ) as client:
        yield client


def counter_rows(url: str) -> int:
    return int(run_sql(url, "SELECT count(*) AS n FROM rate_limit_counters")[0].n)


def test_the_request_after_the_limit_is_429_while_the_probe_still_answers(
    seeded_url, clock
):
    with general_limit_client(seeded_url) as client:
        statuses = [client.get(ME).status_code for _ in range(LIMIT + 1)]
        refused = client.get(ME)
        live = client.get("/health/live")

    assert statuses == [401] * LIMIT + [429]
    assert refused.json()["code"] == "rate_limited"
    assert int(refused.headers["retry-after"]) == 60
    assert live.status_code == 200


def test_a_login_refused_by_the_general_limit_never_touches_its_counters(
    seeded_url, clock
):
    user = create_user(seeded_url)
    with general_limit_client(seeded_url) as client:
        for _ in range(LIMIT):
            client.get(ME)

        refused = client.post(
            LOGIN, json={"username": user.username, "password": "not-the-password"}
        )
        assert refused.status_code == 429
        assert counter_rows(seeded_url) == 0

        # Control: in the next window the same login reaches the D15 limits.
        clock.advance(timedelta(minutes=1))
        failed = client.post(
            LOGIN, json={"username": user.username, "password": "not-the-password"}
        )

    assert failed.status_code == 401
    assert counter_rows(seeded_url) > 0
