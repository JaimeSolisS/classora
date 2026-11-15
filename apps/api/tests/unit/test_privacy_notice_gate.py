"""The privacy-notice gate in ``app.core.authz`` without a database (#174 C).

C2: the check order ``401 → 403 csrf_failed → 403 privacy_notice_required →
403 permission_denied → 403 reauth_required → 422``, each step proven on the
same routes with every earlier condition satisfied (the #26 test app of
``test_reauth.py``: the permission and the reauthentication on the router,
on the route, or both in one list). C1: ``authenticated()`` gates,
``authenticated(notice_exempt=True)`` doesn't and records the exemption on
its :class:`AccessRule`; ``require()`` has no exemption.
"""

import dataclasses
import uuid
from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from app.core.authz import (
    AccessRule,
    Principal,
    PrivacyNoticeRequiredError,
    ResolvedSession,
    authenticated,
    ensure_privacy_notice_accepted,
    get_current_session,
    require,
    route_access_rules,
)
from app.core.conventions import install_conventions
from app.core.csrf import csrf_token_for
from app.core.errors import PROBLEM_JSON
from app.modules.identity.permissions import PAYROLL_FINALIZE, TUTOR, USER_VIEW
from tests.authz.csrf import CSRF_HEADER, ORIGIN_HEADER
from tests.conftest import CLOCK_START
from tests.unit.test_reauth import (
    ORDERED_PATHS,
    SESSION_TOKEN,
    VALID_BODY,
    FakeSession,
    build_reauth_app,
)

NOTICE_REQUIRED = (403, "privacy_notice_required")


def principal(*permissions: str, accepted: bool) -> Principal:
    return Principal(
        user_id=uuid.uuid7(),
        roles=frozenset({TUTOR}),
        permissions=frozenset(permissions),
        privacy_notice_accepted=accepted,
    )


def code_of(response) -> tuple[int, str | None]:
    if response.status_code < 400:
        return response.status_code, None
    return response.status_code, response.json()["code"]


@pytest.fixture
def state() -> FakeSession:
    return FakeSession()


@pytest.fixture
def client(state: FakeSession) -> Iterator[TestClient]:
    headers = {**ORIGIN_HEADER, CSRF_HEADER: csrf_token_for(SESSION_TOKEN)}
    with TestClient(
        build_reauth_app(state), raise_server_exceptions=False, headers=headers
    ) as test_client:
        yield test_client


# --- C2: the order, one step at a time ------------------------------------------------


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_step_1_no_session_is_401(client, state, path):
    response = client.post(path, json={"amount": "x"}, headers={CSRF_HEADER: ""})

    assert code_of(response) == (401, "authentication_required")


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_step_2_csrf_comes_before_the_notice(client, state, path):
    state.principal = principal(accepted=False)  # nothing else holds either

    response = client.post(path, json={"amount": "x"}, headers={CSRF_HEADER: ""})

    assert code_of(response) == (403, "csrf_failed")


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_step_3_the_notice_comes_before_the_permission(client, state, path):
    state.principal = principal(accepted=False)  # no permission, no reauth

    response = client.post(path, json={"amount": "x", "extra": 1})

    assert code_of(response) == NOTICE_REQUIRED
    assert response.headers["content-type"] == PROBLEM_JSON
    assert "errors" not in response.json()


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_step_3_holds_even_with_the_permission_and_a_recent_reauth(client, state, path):
    state.principal = principal(PAYROLL_FINALIZE, accepted=False)
    state.reauthenticated_at = CLOCK_START

    assert code_of(client.post(path, json=VALID_BODY)) == NOTICE_REQUIRED


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_step_4_permission_denied_after_acceptance(client, state, path):
    state.principal = principal(accepted=True)

    response = client.post(path, json={"amount": "x"})

    assert code_of(response) == (403, "permission_denied")


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_step_5_reauth_required_after_the_permission(client, state, clock, path):
    state.principal = principal(PAYROLL_FINALIZE, accepted=True)

    response = client.post(path, json={"amount": "x"})

    assert code_of(response) == (403, "reauth_required")


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_step_6_validation_then_success(client, state, clock, path):
    state.principal = principal(PAYROLL_FINALIZE, accepted=True)
    state.reauthenticated_at = CLOCK_START

    assert code_of(client.post(path, json={"amount": "x"}))[0] == 422
    assert code_of(client.post(path, json=VALID_BODY)) == (200, None)


# --- C1: authenticated(), authenticated(notice_exempt=True) and require() -------------


def gate_app(resolved: ResolvedSession) -> FastAPI:
    app = FastAPI()
    install_conventions(app)
    app.dependency_overrides[get_current_session] = lambda: resolved

    @app.get("/gated")
    async def gated(who: Annotated[Principal, authenticated()]) -> dict[str, str]:
        return {"user_id": str(who.user_id)}

    @app.get("/exempt")
    async def exempt(
        who: Annotated[Principal, authenticated(notice_exempt=True)],
    ) -> dict[str, str]:
        return {"user_id": str(who.user_id)}

    @app.get("/permission")
    async def permission(
        who: Annotated[Principal, require(USER_VIEW)],
    ) -> dict[str, str]:
        return {"user_id": str(who.user_id)}

    return app


@pytest.mark.parametrize(
    ("accepted", "expected"),
    [
        (False, {"/gated": NOTICE_REQUIRED, "/exempt": (200, None)}),
        (True, {"/gated": (200, None), "/exempt": (200, None)}),
    ],
)
def test_authenticated_gates_unless_exempt(accepted, expected):
    who = principal(USER_VIEW, accepted=accepted)
    app = gate_app(ResolvedSession(who, SESSION_TOKEN))

    with TestClient(app, raise_server_exceptions=False) as client:
        got = {path: code_of(client.get(path)) for path in expected}
        permission = code_of(client.get("/permission"))

    assert got == expected
    assert permission == (NOTICE_REQUIRED if not accepted else (200, None))


def test_the_exemption_is_route_metadata():
    app = gate_app(ResolvedSession(principal(accepted=True), SESSION_TOKEN))
    rules = {
        str(context.path): route_access_rules(context)
        for context in iter_route_contexts(app.routes)
        if str(context.path) in {"/gated", "/exempt", "/permission"}
    }

    assert rules == {
        "/gated": [AccessRule("authenticated")],
        "/exempt": [AccessRule("authenticated", notice_exempt=True)],
        "/permission": [AccessRule("permission", permission=USER_VIEW)],
    }


def test_require_has_no_exemption():
    assert "notice_exempt" not in require.__code__.co_varnames


def test_ensure_privacy_notice_accepted():
    ensure_privacy_notice_accepted(principal(accepted=True))

    with pytest.raises(PrivacyNoticeRequiredError):
        ensure_privacy_notice_accepted(principal(accepted=False))


def test_the_error_takes_no_arguments_and_names_nothing():
    error = PrivacyNoticeRequiredError()

    assert (error.status, error.code) == NOTICE_REQUIRED
    with pytest.raises(TypeError):
        PrivacyNoticeRequiredError("x")  # type: ignore[call-arg]


def test_the_flag_is_hidden_from_repr():
    who = principal(accepted=False)

    assert "privacy" not in repr(who)
    assert [f.name for f in dataclasses.fields(Principal)][-1] == (
        "privacy_notice_accepted"
    )
