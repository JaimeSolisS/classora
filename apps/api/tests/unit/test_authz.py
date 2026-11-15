"""The principal, the route access declarations and the check order (M2-03 A, B)."""

import ast
import dataclasses
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from fastapi.testclient import TestClient

from app.core import authz
from app.core.authz import (
    AUTH_RESPONSES,
    PERMISSION_RESPONSES,
    AccessRule,
    AuthenticationRequiredError,
    PermissionDeniedError,
    Principal,
    authenticated,
    ensure_permission,
    get_current_principal,
    public,
    require,
    route_access_rules,
    route_markers,
)
from app.core.errors import PROBLEM_JSON
from app.main import build_app
from app.modules.identity.permissions import (
    SCHEDULE_OVERRIDE,
    STUDENT_VIEW,
    TUTOR,
    USER_VIEW,
)
from tests.authz.csrf import ORIGIN_HEADER
from tests.unit.authz_app import build_authz_app

API_ROOT = Path(__file__).resolve().parents[2]
PERIOD = "0199a000-0000-7000-8000-0000000000aa"
OVERRIDE = f"/sample/schedule/{PERIOD}/override"
VALID_BODY = {"note": "closing", "amount": 3}


def principal(*permissions: str, roles: tuple[str, ...] = (TUTOR,)) -> Principal:
    return Principal(
        user_id=uuid.uuid7(),
        roles=frozenset(roles),
        permissions=frozenset(permissions),
    )


@pytest.fixture
def app() -> FastAPI:
    return build_authz_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(
        app, raise_server_exceptions=False, headers=ORIGIN_HEADER
    ) as test_client:
        yield test_client


def sign_in(app: FastAPI, who: Principal | None) -> None:
    app.dependency_overrides[get_current_principal] = lambda: who


def assert_problem(response, status: int, code: str, detail: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["code"] == code
    assert body["detail"] == detail
    assert set(body) == {"type", "title", "status", "code", "request_id", "detail"}


# --- A1: Principal ------------------------------------------------------------------


def test_principal_is_frozen_and_checks_permissions():
    p = principal(USER_VIEW)

    assert p.has(USER_VIEW)
    assert not p.has(STUDENT_VIEW)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.roles = frozenset()  # type: ignore[misc]


def test_principal_holds_only_id_roles_and_permissions():
    assert [f.name for f in dataclasses.fields(Principal)] == [
        "user_id",
        "roles",
        "permissions",
        "privacy_notice_accepted",
    ]


def test_principal_repr_shows_only_the_id_and_roles():
    p = principal(USER_VIEW, SCHEDULE_OVERRIDE, roles=(TUTOR,))

    text = repr(p)

    assert str(p.user_id) in text
    assert "tutor" in text
    assert USER_VIEW not in text
    assert SCHEDULE_OVERRIDE not in text
    assert "permissions" not in text


# --- A2, A3: the seam and the errors ------------------------------------------------


def test_without_a_registered_resolver_the_real_dependency_answers_401(app, client):
    # The sample app registers no PrincipalResolver: fails closed (#23 C2).
    response = client.get("/sample/me")

    assert_problem(response, 401, "authentication_required", "Sign in to continue.")


@pytest.mark.parametrize("error", [AuthenticationRequiredError, PermissionDeniedError])
def test_errors_take_no_arguments(error):
    with pytest.raises(TypeError):
        error("user.view")  # type: ignore[call-arg]


def test_error_classes():
    assert (AuthenticationRequiredError.status, AuthenticationRequiredError.code) == (
        401,
        "authentication_required",
    )
    assert (PermissionDeniedError.status, PermissionDeniedError.code) == (
        403,
        "permission_denied",
    )


def test_current_principal_docstring_names_the_seam():
    doc = get_current_principal.__doc__ or ""

    assert "PrincipalResolver" in doc
    assert "dependency_overrides" in doc


# --- A4: core imports no module (#22 audit.py, uow.py; #25 clock.py; #26 reauth.py) --


@pytest.mark.parametrize(
    "name", ["authz.py", "audit.py", "uow.py", "clock.py", "reauth.py"]
)
def test_core_access_and_audit_import_no_domain_module(name):
    path = API_ROOT / "app" / "core" / name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            targets.extend(f"{node.module}.{alias.name}" for alias in node.names)

    assert targets, "nothing scanned"
    forbidden = [t for t in targets if t.startswith(("app.modules", "app.workflows"))]
    assert forbidden == []


# --- B1: the three declarations -----------------------------------------------------


def test_public_route_needs_no_principal(client):
    assert client.get("/sample/public").json() == {"status": "ok"}


@pytest.mark.parametrize("reason", ["", "   "])
def test_public_needs_a_reason(reason):
    with pytest.raises(ValueError, match="reason"):
        public(reason=reason)


def test_authenticated_yields_the_principal(app, client):
    who = principal()
    sign_in(app, who)

    assert client.get("/sample/me").json() == {"user_id": str(who.user_id)}


def test_authenticated_treats_a_missing_principal_as_anonymous(app, client):
    sign_in(app, None)

    response = client.get("/sample/me")

    assert_problem(response, 401, "authentication_required", "Sign in to continue.")


def test_require_allows_holders(app, client):
    who = principal(SCHEDULE_OVERRIDE)
    sign_in(app, who)

    response = client.post(OVERRIDE, json=VALID_BODY)

    assert response.status_code == 200
    assert response.json() == {"period_id": PERIOD, "by": str(who.user_id)}


def test_require_in_dependencies_list(app, client):
    sign_in(app, principal(USER_VIEW))
    assert client.get("/sample/users").status_code == 200

    sign_in(app, principal(STUDENT_VIEW))
    response = client.get("/sample/users")

    assert_problem(
        response, 403, "permission_denied", "You do not have permission to do this."
    )


def test_require_denies_others_without_leaking(app, client):
    sign_in(app, principal(USER_VIEW, STUDENT_VIEW))

    response = client.post(OVERRIDE, json=VALID_BODY)

    assert_problem(
        response, 403, "permission_denied", "You do not have permission to do this."
    )
    assert SCHEDULE_OVERRIDE not in response.text
    assert PERIOD not in response.text


def test_require_answers_401_before_403(client):
    response = client.post(OVERRIDE, json=VALID_BODY)

    assert response.status_code == 401


@pytest.mark.parametrize(
    "code", ["user", "User.view", "user.", ".view", "user..view", "user.view ", "1.a"]
)
def test_require_rejects_malformed_codes_at_declaration(code):
    with pytest.raises(ValueError, match="permission code"):
        require(code)


def test_require_accepts_dotted_codes():
    for code in ("user.view", "user.password_reset.issue", "finance.refund.approve"):
        require(code)


# --- B2: metadata and the walker ----------------------------------------------------


def _routes(app: FastAPI) -> dict[str, RouteContext]:
    return {
        str(r.path): r
        for r in iter_route_contexts(app.routes)
        if isinstance(r.original_route, APIRoute)
    }


def test_rules_are_found_on_route_router_parameter_and_sub_dependency():
    async def sub_dependency(
        who: Annotated[Principal, authenticated()],
    ) -> Principal:
        return who

    router = APIRouter(prefix="/r", dependencies=[require(USER_VIEW)])

    @router.get("/router")
    async def on_router() -> None: ...

    app = FastAPI()

    @app.get("/route", dependencies=[public(reason="probe")])
    async def on_route() -> None: ...

    @app.get("/param")
    async def as_param(who: Annotated[Principal, require(STUDENT_VIEW)]) -> None: ...

    @app.get("/nested")
    async def nested(who: Annotated[Principal, Depends(sub_dependency)]) -> None: ...

    @app.get("/none")
    async def none() -> None: ...

    app.include_router(router)
    routes = _routes(app)

    assert route_access_rules(routes["/r/router"]) == [
        AccessRule("permission", permission=USER_VIEW)
    ]
    assert route_access_rules(routes["/route"]) == [
        AccessRule("public", reason="probe")
    ]
    assert route_access_rules(routes["/param"]) == [
        AccessRule("permission", permission=STUDENT_VIEW)
    ]
    assert route_access_rules(routes["/nested"]) == [AccessRule("authenticated")]
    assert route_access_rules(routes["/none"]) == []


def test_two_declarations_are_two_rules():
    app = FastAPI()

    @app.get("/two", dependencies=[public(reason="a"), authenticated()])
    async def two() -> None: ...

    assert [r.kind for r in route_access_rules(_routes(app)["/two"])] == [
        "public",
        "authenticated",
    ]


def test_one_declaration_reached_twice_is_one_rule():
    access = require(USER_VIEW)
    app = FastAPI()

    @app.get("/twice", dependencies=[access])
    async def twice(who: Annotated[Principal, access]) -> None: ...

    assert route_access_rules(_routes(app)["/twice"]) == [
        AccessRule("permission", permission=USER_VIEW)
    ]


def test_the_walker_is_generic_over_marker_types():
    @dataclasses.dataclass(frozen=True)
    class OtherMarker:
        name: str

    async def marked() -> None: ...

    setattr(marked, authz.ROUTE_MARKER_ATTR, OtherMarker("recent-auth"))
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(marked), require(USER_VIEW)])
    async def x() -> None: ...

    route = _routes(app)["/x"]
    assert route_markers(route, OtherMarker) == [OtherMarker("recent-auth")]
    assert len(route_access_rules(route)) == 1


def test_existing_routes_declare_public_access():
    routes = _routes(build_app(include_dev_routes=True))

    for path in ("/health/live", "/health/ready", "/api/v1/dev/demo-jobs"):
        [rule] = route_access_rules(routes[path])
        assert rule.kind == "public"
        assert rule.reason


# --- B3 ------------------------------------------------------------------------------


def test_ensure_permission():
    ensure_permission(principal(USER_VIEW), USER_VIEW)
    with pytest.raises(PermissionDeniedError):
        ensure_permission(principal(STUDENT_VIEW), USER_VIEW)


# --- B4 ------------------------------------------------------------------------------


def test_response_docs_are_problem_details(app):
    assert set(AUTH_RESPONSES) == {401}
    assert set(PERMISSION_RESPONSES) == {401, 403}
    operation = app.openapi()["paths"]["/sample/schedule/{period_id}/override"]["post"]
    for status in ("401", "403"):
        schema = operation["responses"][status]["content"][PROBLEM_JSON]["schema"]
        assert schema == {"$ref": "#/components/schemas/ProblemDetails"}


# --- B6: check order -----------------------------------------------------------------

INVALID_REQUESTS = [
    pytest.param(OVERRIDE, VALID_BODY, id="valid"),
    pytest.param(OVERRIDE, {"note": 1}, id="body-fails-schema"),
    pytest.param(OVERRIDE, None, id="body-missing"),
    pytest.param("/sample/schedule/not-a-uuid/override", VALID_BODY, id="bad-path"),
    pytest.param(
        "/sample/schedule/not-a-uuid/override?x=1", {"extra": True}, id="all-invalid"
    ),
]


@pytest.mark.parametrize(("path", "body"), INVALID_REQUESTS)
def test_anonymous_gets_401_before_validation(client, path, body):
    response = client.post(path, json=body)

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"
    assert "errors" not in response.json()


@pytest.mark.parametrize(("path", "body"), INVALID_REQUESTS)
def test_unpermitted_gets_403_before_validation(app, client, path, body):
    sign_in(app, principal(USER_VIEW))

    response = client.post(path, json=body)

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_permitted_caller_gets_validation_errors(app, client):
    sign_in(app, principal(SCHEDULE_OVERRIDE))

    response = client.post("/sample/schedule/not-a-uuid/override", json={"note": 1})

    assert response.status_code == 422
    paths = {tuple(e["path"]) for e in response.json()["errors"]}
    assert ("path", "period_id") in paths
    assert ("body", "amount") in paths


def test_unparseable_json_is_rejected_before_authorization(client):
    # Documented exception (FastAPI 0.143.3): FastAPI parses the JSON body
    # before any dependency runs. The 422 reveals nothing about the route.
    response = client.post(
        OVERRIDE, content=b"{not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert [e["code"] for e in response.json()["errors"]] == ["json_invalid"]
