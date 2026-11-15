"""Role classes, session limits and the expiry rule (#25 B1, C1, C2, C3)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import SessionExpiredError
from app.core.config import Settings
from app.modules.identity.models import AuthSession
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_CODES,
    STAFF_ROLES,
    STUDENT,
    TUTOR,
)
from app.modules.identity.sessions import (
    EXTENDED_LIMITS,
    LAST_SEEN_INTERVAL,
    STAFF_LIMITS,
    is_expired,
    session_limits,
)
from tests.authz.csrf import CSRF_HEADER, ORIGIN_HEADER
from tests.unit.test_csrf_core import probe_app

# --- B1: the role class -------------------------------------------------------------


def test_the_limits_are_adr_0007_d8():
    assert (STAFF_LIMITS.idle, STAFF_LIMITS.absolute) == (
        timedelta(minutes=30),
        timedelta(hours=12),
    )
    assert (EXTENDED_LIMITS.idle, EXTENDED_LIMITS.absolute) == (
        timedelta(days=7),
        timedelta(days=30),
    )
    assert LAST_SEEN_INTERVAL == timedelta(seconds=60)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (OWNER, STAFF_LIMITS),
        (ADMINISTRATOR, STAFF_LIMITS),
        (ACADEMIC_COORDINATOR, STAFF_LIMITS),
        (FINANCE_STAFF, STAFF_LIMITS),
        (TUTOR, EXTENDED_LIMITS),
        (STUDENT, EXTENDED_LIMITS),
        (GUARDIAN, EXTENDED_LIMITS),
    ],
)
def test_every_single_role(role, expected):
    assert session_limits({role}) == expected


def test_every_role_is_classified():
    staff = {r for r in ROLE_CODES if session_limits({r}) == STAFF_LIMITS}

    assert staff == STAFF_ROLES


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ({TUTOR, GUARDIAN}, EXTENDED_LIMITS),
        ({TUTOR, STUDENT, GUARDIAN}, EXTENDED_LIMITS),
        ({TUTOR, FINANCE_STAFF}, STAFF_LIMITS),
        ({GUARDIAN, OWNER}, STAFF_LIMITS),
        ({STUDENT, ACADEMIC_COORDINATOR}, STAFF_LIMITS),
        (set(ROLE_CODES), STAFF_LIMITS),
    ],
    ids=lambda v: "+".join(sorted(v)) if isinstance(v, set) else None,
)
def test_mixed_sets_get_the_strictest_class(roles, expected):
    assert session_limits(roles) == expected
    # Order never matters (a list in either order, or a generator).
    assert session_limits(sorted(roles)) == expected
    assert session_limits(reversed(sorted(roles))) == expected


def test_no_roles_is_staff_fail_safe():
    assert session_limits(()) == STAFF_LIMITS
    assert session_limits(frozenset()) == STAFF_LIMITS


def test_a_code_outside_the_catalog_is_staff_fail_safe():
    assert session_limits({"unknown"}) == STAFF_LIMITS
    assert session_limits({"unknown", TUTOR}) == STAFF_LIMITS


# --- C1: the expiry rule ------------------------------------------------------------

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def row(*, last_seen: datetime = T0, idle: int = 1800, absolute=timedelta(hours=12)):
    return AuthSession(
        last_seen_at=last_seen, expires_at=T0 + absolute, idle_timeout_seconds=idle
    )


@pytest.mark.parametrize(
    ("at", "expired"),
    [
        (T0, False),
        (T0 + timedelta(minutes=30, seconds=-1), False),
        (T0 + timedelta(minutes=30), True),  # exactly at the idle limit
        (T0 + timedelta(minutes=31), True),
    ],
)
def test_idle_limit(at, expired):
    assert is_expired(row(), at) is expired


@pytest.mark.parametrize(
    ("at", "expired"),
    [
        (T0 + timedelta(hours=12, seconds=-1), False),
        (T0 + timedelta(hours=12), True),  # exactly at the absolute limit
    ],
)
def test_absolute_limit_even_with_recent_activity(at, expired):
    recent = row(last_seen=at - timedelta(minutes=1))

    assert is_expired(recent, at) is expired


# --- C2, C3: the error and the core order -------------------------------------------


def test_session_expired_error_has_a_fixed_body():
    error = SessionExpiredError()

    assert (error.status, error.code) == (401, "session_expired")
    assert error.detail == "Your session has expired. Sign in again."
    with pytest.raises(TypeError):
        SessionExpiredError("leak")  # type: ignore[call-arg]


async def expired_resolver(request: Request, session: AsyncSession, settings: Settings):
    raise SessionExpiredError()


@pytest.fixture
def expired_client():
    with TestClient(
        probe_app(expired_resolver),
        raise_server_exceptions=False,
        headers=ORIGIN_HEADER,
    ) as client:
        yield client


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_an_expired_session_is_401_session_expired_before_the_csrf_check(
    expired_client, method
):
    response = expired_client.request(method, "/probe")  # no X-CSRF-Token

    assert response.status_code == 401
    assert response.json()["code"] == "session_expired"
    assert "set-cookie" not in response.headers


def test_session_expired_comes_before_a_wrong_csrf_token():
    with TestClient(
        probe_app(expired_resolver),
        raise_server_exceptions=False,
        headers=ORIGIN_HEADER,
    ) as client:
        response = client.post("/probe", headers={CSRF_HEADER: "x" * 43})

    assert (response.status_code, response.json()["code"]) == (401, "session_expired")
