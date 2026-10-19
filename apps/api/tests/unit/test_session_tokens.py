"""Session tokens, the cookie per mode and the principal resolver seam (#23 B, C)."""

import base64
import hashlib
import uuid
from typing import Annotated

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.authz import (
    AUTH_RESPONSES,
    PRINCIPAL_RESOLVER_STATE,
    Principal,
    ResolvedSession,
    authenticated,
    register_principal_resolver,
)
from app.core.config import Settings, get_settings
from app.core.conventions import install_conventions
from app.main import build_app
from app.modules.identity import sessions
from app.modules.identity.service import resolve_session_principal
from tests.authz.csrf import TEST_APP_ORIGIN
from tests.unit.authz_app import build_authz_app

PLACEHOLDER = SecretStr("postgresql://x:y@127.0.0.1:1/z")


def settings(*, secure: bool) -> Settings:
    return Settings(
        app_env="local",
        app_origin=TEST_APP_ORIGIN,
        database_url=PLACEHOLDER,
        database_direct_url=PLACEHOLDER,
        session_cookie_secure=secure,
    )


# --- B1: tokens -----------------------------------------------------------------------


def test_a_new_token_is_32_random_bytes():
    tokens = {sessions.new_token() for _ in range(50)}

    assert len(tokens) == 50
    assert {len(t) for t in tokens} == {32}


def test_the_stored_form_is_the_sha256_digest():
    token = sessions.new_token()

    assert sessions.hash_token(token) == hashlib.sha256(token).digest()
    assert len(sessions.hash_token(token)) == 32


def test_the_cookie_value_is_unpadded_base64url():
    token = bytes(range(224, 256))

    value = sessions.encode_token(token)

    assert len(value) == 43
    assert "=" not in value
    assert set(value) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert value == base64.urlsafe_b64encode(token).decode().rstrip("=")
    assert sessions.decode_token(value) == token


@pytest.mark.parametrize(
    "value",
    [
        "",
        "A" * 42,
        "A" * 44,
        "A" * 42 + "=",
        "A" * 42 + "B",
        "A" * 42 + "/",
        "A" * 42 + "+",
        "A" * 42 + " ",
        "Ä" * 43,
    ],
)
def test_anything_but_a_canonical_token_decodes_to_none(value):
    assert sessions.decode_token(value) is None


def test_round_trip_of_random_tokens():
    for _ in range(200):
        token = sessions.new_token()
        assert sessions.decode_token(sessions.encode_token(token)) == token


# --- B2, B3: the cookie per mode ------------------------------------------------------


def set_cookie_headers(response: Response) -> list[str]:
    return [
        value.decode()
        for name, value in response.raw_headers
        if name.decode().lower() == "set-cookie"
    ]


@pytest.mark.parametrize(
    ("limits", "max_age"),
    [(sessions.STAFF_LIMITS, 43200), (sessions.EXTENDED_LIMITS, 2592000)],
    ids=["staff", "extended"],
)
@pytest.mark.parametrize(
    ("secure", "expected"),
    [
        (
            True,
            "__Host-classora_session={}; HttpOnly; Max-Age={}; Path=/; SameSite=lax;"
            " Secure",
        ),
        (False, "classora_session={}; HttpOnly; Max-Age={}; Path=/; SameSite=lax"),
    ],
    ids=["secure", "local"],
)
def test_the_session_cookie(secure, expected, limits, max_age):
    token = sessions.new_token()
    response = Response()

    lifetime = limits.absolute
    sessions.set_session_cookie(
        response, settings(secure=secure), token, max_age=lifetime
    )

    # Max-Age is the absolute timeout (#25); no Expires, no Domain.
    assert set_cookie_headers(response) == [
        expected.format(sessions.encode_token(token), max_age)
    ]


@pytest.mark.parametrize(
    ("secure", "expected"),
    [
        (
            True,
            '__Host-classora_session=""; HttpOnly; Max-Age=0; Path=/; SameSite=lax;'
            " Secure",
        ),
        (False, 'classora_session=""; HttpOnly; Max-Age=0; Path=/; SameSite=lax'),
    ],
    ids=["secure", "local"],
)
def test_the_cleared_cookie(secure, expected):
    response = Response()

    sessions.clear_session_cookie(response, settings(secure=secure))

    assert set_cookie_headers(response) == [expected]


def test_the_cookie_name_follows_the_setting():
    assert sessions.cookie_name(settings(secure=True)) == "__Host-classora_session"
    assert sessions.cookie_name(settings(secure=False)) == "classora_session"


def test_the_settings_default_stays_secure():
    assert Settings.model_fields["session_cookie_secure"].default is True


# --- C2: the resolver seam ------------------------------------------------------------


def probe_app(resolver=None) -> FastAPI:
    """A database-free app with one authenticated() route."""
    app = FastAPI()
    install_conventions(app)
    app.state.sessionmaker = async_sessionmaker()  # unbound: never connects
    app.dependency_overrides[get_settings] = lambda: settings(secure=False)
    if resolver is not None:
        register_principal_resolver(app, resolver)

    @app.get("/probe", responses=AUTH_RESPONSES)
    async def probe(
        principal: Annotated[Principal, authenticated()],
    ) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    return app


@pytest.fixture
def anyone() -> Principal:
    return Principal(
        user_id=uuid.uuid7(), roles=frozenset({"tutor"}), permissions=frozenset()
    )


def test_without_a_resolver_the_answer_is_401():
    """Fails closed, before the Unit of Work or the settings are needed."""
    with TestClient(build_authz_app(), raise_server_exceptions=False) as client:
        response = client.get("/sample/me")

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_a_resolver_returning_none_is_401():
    async def nobody(request: Request, session: AsyncSession, settings: Settings):
        return None

    with TestClient(probe_app(nobody), raise_server_exceptions=False) as client:
        response = client.get("/probe")

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_a_resolver_gets_the_request_session_and_settings(anyone):
    seen = {}

    async def resolver(request: Request, session: AsyncSession, settings: Settings):
        seen.update(request=request, session=session, settings=settings)
        return ResolvedSession(principal=anyone, session_token=b"t" * 32)

    with TestClient(probe_app(resolver), raise_server_exceptions=False) as client:
        response = client.get("/probe")

    assert response.status_code == 200
    assert response.json() == {"user_id": str(anyone.user_id)}
    assert isinstance(seen["request"], Request)
    assert isinstance(seen["session"], AsyncSession)
    assert seen["settings"].session_cookie_secure is False


def test_build_app_registers_the_identity_resolver():
    app = build_app()

    assert getattr(app.state, PRINCIPAL_RESOLVER_STATE) is resolve_session_principal


def test_the_authz_sample_app_registers_none():
    assert not hasattr(build_authz_app().state, PRINCIPAL_RESOLVER_STATE)
