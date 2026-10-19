"""The rate-limit registry, windows, the dependency marker and the error
(#27 B1, B2, B5, B6, B8). Counting against PostgreSQL is covered by
``tests/integration/test_rate_limits.py``."""

import ast
import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from app.core import ratelimit
from app.core.authz import public, route_access_rules
from app.core.conventions import install_conventions
from app.core.ratelimit import (
    PENDING_POLICIES,
    RATE_LIMIT_POLICIES,
    RATE_LIMIT_RESPONSES,
    RateLimitedError,
    RateLimiter,
    RateLimitRule,
    key_hash,
    rate_limit,
    route_rate_limit_rules,
    window_for,
)

MINUTE = timedelta(minutes=1)
SECOND = timedelta(seconds=1)
LOGIN_IP = RATE_LIMIT_POLICIES["login.ip"]
REGISTRATION = RATE_LIMIT_POLICIES["registration.ip"]


# --- B1: the registry ------------------------------------------------------------


def test_the_registry_has_exactly_the_adr_0008_limits():
    rows = {
        code: (p.limit, p.window, p.key, p.counts)
        for code, p in RATE_LIMIT_POLICIES.items()
    }

    assert rows == {
        "login.ip": (50, timedelta(minutes=15), "ip", "requests"),
        "login.username": (10, timedelta(minutes=15), "account", "failures"),
        "reauth.user": (5, timedelta(minutes=15), "account", "failures"),
        "token_redemption.ip": (20, timedelta(minutes=15), "ip", "requests"),
        "registration.ip": (10, timedelta(hours=1), "ip", "requests"),
    }
    for code, policy in RATE_LIMIT_POLICIES.items():
        assert policy.code == code
        assert policy.description and policy.applied_by


def test_the_password_change_shares_the_reauth_budget():
    # #27 E1 (a): #30 applies reauth.user to the current-password check.
    assert "#30" in RATE_LIMIT_POLICIES["reauth.user"].applied_by
    assert "password change" in RATE_LIMIT_POLICIES["reauth.user"].description


def test_the_registry_cannot_be_changed_at_runtime():
    with pytest.raises(TypeError):
        RATE_LIMIT_POLICIES["extra"] = LOGIN_IP  # type: ignore[index]
    with pytest.raises(AttributeError):
        LOGIN_IP.limit = 1000  # type: ignore[misc]


def test_pending_policies_are_registered_policies():
    assert set(PENDING_POLICIES) <= set(RATE_LIMIT_POLICIES)


def test_the_module_imports_no_domain_module():
    tree = ast.parse(Path(ratelimit.__file__).read_text())
    targets = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]

    assert not [t for t in targets if t.startswith(("app.modules", "app.workflows"))]


# --- B2: fixed windows and Retry-After ---------------------------------------------


@pytest.mark.parametrize(
    ("now", "start"),
    [
        (datetime(2026, 10, 18, 12, 0, tzinfo=UTC), "12:00"),
        (datetime(2026, 10, 18, 12, 7, 30, tzinfo=UTC), "12:00"),
        (datetime(2026, 10, 18, 12, 14, 59, 999999, tzinfo=UTC), "12:00"),
        (datetime(2026, 10, 18, 12, 15, tzinfo=UTC), "12:15"),
        (datetime(2026, 10, 18, 12, 44, tzinfo=UTC), "12:30"),
        (datetime(2026, 10, 18, 12, 59, tzinfo=UTC), "12:45"),
    ],
)
def test_15_minute_windows_are_aligned_to_the_quarter_hour(now, start):
    window = window_for(LOGIN_IP, now)

    assert window.start.strftime("%H:%M") == start
    assert window.start.second == window.start.microsecond == 0
    assert window.end - window.start == timedelta(minutes=15)
    assert window.start <= now < window.end
    assert window.start.tzinfo is not None


def test_windows_follow_utc_whatever_the_input_offset():
    mexico = datetime(2026, 10, 18, 0, 7, tzinfo=timezone(timedelta(hours=-6)))

    window = window_for(LOGIN_IP, mexico)

    assert window.start == datetime(2026, 10, 18, 6, 0, tzinfo=UTC)


def test_one_hour_windows_are_aligned_to_the_full_hour():
    window = window_for(REGISTRATION, datetime(2026, 10, 18, 12, 59, tzinfo=UTC))

    assert (window.start, window.end) == (
        datetime(2026, 10, 18, 12, 0, tzinfo=UTC),
        datetime(2026, 10, 18, 13, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(0), 900),  # start of the window
        (timedelta(minutes=7, seconds=30), 450),  # middle
        (timedelta(minutes=7, seconds=29, milliseconds=1), 451),  # rounded up
        (timedelta(minutes=14, seconds=59), 1),  # last second
        (timedelta(minutes=14, seconds=59, milliseconds=999), 1),  # never 0
    ],
)
def test_retry_after_is_whole_seconds_until_the_window_ends(offset, expected):
    start = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    now = start + offset

    assert window_for(LOGIN_IP, now).retry_after(now) == expected


def test_windows_use_the_application_clock(clock):
    clock.set(datetime(2026, 10, 18, 12, 14, 59, tzinfo=UTC))
    assert window_for(LOGIN_IP, ratelimit.clock.now()).start.minute == 0
    clock.advance(SECOND)
    assert window_for(LOGIN_IP, ratelimit.clock.now()).start.minute == 15


# --- A2: hashed keys ----------------------------------------------------------------


def test_keys_are_hashed_with_their_policy():
    digest = key_hash("login.username", "demo.owner")

    assert digest == hashlib.sha256(b"login.username\x00demo.owner").digest()
    assert len(digest) == 32
    assert digest != key_hash("reauth.user", "demo.owner")
    assert digest != key_hash("login.username", "demo.owner2")


# --- B5: the declaration -------------------------------------------------------------


@pytest.mark.parametrize(
    "codes",
    [(), ("login.nope",), ("login.ip", "login.ip"), ("LOGIN.IP",)],
    ids=["none", "unknown", "duplicate", "case"],
)
def test_a_bad_declaration_raises_when_the_route_is_built(codes):
    with pytest.raises(ValueError):
        rate_limit(*codes)


def test_the_marker_is_found_on_the_route_and_is_not_an_access_rule():
    app = FastAPI()
    router = APIRouter()

    @router.post(
        "/limited",
        responses=RATE_LIMIT_RESPONSES,
        dependencies=[public(reason="probe")],
    )
    async def limited(
        limiter: Annotated[RateLimiter, rate_limit("login.ip", "login.username")],
    ) -> None: ...

    @router.post("/router-level", dependencies=[public(reason="probe")])
    async def router_level() -> None: ...

    app.include_router(router)
    outer = APIRouter(dependencies=[rate_limit("registration.ip")])
    outer.include_router(router, prefix="/outer")
    app.include_router(outer)
    contexts = {str(c.path): c for c in iter_route_contexts(app.routes)}

    assert route_rate_limit_rules(contexts["/limited"]) == [
        RateLimitRule(("login.ip", "login.username"))
    ]
    assert [r.kind for r in route_access_rules(contexts["/limited"])] == ["public"]
    assert route_rate_limit_rules(contexts["/router-level"]) == []
    assert route_rate_limit_rules(contexts["/outer/router-level"]) == [
        RateLimitRule(("registration.ip",))
    ]


def limiter(*codes: str) -> RateLimiter:
    # No engine: a programming error must raise before any database access.
    return RateLimiter(codes, engine=None, method="POST", path="/x")


@pytest.mark.anyio
async def test_reserving_an_undeclared_policy_is_a_programming_error():
    with pytest.raises(RuntimeError, match="not declared"):
        await limiter("login.ip").reserve("login.username", "someone")
    with pytest.raises(RuntimeError, match="not declared"):
        await limiter("login.ip").reserve("nope.code", "someone")


@pytest.mark.anyio
async def test_reserving_a_requests_policy_is_a_programming_error():
    with pytest.raises(RuntimeError, match="counts requests"):
        await limiter("login.ip", "login.username").reserve("login.ip", "1.2.3.4")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- B6: the error --------------------------------------------------------------------


def error_app() -> FastAPI:
    app = FastAPI()
    install_conventions(app)

    @app.get("/limited")
    async def limited() -> None:
        raise RateLimitedError(retry_after=123)

    @app.get("/limited-zero")
    async def limited_zero() -> None:
        raise RateLimitedError(retry_after=0)

    return app


def test_rate_limited_renders_429_with_retry_after_and_no_policy():
    with TestClient(error_app()) as client:
        response = client.get("/limited")

    assert response.status_code == 429
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["retry-after"] == "123"
    body = response.json()
    assert body["code"] == "rate_limited"
    assert body["title"] == "Too Many Requests"
    assert body["detail"] == "Too many attempts. Try again later."
    assert set(body) == {"type", "title", "status", "code", "request_id", "detail"}
    for code in RATE_LIMIT_POLICIES:
        assert code not in response.text


def test_retry_after_is_at_least_one_second():
    with TestClient(error_app()) as client:
        assert client.get("/limited-zero").headers["retry-after"] == "1"


def test_the_429_is_documented_with_a_retry_after_header():
    [(status, doc)] = RATE_LIMIT_RESPONSES.items()

    assert status == 429
    assert doc["content"]["application/problem+json"]["schema"] == {
        "$ref": "#/components/schemas/ProblemDetails"
    }
    assert doc["headers"]["Retry-After"]["schema"]["type"] == "integer"
