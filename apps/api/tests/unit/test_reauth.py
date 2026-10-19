"""``require_recent_auth``, the conditional guard, the window and the check
order without a database (#26 C).

A test-only app (never mounted in the real app) with a fake resolver whose
session the tests mutate, and the ``clock`` fixture. The real endpoint and the
real resolver are covered by ``tests/integration/test_reauthenticate.py``.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.authz import (
    AccessRule,
    Principal,
    ResolvedSession,
    SessionExpiredError,
    authenticated,
    register_principal_resolver,
    require,
    route_access_rules,
)
from app.core.config import Settings, get_settings
from app.core.conventions import install_conventions
from app.core.csrf import csrf_token_for, require_trusted_origin
from app.core.errors import PROBLEM_JSON
from app.core.reauth import (
    REAUTH_RESPONSES,
    REAUTH_WINDOW,
    ReauthRequiredError,
    ReauthRule,
    RecentAuthGuard,
    is_recent,
    recent_auth_guard,
    require_recent_auth,
    route_reauth_rules,
)
from app.modules.identity.permissions import PAYROLL_FINALIZE, TUTOR, USER_MANAGE
from tests.authz.csrf import CSRF_HEADER, ORIGIN_HEADER
from tests.conftest import CLOCK_START
from tests.unit.authz_app import SAMPLE_SETTINGS

SESSION_TOKEN = bytes(range(32))
MINUTE = timedelta(minutes=1)
SECOND = timedelta(seconds=1)
ITEM = "0199a000-0000-7000-8000-0000000000aa"
VALID_BODY = {"amount": 3}

ROUTER_REAUTH = f"/router-reauth/{ITEM}"
ROUTE_REAUTH = f"/route-reauth/{ITEM}"
SAME_LIST = f"/same-list/{ITEM}"
ORDERED_PATHS = [ROUTER_REAUTH, ROUTE_REAUTH, SAME_LIST]


@dataclass
class FakeSession:
    """What the fake resolver returns; tests change it between requests."""

    principal: Principal | None = None
    reauthenticated_at: datetime | None = None
    expired: bool = False


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int


class Flag(BaseModel):
    sensitive: bool


def build_reauth_app(state: FakeSession) -> FastAPI:
    app = FastAPI(dependencies=[Depends(require_trusted_origin)])
    install_conventions(app)
    app.state.sessionmaker = async_sessionmaker()  # unbound: never connects
    app.dependency_overrides[get_settings] = lambda: SAMPLE_SETTINGS

    async def resolver(
        request: Request, session: AsyncSession, settings: Settings
    ) -> ResolvedSession | None:
        if state.expired:
            raise SessionExpiredError()
        if state.principal is None:
            return None
        return ResolvedSession(
            principal=state.principal,
            session_token=SESSION_TOKEN,
            session_id=uuid.uuid7(),
            reauthenticated_at=state.reauthenticated_at,
        )

    register_principal_resolver(app, resolver)

    # Reauth on the router, the permission on the route.
    by_router = APIRouter(
        prefix="/router-reauth",
        dependencies=[require_recent_auth(PAYROLL_FINALIZE)],
        responses=REAUTH_RESPONSES,
    )

    @by_router.post("/{item_id}")
    async def router_reauth(
        item_id: uuid.UUID,
        principal: Annotated[Principal, require(PAYROLL_FINALIZE)],
        body: Body,
    ) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    # The permission on the router, the reauth on the route.
    by_route = APIRouter(
        prefix="/route-reauth",
        dependencies=[require(PAYROLL_FINALIZE)],
        responses=REAUTH_RESPONSES,
    )

    @by_route.post("/{item_id}", dependencies=[require_recent_auth(PAYROLL_FINALIZE)])
    async def route_reauth(item_id: uuid.UUID, body: Body) -> dict[str, str]:
        return {"status": "ok"}

    # Both in one list, the reauth first.
    @app.post(
        "/same-list/{item_id}",
        responses=REAUTH_RESPONSES,
        dependencies=[
            require_recent_auth(PAYROLL_FINALIZE),
            require(PAYROLL_FINALIZE),
        ],
    )
    async def same_list(item_id: uuid.UUID, body: Body) -> dict[str, str]:
        return {"status": "ok"}

    # The conditional guard on an authenticated() route.
    @app.post("/conditional", responses=REAUTH_RESPONSES)
    async def conditional(
        principal: Annotated[Principal, require(USER_MANAGE)],
        guard: Annotated[RecentAuthGuard, recent_auth_guard("user.suspend_or_archive")],
        body: Flag,
    ) -> dict[str, bool]:
        if body.sensitive:
            guard.ensure()
        return {"sensitive": body.sensitive}

    # The dependency as an annotated parameter yields the principal.
    @app.post("/as-parameter", responses=REAUTH_RESPONSES)
    async def as_parameter(
        who: Annotated[Principal, authenticated()],
        principal: Annotated[Principal, require_recent_auth("finance.correction")],
    ) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    app.include_router(by_router)
    app.include_router(by_route)
    return app


def principal(*permissions: str) -> Principal:
    return Principal(
        user_id=uuid.uuid7(),
        roles=frozenset({TUTOR}),
        permissions=frozenset(permissions),
    )


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


def code_of(response) -> tuple[int, str | None]:
    if response.status_code < 400:
        return response.status_code, None
    return response.status_code, response.json()["code"]


REAUTH_REQUIRED = (403, "reauth_required")
OK = (200, None)


# --- C2: the window -------------------------------------------------------------------


def test_is_recent_boundaries(clock):
    at = clock.now()

    assert not is_recent(None)
    assert is_recent(at)
    clock.advance(REAUTH_WINDOW - SECOND)
    assert is_recent(at)
    clock.advance(SECOND)
    assert not is_recent(at)


def test_a_future_mark_counts_as_recent_only_until_its_window_ends(clock):
    # The comparison is only "now < mark + window"; nothing else is assumed.
    assert is_recent(clock.now() + MINUTE)


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_never_reauthenticated_is_reauth_required(client, state, clock, path):
    state.principal = principal(PAYROLL_FINALIZE)

    assert code_of(client.post(path, json=VALID_BODY)) == REAUTH_REQUIRED


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_the_window_lasts_exactly_five_minutes(client, state, clock, path):
    state.principal = principal(PAYROLL_FINALIZE)
    state.reauthenticated_at = CLOCK_START

    assert code_of(client.post(path, json=VALID_BODY)) == OK
    clock.set(CLOCK_START + timedelta(minutes=4, seconds=59))
    assert code_of(client.post(path, json=VALID_BODY)) == OK
    clock.set(CLOCK_START + timedelta(minutes=5))
    assert code_of(client.post(path, json=VALID_BODY)) == REAUTH_REQUIRED


def test_requests_in_between_do_not_extend_the_window(client, state, clock):
    state.principal = principal(PAYROLL_FINALIZE)
    state.reauthenticated_at = CLOCK_START

    for _ in range(4):
        clock.advance(MINUTE)
        assert code_of(client.post(SAME_LIST, json=VALID_BODY)) == OK
    clock.set(CLOCK_START + REAUTH_WINDOW)

    assert code_of(client.post(SAME_LIST, json=VALID_BODY)) == REAUTH_REQUIRED
    assert state.reauthenticated_at == CLOCK_START


# --- C6: the check order --------------------------------------------------------------


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_no_session_is_401_first(client, state, path):
    response = client.post(path, json={"amount": "x"}, headers={CSRF_HEADER: ""})

    assert code_of(response) == (401, "authentication_required")


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_an_expired_session_is_401_session_expired(client, state, path):
    state.expired = True

    assert code_of(client.post(path, json=VALID_BODY)) == (401, "session_expired")


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_a_missing_csrf_token_comes_before_the_permission(client, state, path):
    state.principal = principal()  # neither the permission nor recent

    response = client.post(path, json=VALID_BODY, headers={CSRF_HEADER: ""})

    assert code_of(response) == (403, "csrf_failed")


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_a_user_without_the_permission_is_never_asked_for_the_password(
    client, state, path
):
    state.principal = principal()

    for body in (VALID_BODY, {"amount": "x"}, None):
        response = client.post(path, json=body)
        assert code_of(response) == (403, "permission_denied")


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_reauth_required_comes_before_validation(client, state, clock, path):
    state.principal = principal(PAYROLL_FINALIZE)

    response = client.post(path, json={"amount": "x", "extra": 1})

    assert code_of(response) == REAUTH_REQUIRED
    assert "errors" not in response.json()


@pytest.mark.parametrize("path", ORDERED_PATHS)
def test_validation_comes_after_a_recent_reauthentication(client, state, clock, path):
    state.principal = principal(PAYROLL_FINALIZE)
    state.reauthenticated_at = CLOCK_START

    response = client.post(path, json={"amount": "x"})

    assert code_of(response) == (422, "validation_error")


def test_the_dependency_as_a_parameter_yields_the_principal(client, state, clock):
    who = principal()
    state.principal = who
    assert code_of(client.post("/as-parameter")) == REAUTH_REQUIRED

    state.reauthenticated_at = CLOCK_START
    response = client.post("/as-parameter")

    assert response.json() == {"user_id": str(who.user_id)}


# --- C3, C4: declarations and the conditional guard -----------------------------------


def test_an_unknown_action_raises_when_the_route_is_built():
    with pytest.raises(ValueError, match="sensitive action"):
        require_recent_auth("user.view")
    with pytest.raises(ValueError, match="sensitive action"):
        recent_auth_guard("nope.nothing")

    router = APIRouter()
    with pytest.raises(ValueError, match="sensitive action"):

        @router.post("/x", dependencies=[require_recent_auth("payroll.finalise")])
        async def misspelled() -> None: ...


def test_the_guard_checks_only_when_ensure_is_called(client, state, clock):
    state.principal = principal(USER_MANAGE)

    plain = client.post("/conditional", json={"sensitive": False})
    sensitive = client.post("/conditional", json={"sensitive": True})
    state.reauthenticated_at = CLOCK_START
    sensitive_after = client.post("/conditional", json={"sensitive": True})

    assert code_of(plain) == OK
    assert code_of(sensitive) == REAUTH_REQUIRED
    assert code_of(sensitive_after) == OK


def test_the_guard_still_needs_the_permission_first(client, state):
    state.principal = principal()

    response = client.post("/conditional", json={"sensitive": True})

    assert code_of(response) == (403, "permission_denied")


def test_the_guard_object(clock):
    guard = RecentAuthGuard("user.suspend_or_archive", None)
    with pytest.raises(ReauthRequiredError):
        guard.ensure()

    fresh = RecentAuthGuard("user.suspend_or_archive", clock.now())
    fresh.ensure()
    assert "reauthenticated_at" not in repr(fresh)


def _contexts(app: FastAPI) -> dict[tuple[str, str], object]:
    return {
        (method, str(r.path)): r
        for r in iter_route_contexts(app.routes)
        if isinstance(r.original_route, APIRoute)
        for method in r.methods or ()
    }


def test_markers_are_separate_from_access_rules(state):
    routes = _contexts(build_reauth_app(state))

    for path in ("/router-reauth/{item_id}", "/route-reauth/{item_id}"):
        route = routes[("POST", path)]
        assert route_reauth_rules(route) == [ReauthRule(PAYROLL_FINALIZE)]
        assert route_access_rules(route) == [
            AccessRule("permission", permission=PAYROLL_FINALIZE)
        ]
    conditional = routes[("POST", "/conditional")]
    assert route_reauth_rules(conditional) == [
        ReauthRule("user.suspend_or_archive", conditional=True)
    ]


# --- C5: the error --------------------------------------------------------------------


def test_the_error_body_never_names_the_action(client, state):
    state.principal = principal(PAYROLL_FINALIZE)

    response = client.post(ROUTER_REAUTH, json=VALID_BODY)

    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["code"] == "reauth_required"
    assert body["detail"] == "Confirm your password to continue."
    assert set(body) == {"type", "title", "status", "code", "request_id", "detail"}
    assert "payroll" not in response.text
    assert "finalize" not in response.text


def test_the_error_takes_no_arguments():
    assert (ReauthRequiredError.status, ReauthRequiredError.code) == (
        403,
        "reauth_required",
    )
    with pytest.raises(TypeError):
        ReauthRequiredError("payroll.finalize")  # type: ignore[call-arg]


def test_marked_routes_document_403_as_problem_details(state):
    document = build_reauth_app(state).openapi()

    operation = document["paths"]["/router-reauth/{item_id}"]["post"]
    schema = operation["responses"]["403"]["content"][PROBLEM_JSON]["schema"]
    assert schema == {"$ref": "#/components/schemas/ProblemDetails"}
    assert "reauth_required" in operation["responses"]["403"]["description"]


def test_without_a_resolver_it_fails_closed():
    app = build_reauth_app(FakeSession())
    del app.state.principal_resolver
    headers = {**ORIGIN_HEADER, CSRF_HEADER: csrf_token_for(SESSION_TOKEN)}

    with TestClient(app, raise_server_exceptions=False, headers=headers) as client:
        response = client.post(SAME_LIST, json=VALID_BODY)

    assert code_of(response) == (401, "authentication_required")
