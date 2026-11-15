"""The general in-process API rate limit (#163; ADR-0008 D16 amendment
2026-11-12): the limiter, the middleware, its place in the app and its logs.

The clock is the ``clock`` fixture (``CLOCK_START`` is a whole minute, so a
window starts there). Real-app tests use paths that need no database.
"""

import ast
import logging
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.types import Message, Receive, Scope, Send

from app.core import api_rate_limit
from app.core.api_rate_limit import (
    MAX_TRACKED_CLIENTS,
    RATE_LIMITED_DETAIL,
    ApiRateLimitMiddleware,
    InProcessRateLimiter,
)
from app.core.config import Settings, get_settings
from app.core.middleware import RequestContextMiddleware
from app.core.ratelimit import RateLimitedError
from app.core.security_headers import SecurityHeadersMiddleware
from app.main import build_app, create_app
from tests.authz.csrf import ORIGIN_HEADER, TEST_APP_ORIGIN
from tests.conftest import FrozenClock

PEER = "203.0.113.5"
OTHER_PEER = "203.0.113.6"
PROXY = "10.0.0.7"
PLACEHOLDER_DSN = "postgresql://placeholder:placeholder@127.0.0.1:1/placeholder"
SECURITY_HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}


def settings(trusted_proxies: str = "") -> Settings:
    return Settings(
        app_env="local",
        app_origin=TEST_APP_ORIGIN,
        database_url=SecretStr(PLACEHOLDER_DSN),
        database_direct_url=SecretStr(PLACEHOLDER_DSN),
        trusted_proxies=trusted_proxies,  # type: ignore[arg-type]
    )


# --- the limiter -----------------------------------------------------------------


def test_requests_up_to_the_limit_pass_and_the_next_is_refused(clock: FrozenClock):
    limiter = InProcessRateLimiter(3)

    verdicts = [limiter.hit("k") for _ in range(4)]

    assert [v.allowed for v in verdicts] == [True, True, True, False]
    assert verdicts[3].retry_after == 60


def test_the_next_window_gives_a_new_budget(clock: FrozenClock):
    limiter = InProcessRateLimiter(1)
    assert limiter.hit("k").allowed
    clock.advance(timedelta(seconds=59))
    assert not limiter.hit("k").allowed

    clock.advance(timedelta(seconds=1))

    assert limiter.hit("k").allowed


def test_each_key_has_its_own_budget(clock: FrozenClock):
    limiter = InProcessRateLimiter(1)

    assert limiter.hit("a").allowed
    assert limiter.hit("b").allowed
    assert not limiter.hit("a").allowed


@pytest.mark.parametrize(
    ("seconds_in", "retry_after"),
    [(0, 60), (0.2, 60), (1, 59), (30.5, 30), (59, 1), (59.9, 1)],
)
def test_retry_after_is_the_rest_of_the_window_rounded_up(
    clock: FrozenClock, seconds_in: float, retry_after: int
):
    limiter = InProcessRateLimiter(1)
    clock.advance(timedelta(seconds=seconds_in))
    limiter.hit("k")

    assert limiter.hit("k").retry_after == retry_after


def test_only_the_current_windows_keys_are_kept(clock: FrozenClock):
    limiter = InProcessRateLimiter(5)
    limiter.hit("a")
    limiter.hit("b")

    clock.advance(timedelta(minutes=1))
    limiter.hit("c")

    assert limiter.tracked_keys == {"c"}


def test_the_first_refusal_of_a_window_is_flagged_once(clock: FrozenClock):
    limiter = InProcessRateLimiter(1)
    limiter.hit("k")

    flags = [limiter.hit("k").first_refusal for _ in range(3)]
    clock.advance(timedelta(minutes=1))
    limiter.hit("k")

    assert flags == [True, False, False]
    assert limiter.hit("k").first_refusal


def test_new_keys_beyond_the_cap_pass_uncounted_with_one_warning(
    clock: FrozenClock, caplog: pytest.LogCaptureFixture
):
    limiter = InProcessRateLimiter(1, max_keys=2)
    limiter.hit("a")
    limiter.hit("b")

    with caplog.at_level(logging.WARNING, logger="app.security"):
        verdicts = [limiter.hit("c") for _ in range(3)] + [limiter.hit("d")]

    assert all(v.allowed for v in verdicts)
    assert limiter.tracked_keys == {"a", "b"}
    # Tracked keys keep their limit.
    assert not limiter.hit("a").allowed
    overflow = [
        r for r in caplog.records if r.getMessage() == "api_rate_limit.overflow"
    ]
    assert len(overflow) == 1
    assert overflow[0].levelno == logging.WARNING

    caplog.clear()
    clock.advance(timedelta(minutes=1))
    with caplog.at_level(logging.WARNING, logger="app.security"):
        for key in ("a", "b", "c"):
            limiter.hit(key)
    assert [r.getMessage() for r in caplog.records] == ["api_rate_limit.overflow"]


def test_the_default_cap_is_ten_thousand_clients():
    assert MAX_TRACKED_CLIENTS == 10_000


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limiter_needs_a_positive_limit(limit: int):
    with pytest.raises(ValueError, match="at least 1"):
        InProcessRateLimiter(limit)


def test_the_detail_is_the_same_as_the_authentication_limits():
    assert RATE_LIMITED_DETAIL == RateLimitedError.detail
    assert api_rate_limit.RATE_LIMITED_CODE == RateLimitedError.code


# --- the middleware on a raw ASGI app --------------------------------------------


class Spy:
    """An inner ASGI app that counts its calls and answers 204."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})


class FakeApp:
    """``scope["app"]`` with the test settings override."""

    def __init__(self, trusted_proxies: str = "") -> None:
        self.dependency_overrides = {get_settings: lambda: settings(trusted_proxies)}


def http_scope(
    path: str = "/api/v1/things",
    *,
    method: str = "GET",
    client: tuple[str, int] | None = (PEER, 40000),
    headers: list[tuple[bytes, bytes]] | None = None,
    app: Any = None,
) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": client,
        "server": ("testserver", 80),
        "app": app if app is not None else FakeApp(),
    }


async def call(middleware: ApiRateLimitMiddleware, scope: Scope) -> list[Message]:
    sent: list[Message] = []

    async def receive() -> Message:
        raise AssertionError("the request body was read")

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


def status_of(messages: list[Message]) -> int:
    return int(messages[0]["status"])


async def statuses(
    middleware: ApiRateLimitMiddleware, scope: Scope, n: int
) -> list[int]:
    return [status_of(await call(middleware, scope)) for _ in range(n)]


@pytest.fixture
def spy() -> Spy:
    return Spy()


@pytest.fixture
def limited(spy: Spy, clock: FrozenClock) -> ApiRateLimitMiddleware:
    return ApiRateLimitMiddleware(spy, limit_per_minute=3)


@pytest.mark.anyio
async def test_the_fourth_request_is_refused_without_calling_the_app(
    limited: ApiRateLimitMiddleware, spy: Spy
):
    assert await statuses(limited, http_scope(method="POST"), 4) == [204, 204, 204, 429]
    assert spy.calls == 3


@pytest.mark.anyio
async def test_a_new_window_lets_the_client_through_again(
    limited: ApiRateLimitMiddleware, clock: FrozenClock
):
    await statuses(limited, http_scope(), 4)
    clock.advance(timedelta(minutes=1))

    assert await statuses(limited, http_scope(), 1) == [204]


@pytest.mark.anyio
async def test_another_ip_has_its_own_budget(limited: ApiRateLimitMiddleware):
    await statuses(limited, http_scope(), 4)

    assert await statuses(limited, http_scope(client=(OTHER_PEER, 1)), 1) == [204]


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
async def test_health_probes_are_never_counted_or_refused(
    limited: ApiRateLimitMiddleware, spy: Spy, path: str
):
    assert await statuses(limited, http_scope(path), 10) == [204] * 10
    # The probes took nothing from the client's budget.
    assert await statuses(limited, http_scope(), 4) == [204, 204, 204, 429]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/no/such/path"),
        ("OPTIONS", "/api/v1/auth/me"),
        ("GET", "/api/v1/notifications/stream"),
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("POST", "/api/v1/auth/login"),
        # Only GET on a probe is exempt.
        ("POST", "/health/live"),
        ("GET", "/health/live/x"),
    ],
)
async def test_every_other_request_is_counted(
    limited: ApiRateLimitMiddleware, method: str, path: str
):
    scope = http_scope(path, method=method)

    assert await statuses(limited, scope, 4) == [204, 204, 204, 429]


@pytest.mark.anyio
async def test_non_http_scopes_pass_through(limited: ApiRateLimitMiddleware, spy: Spy):
    for _ in range(5):
        await limited({"type": "lifespan"}, None, None)  # type: ignore[arg-type]

    assert spy.calls == 5


def forwarded(value: str) -> list[tuple[bytes, bytes]]:
    return [(b"x-forwarded-for", value.encode())]


@pytest.mark.anyio
async def test_a_trusted_proxys_forwarded_clients_have_separate_budgets(
    limited: ApiRateLimitMiddleware,
):
    app = FakeApp(trusted_proxies=f"{PROXY}/32")
    first = http_scope(
        client=(PROXY, 1), headers=forwarded(f"198.51.100.1, {PEER}"), app=app
    )
    second = http_scope(client=(PROXY, 1), headers=forwarded(OTHER_PEER), app=app)

    assert await statuses(limited, first, 4) == [204, 204, 204, 429]
    assert await statuses(limited, second, 1) == [204]


@pytest.mark.anyio
async def test_forwarded_for_from_an_untrusted_peer_is_ignored(
    limited: ApiRateLimitMiddleware,
):
    rotating = [http_scope(headers=forwarded(f"198.51.100.{n}")) for n in range(4)]

    assert [status_of(await call(limited, scope)) for scope in rotating] == [
        204,
        204,
        204,
        429,
    ]


@pytest.mark.anyio
async def test_ipv6_clients_in_one_64_share_a_budget(limited: ApiRateLimitMiddleware):
    first = http_scope(client=("2001:db8:1:2::1", 1))
    second = http_scope(client=("2001:db8:1:2:ffff::9", 1))
    elsewhere = http_scope(client=("2001:db8:1:3::1", 1))

    assert await statuses(limited, first, 2) == [204, 204]
    assert await statuses(limited, second, 2) == [204, 429]
    assert await statuses(limited, elsewhere, 1) == [204]


@pytest.mark.anyio
@pytest.mark.parametrize("client", [None, ("testclient", 50000)])
async def test_a_client_that_is_not_an_ip_uses_the_unknown_key(
    limited: ApiRateLimitMiddleware, client: tuple[str, int] | None
):
    await statuses(limited, http_scope(client=None), 3)

    assert await statuses(limited, http_scope(client=client), 1) == [429]
    assert limited.limiter.tracked_keys == {"unknown"}


def test_the_middleware_uses_client_ip_and_ip_key_without_a_copy():
    source = Path(api_rate_limit.__file__).read_text()
    tree = ast.parse(source)
    imported = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert ("app.core.client_ip", "client_ip") in imported
    assert ("app.core.client_ip", "ip_key") in imported
    assert "x-forwarded-for" not in source.lower()
    assert "IPv6Network" not in source


def test_the_middleware_imports_no_database_or_domain_code():
    tree = ast.parse(Path(api_rate_limit.__file__).read_text())
    modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    for module in modules:
        assert not module.startswith(("sqlalchemy", "app.modules", "app.workflows"))
        assert module not in {"app.core.db", "app.core.ratelimit", "app.core.uow"}


# --- logging -------------------------------------------------------------------


@pytest.mark.anyio
async def test_one_warning_per_client_per_window_without_ip_or_path(
    limited: ApiRateLimitMiddleware,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
):
    path = "/api/v1/users/jane.doe@example.com"
    with caplog.at_level(logging.DEBUG, logger="app.security"):
        await statuses(limited, http_scope(path, method="DELETE"), 8)
        await statuses(limited, http_scope(path, client=(OTHER_PEER, 1)), 5)

    records = [r for r in caplog.records if r.getMessage() == "api_rate_limit.exceeded"]
    assert len(records) == 2
    for record in records:
        assert record.name == "app.security"
        assert record.levelno == logging.WARNING
        assert record.policy == "api.ip"  # type: ignore[attr-defined]
        rendered = repr(record.__dict__)
        for forbidden in (PEER, OTHER_PEER, "jane.doe", "example.com", "/api/v1"):
            assert forbidden not in rendered
    assert [r.method for r in records] == ["DELETE", "GET"]  # type: ignore[attr-defined]

    caplog.clear()
    clock.advance(timedelta(minutes=1))
    with caplog.at_level(logging.WARNING, logger="app.security"):
        await statuses(limited, http_scope(path), 5)
    assert [r.getMessage() for r in caplog.records] == ["api_rate_limit.exceeded"]


# --- the real app (no database needed) -------------------------------------------


@pytest.fixture
def real_client(app_env: None, clock: FrozenClock) -> Iterator[TestClient]:
    app = build_app(api_rate_limit_per_minute=2)
    with TestClient(
        app, raise_server_exceptions=False, headers=ORIGIN_HEADER, client=(PEER, 1)
    ) as client:
        yield client


def test_the_429_is_the_rate_limited_problem_with_every_api_header(
    real_client: TestClient,
):
    for _ in range(2):
        assert real_client.get("/no/such/path").status_code == 404

    response = real_client.post(
        "/api/v1/auth/login",
        json={"username": "someone", "password": "x"},
        headers={"X-Request-ID": "limit-check-1"},
    )

    assert response.status_code == 429
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "rate_limited"
    assert body["detail"] == RateLimitedError.detail
    assert body["status"] == 429
    assert body["request_id"] == "limit-check-1"
    assert response.headers["retry-after"] == "60"
    assert response.headers.get_list("x-request-id") == ["limit-check-1"]
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get_list(name) == [value]
    assert "set-cookie" not in response.headers
    for leak in (PEER, "api.ip"):
        assert leak not in str({k: v for k, v in body.items() if k != "status"})


def test_the_real_app_never_limits_the_liveness_probe(real_client: TestClient):
    for _ in range(3):
        real_client.get("/openapi.json")

    assert real_client.get("/openapi.json").status_code == 429
    assert [real_client.get("/health/live").status_code for _ in range(5)] == [200] * 5


def test_a_429_gets_its_access_line(
    real_client: TestClient, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.INFO, logger="app.access"):
        statuses_seen = [real_client.get("/docs").status_code for _ in range(3)]

    assert statuses_seen == [200, 200, 429]
    access = [r for r in caplog.records if r.name == "app.access"]
    assert access[-1].status == 429  # type: ignore[attr-defined]
    assert access[-1].path == "<unmatched>"  # type: ignore[attr-defined]


# --- wiring -------------------------------------------------------------------


def user_middleware(app: FastAPI) -> list[type]:
    return [m.cls for m in app.user_middleware]  # type: ignore[misc]


def test_build_app_installs_no_limiter_by_default():
    assert ApiRateLimitMiddleware not in user_middleware(build_app())


def test_the_limiter_sits_inside_the_conventions_middleware():
    app = build_app(api_rate_limit_per_minute=5)

    # user_middleware lists the outermost first.
    assert user_middleware(app) == [
        SecurityHeadersMiddleware,
        RequestContextMiddleware,
        ApiRateLimitMiddleware,
    ]
    assert app.user_middleware[2].kwargs == {"limit_per_minute": 5}


def test_create_app_installs_the_setting(app_env: None, monkeypatch):
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "42")

    app = create_app()

    [entry] = [m for m in app.user_middleware if m.cls is ApiRateLimitMiddleware]
    assert entry.kwargs == {"limit_per_minute": 42}


def test_create_app_uses_the_default_of_600(app_env: None):
    app = create_app()

    [entry] = [m for m in app.user_middleware if m.cls is ApiRateLimitMiddleware]
    assert entry.kwargs == {"limit_per_minute": 600}


def test_create_app_installs_none_when_the_setting_is_0(app_env: None, monkeypatch):
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "0")

    assert ApiRateLimitMiddleware not in user_middleware(create_app())
