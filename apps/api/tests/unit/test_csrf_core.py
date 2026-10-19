"""The CSRF token derivation and the core checks without a database (#24 B, C).

The real app with real sessions is covered by ``tests/integration/test_csrf.py``
and ``test_origin.py``; here the sample app of ``authz_app.py`` and a probe
app with a fake resolver exercise the core seam.
"""

import base64
import hashlib
import hmac
import re
import uuid
from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.authz import (
    AUTH_RESPONSES,
    CSRF_RESPONSES,
    Principal,
    ResolvedSession,
    authenticated,
    register_principal_resolver,
)
from app.core.config import Settings, get_settings
from app.core.conventions import install_conventions
from app.core.csrf import (
    CSRF_TOKEN_LENGTH,
    CsrfFailedError,
    csrf_token_for,
    require_trusted_origin,
)
from app.main import build_app
from tests.authz.csrf import CSRF_HEADER, ORIGIN_HEADER, send_without_origin
from tests.unit.authz_app import SAMPLE_SETTINGS, build_authz_app

SESSION_TOKEN = bytes(range(32))

# --- C8: derivation -----------------------------------------------------------------


def test_the_token_is_hmac_sha256_of_the_fixed_message_keyed_with_the_session():
    digest = hmac.new(SESSION_TOKEN, b"classora/csrf/v1", hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    assert csrf_token_for(SESSION_TOKEN) == expected


def test_known_test_vector():
    # Computed once with the standard library; pins the algorithm and message.
    key = b"\x00" * 32
    digest = hmac.new(key, b"classora/csrf/v1", hashlib.sha256).hexdigest()

    token = csrf_token_for(key)

    assert base64.urlsafe_b64decode(token + "=").hex() == digest


def test_the_token_is_deterministic_43_url_safe_characters():
    token = csrf_token_for(SESSION_TOKEN)

    assert token == csrf_token_for(SESSION_TOKEN)
    assert len(token) == CSRF_TOKEN_LENGTH == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)


def test_different_sessions_have_different_tokens():
    tokens = {csrf_token_for(bytes([i]) * 32) for i in range(50)}

    assert len(tokens) == 50


def test_the_token_does_not_reveal_the_session_token():
    token = csrf_token_for(SESSION_TOKEN)

    assert base64.urlsafe_b64encode(SESSION_TOKEN).rstrip(b"=").decode() != token
    assert SESSION_TOKEN.hex() not in base64.urlsafe_b64decode(token + "=").hex()


def test_the_error_takes_no_arguments_and_has_a_fixed_body():
    error = CsrfFailedError()

    assert (error.status, error.code) == (403, "csrf_failed")
    assert error.detail == (
        "The request could not be verified. Reload the page and try again."
    )
    with pytest.raises(TypeError):
        CsrfFailedError("detail")  # type: ignore[call-arg]


# --- C4: the core seam --------------------------------------------------------------


@pytest.fixture
def anyone() -> Principal:
    return Principal(
        user_id=uuid.uuid7(), roles=frozenset({"tutor"}), permissions=frozenset()
    )


def probe_app(resolver=None) -> FastAPI:
    """A database-free app with the Origin check and authenticated() routes."""
    app = FastAPI(dependencies=[Depends(require_trusted_origin)])
    install_conventions(app)
    app.state.sessionmaker = async_sessionmaker()  # unbound: never connects
    app.dependency_overrides[get_settings] = lambda: SAMPLE_SETTINGS
    if resolver is not None:
        register_principal_resolver(app, resolver)

    @app.get("/probe", responses=AUTH_RESPONSES)
    async def probe_get(principal: Annotated[Principal, authenticated()]) -> str:
        return "ok"

    for method in ("post", "put", "patch", "delete"):
        app.add_api_route(
            "/probe",
            probe_get,
            methods=[method.upper()],
            responses={**AUTH_RESPONSES, **CSRF_RESPONSES},
        )
    return app


def resolving(principal: Principal):
    async def resolver(request: Request, session: AsyncSession, settings: Settings):
        return ResolvedSession(principal=principal, session_token=SESSION_TOKEN)

    return resolver


@pytest.fixture
def client(anyone) -> Iterator[TestClient]:
    with TestClient(
        probe_app(resolving(anyone)),
        raise_server_exceptions=False,
        headers=ORIGIN_HEADER,
    ) as test_client:
        yield test_client


UNSAFE = ["POST", "PUT", "PATCH", "DELETE"]


@pytest.mark.parametrize("method", UNSAFE)
def test_unsafe_methods_need_the_token(client, method):
    missing = client.request(method, "/probe")
    wrong = client.request(method, "/probe", headers={CSRF_HEADER: "x" * 43})
    right = client.request(
        method, "/probe", headers={CSRF_HEADER: csrf_token_for(SESSION_TOKEN)}
    )

    assert (missing.status_code, missing.json()["code"]) == (403, "csrf_failed")
    assert (wrong.status_code, wrong.json()["code"]) == (403, "csrf_failed")
    assert right.status_code == 200


@pytest.mark.parametrize("method", UNSAFE)
def test_unsafe_methods_need_the_origin(client, method):
    headers = {CSRF_HEADER: csrf_token_for(SESSION_TOKEN)}

    response = send_without_origin(client, method, "/probe", headers=headers)

    assert (response.status_code, response.json()["code"]) == (403, "csrf_failed")


def test_get_needs_neither(client):
    response = send_without_origin(client, "GET", "/probe")

    assert response.status_code == 200


@pytest.mark.parametrize("method", UNSAFE)
def test_without_a_resolver_unsafe_requests_are_still_401(method):
    with TestClient(
        probe_app(), raise_server_exceptions=False, headers=ORIGIN_HEADER
    ) as client:
        response = client.request(method, "/probe", headers={CSRF_HEADER: "x"})

    assert (response.status_code, response.json()["code"]) == (
        401,
        "authentication_required",
    )


@pytest.mark.parametrize("method", UNSAFE)
def test_a_resolver_returning_none_is_401_before_the_token(method):
    async def nobody(request: Request, session: AsyncSession, settings: Settings):
        return None

    with TestClient(
        probe_app(nobody), raise_server_exceptions=False, headers=ORIGIN_HEADER
    ) as client:
        response = client.request(method, "/probe")

    assert response.status_code == 401


def test_the_sample_app_without_a_resolver_answers_401_on_unsafe_routes():
    with TestClient(
        build_authz_app(), raise_server_exceptions=False, headers=ORIGIN_HEADER
    ) as client:
        response = client.patch("/sample/me")

    assert response.status_code == 401


def test_the_resolved_session_hides_its_token(anyone):
    resolved = ResolvedSession(principal=anyone, session_token=SESSION_TOKEN)

    assert SESSION_TOKEN.hex() not in repr(resolved)
    assert repr(SESSION_TOKEN) not in repr(resolved)


# --- B2: the Origin check is installed on the real app ------------------------------


def test_build_app_installs_the_origin_check_on_every_route():
    app = build_app(include_dev_routes=True)

    assert [d.dependency for d in app.router.dependencies] == [require_trusted_origin]
