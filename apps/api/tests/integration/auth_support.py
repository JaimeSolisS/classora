"""Helpers for the login and session tests (#23).

- :func:`auth_client`: the real app (``build_app`` with its lifespan) on a test
  database, in the secure (``https://`` base URL) or local cookie mode.
- :func:`create_user`: a committed user (the #20 factory helper).
- :func:`parse_set_cookie`: the one raw ``Set-Cookie`` header of a response,
  split into name, value and attributes.
"""

import asyncio
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from fastapi.testclient import TestClient
from httpx import Response

from app.core.config import get_settings
from app.core.csrf import csrf_token_for
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.main import build_app, lifespan
from app.modules.identity import sessions
from tests.authz.csrf import CSRF_HEADER, ORIGIN_HEADER
from tests.factories.identity import TEST_USER_PASSWORD, persist_user
from tests.integration.conftest import settings_for, short_lived_engine

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
ORIGIN = ORIGIN_HEADER
"""Sent by :func:`auth_client` by default; kept for explicit per-request use."""


@contextmanager
def auth_client(
    url: str, *, secure: bool = False, routers: Iterable[APIRouter] = ()
) -> Iterator[TestClient]:
    """A client of the real app; ``secure`` selects the ``__Host-`` cookie.

    In secure mode the base URL is ``https://`` so the client's cookie jar
    sends the ``Secure`` cookie back. Every request carries the test app
    origin (``Origin``) unless the test removes or replaces it (#24 F3).
    """
    app = build_app(lifespan=lifespan)
    for router in routers:  # test-only routes on the real app
        app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: settings_for(
        url, session_cookie_secure=secure
    )
    base_url = "https://testserver" if secure else "http://testserver"
    with TestClient(
        app, base_url=base_url, raise_server_exceptions=False, headers=ORIGIN_HEADER
    ) as client:
        yield client


@dataclass(frozen=True)
class CreatedUser:
    id: uuid.UUID
    username: str


async def _create_user(
    url: str, roles: Iterable[str], delegations: Iterable[str], overrides: Any
) -> CreatedUser:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            user = await persist_user(
                uow.session, roles=roles, delegations=delegations, **overrides
            )
            return CreatedUser(id=user.id, username=user.username)
    finally:
        await engine.dispose()


def create_user(
    url: str,
    *,
    roles: Iterable[str] = ("tutor",),
    delegations: Iterable[str] = (),
    **overrides: Any,
) -> CreatedUser:
    """A committed, active (unless overridden) user with ``roles``.

    Its password is :data:`TEST_USER_PASSWORD`.
    """
    return asyncio.run(_create_user(url, roles, delegations, overrides))


def login(
    client: TestClient, username: str, password: str = TEST_USER_PASSWORD
) -> Response:
    return client.post(
        LOGIN, json={"username": username, "password": password}, headers=ORIGIN
    )


@dataclass(frozen=True)
class SetCookie:
    name: str
    value: str
    attributes: dict[str, str]
    """Lower-case attribute names; flags (``httponly``, ``secure``) map to ``""``."""


def parse_set_cookie(header: str) -> SetCookie:
    first, *rest = (part.strip() for part in header.split(";"))
    name, _, value = first.partition("=")
    attributes: dict[str, str] = {}
    for part in rest:
        key, _, attr_value = part.partition("=")
        attributes[key.lower()] = attr_value
    return SetCookie(name=name, value=value, attributes=attributes)


def only_set_cookie(response: Response) -> SetCookie:
    """The response's single ``Set-Cookie`` header (fails on zero or several)."""
    [header] = response.headers.get_list("set-cookie")
    return parse_set_cookie(header)


def token_of(cookie: SetCookie) -> bytes:
    token = sessions.decode_token(cookie.value)
    assert token is not None, "not a session token"
    return token


def cookie_header(cookie: SetCookie) -> dict[str, str]:
    """A ``Cookie`` request header carrying ``cookie`` (sent by hand)."""
    return {"Cookie": f"{cookie.name}={cookie.value}"}


def csrf_for(cookie: SetCookie) -> str:
    """The CSRF token of the session in ``cookie`` (#24)."""
    return csrf_token_for(token_of(cookie))


def signed_headers(cookie: SetCookie) -> dict[str, str]:
    """``Origin``, the session cookie and its ``X-CSRF-Token``: the headers of
    an unsafe request the SPA sends."""
    return {**ORIGIN, **cookie_header(cookie), CSRF_HEADER: csrf_for(cookie)}
