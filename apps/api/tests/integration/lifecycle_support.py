"""Helpers for the session-lifecycle tests (#25).

- :func:`signed_in`, :func:`me`, :func:`ping`: log in through the real app and
  call ``/auth/me`` or the test-only unsafe ``authenticated()`` route
  (:data:`ping_router`) with a cookie sent by hand.
- :func:`in_uow`: run an identity service call in its own committed Unit of
  Work, as a later endpoint or job would.
- :func:`session_rows`, :func:`events`: read ``auth_sessions`` and audit rows.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditEvent
from app.core.authz import AUTH_RESPONSES, CSRF_RESPONSES, Principal, authenticated
from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.uow import UnitOfWork
from tests.audit.assertions import assert_no_secret_in_audit, audit_events_for
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import (
    ME,
    SetCookie,
    cookie_header,
    login,
    only_set_cookie,
    signed_headers,
)
from tests.integration.conftest import run_sql, short_lived_engine

PING = "/test-only/lifecycle/ping"
FAIL = "/test-only/lifecycle/fail"

ping_router = APIRouter()


@ping_router.post(PING, responses={**AUTH_RESPONSES, **CSRF_RESPONSES})
async def ping(principal: Annotated[Principal, authenticated()]) -> dict[str, str]:
    """A test-only unsafe ``authenticated()`` route."""
    return {"user_id": str(principal.user_id)}


class DeliberateConflictError(AppError):
    status = 409
    code = "conflict"


@ping_router.post(FAIL, responses={**AUTH_RESPONSES, **CSRF_RESPONSES})
async def fail(principal: Annotated[Principal, authenticated()]) -> None:
    """A test-only route that fails after the session was resolved."""
    raise DeliberateConflictError()


def signed_in(
    client: TestClient, username: str, password: str = TEST_USER_PASSWORD
) -> SetCookie:
    """Log in and return the session cookie (the client's jar is cleared)."""
    response = login(client, username, password)
    assert response.status_code == 200, response.text
    client.cookies.clear()
    return only_set_cookie(response)


def me(client: TestClient, cookie: SetCookie) -> Response:
    client.cookies.clear()
    return client.get(ME, headers=cookie_header(cookie))


def post_ping(client: TestClient, cookie: SetCookie, *, csrf: bool = True) -> Response:
    """The unsafe route, with the session's CSRF token unless ``csrf=False``."""
    client.cookies.clear()
    # auth_client sends Origin on every request.
    headers = signed_headers(cookie) if csrf else cookie_header(cookie)
    return client.post(PING, headers=headers)


def code_of(response: Response) -> tuple[int, str | None]:
    """``(status, code)``; the code is ``None`` for a successful response."""
    if response.status_code < 400:
        return response.status_code, None
    return response.status_code, response.json()["code"]


def in_uow[T](
    url: str,
    call: Callable[[AsyncSession], Awaitable[T]],
    *,
    actor_id: uuid.UUID | None = None,
) -> T:
    """Run ``call(session)`` in a committed Unit of Work (rolled back if it
    raises, and the exception propagates)."""

    async def _run() -> T:
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine), actor_id) as uow:
                return await call(uow.session)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


_SESSION_COLUMNS = (
    "SELECT id, user_id, last_seen_at, expires_at, idle_timeout_seconds,"
    " revoked_at, created_at FROM auth_sessions"
)


def session_rows(url: str, user_id: uuid.UUID | None = None) -> list[Any]:
    """``auth_sessions`` rows (all, or one user's), oldest first."""
    if user_id is None:
        return run_sql(url, _SESSION_COLUMNS + " ORDER BY created_at, id")
    return run_sql(
        url,
        _SESSION_COLUMNS + " WHERE user_id = :user_id ORDER BY created_at, id",
        {"user_id": user_id},
    )


def live_count(url: str, user_id: uuid.UUID) -> int:
    return sum(1 for row in session_rows(url, user_id) if row.revoked_at is None)


def events(url: str, **filters: Any) -> list[AuditEvent]:
    async def _load() -> list[AuditEvent]:
        engine = short_lived_engine(url)
        try:
            async with create_sessionmaker(engine)() as session:
                return await audit_events_for(session, **filters)
        finally:
            await engine.dispose()

    return asyncio.run(_load())


def no_secret_in_audit(url: str, *secrets: str) -> None:
    async def _check() -> None:
        engine = short_lived_engine(url)
        try:
            async with create_sessionmaker(engine)() as session:
                await assert_no_secret_in_audit(session, *secrets)
        finally:
            await engine.dispose()

    asyncio.run(_check())
