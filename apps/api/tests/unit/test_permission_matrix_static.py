"""The permission matrix: static coverage check and its self-test (M2-03 E1-E3, E6)."""

from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, WebSocket

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    public,
    require,
)
from app.main import build_app
from tests.authz.harness import (
    MATRIX_PATH,
    check_matrix,
    failure_message,
    load_matrix,
    order_problems,
    parse_matrix,
)
from tests.unit.authz_app import SAMPLE_MATRIX, build_authz_app

# --- The real app (E2, E6) ---------------------------------------------------


def test_real_app_matches_the_matrix():
    problems = check_matrix(build_app(include_dev_routes=True), load_matrix())

    assert not problems, failure_message(problems)


def test_matrix_file_is_sorted_by_path_then_method():
    assert order_problems(load_matrix()) == []


def test_matrix_lists_the_infrastructure_routes():
    # A subset check: every issue that adds a route adds its own entry.
    entries = {(e.method, e.path): e for e in load_matrix()}
    public_routes = {
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("POST", "/api/v1/dev/demo-jobs"),
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/redoc"),
    }

    assert public_routes <= set(entries)
    assert all(
        entries[key].access == "public" and entries[key].reason for key in public_routes
    )


def test_matrix_file_has_a_how_to_header():
    text = MATRIX_PATH.read_text(encoding="utf-8")

    assert text.startswith("# Permission matrix")
    assert "Adding an entry" in text


def test_the_contract_app_is_covered_too():
    # build_app() without dev routes is a subset: only the demo route is extra.
    assert check_matrix(build_app(), load_matrix()) == [
        "POST /api/v1/dev/demo-jobs: stale entry (no such route)"
    ]


def test_order_problems_are_reported():
    entries = parse_matrix(
        {
            "route": [
                {"method": "GET", "path": "/b", "access": "public", "reason": "x"},
                {"method": "GET", "path": "/a", "access": "public", "reason": "x"},
                {"method": "POST", "path": "/a", "access": "public", "reason": "x"},
                {"method": "GET", "path": "/a", "access": "public", "reason": "x"},
            ]
        }
    )

    assert order_problems(entries) == [
        "GET /a: out of order (must come before GET /b)",
        "GET /a: out of order (must come before POST /a)",
    ]


# --- Self-test on a synthetic app and matrix (E3) -------------------------------------


def _add_correct_routes(router: APIRouter) -> None:
    @router.get("/ok/public", dependencies=[public(reason="probe")])
    async def ok_public() -> None: ...

    @router.get("/ok/me", responses=PERMISSION_RESPONSES)
    async def ok_me(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.post("/ok/users/{user_id}", responses=PERMISSION_RESPONSES)
    async def ok_users(who: Annotated[Principal, require("user.view")]) -> None: ...


def synthetic_app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()
    _add_correct_routes(router)

    @router.get("/no-rule")
    async def no_rule() -> None: ...

    @router.get("/two-rules", dependencies=[public(reason="a"), authenticated()])
    async def two_rules() -> None: ...

    @router.get("/unlisted", dependencies=[public(reason="b")])
    async def unlisted() -> None: ...

    @router.get("/mismatch", responses=PERMISSION_RESPONSES)
    async def mismatch(who: Annotated[Principal, require("user.view")]) -> None: ...

    @router.get("/kind-mismatch", responses=PERMISSION_RESPONSES)
    async def kind_mismatch(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.get("/undocumented")
    async def undocumented(who: Annotated[Principal, require("user.view")]) -> None: ...

    @router.get("/undocumented-403", responses={401: PERMISSION_RESPONSES[401]})
    async def undocumented_403(
        who: Annotated[Principal, require("user.view")],
    ) -> None: ...

    app.include_router(router)
    return app


def entry(method: str, path: str, access: str, **fields: Any) -> dict[str, Any]:
    return {"method": method, "path": path, "access": access, **fields}


DOCS = [
    entry("GET", path, "public", reason="docs")
    for path in ("/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc")
]

CORRECT = [
    *DOCS,
    entry("GET", "/ok/public", "public", reason="probe"),
    entry("GET", "/ok/me", "authenticated"),
    entry(
        "POST",
        "/ok/users/{user_id}",
        "permission",
        permission="user.view",
        allow=["owner", "administrator"],
    ),
]


def correct_app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()
    _add_correct_routes(router)
    app.include_router(router)
    return app


def test_a_correct_app_and_matrix_report_nothing():
    assert check_matrix(correct_app(), parse_matrix({"route": CORRECT})) == []


def test_the_sample_app_and_its_matrix_report_nothing():
    entries = parse_matrix(SAMPLE_MATRIX)

    assert check_matrix(build_authz_app(), entries) == []
    assert order_problems(entries) == []


def test_every_planted_problem_is_reported():
    matrix = [
        *CORRECT,
        entry("GET", "/two-rules", "public", reason="a"),
        entry("GET", "/no-rule", "public", reason="c"),
        entry("GET", "/mismatch", "permission", permission="user.manage", allow=[]),
        entry("GET", "/kind-mismatch", "permission", permission="user.view", allow=[]),
        entry("GET", "/undocumented", "permission", permission="user.view", allow=[]),
        entry(
            "GET", "/undocumented-403", "permission", permission="user.view", allow=[]
        ),
        entry("GET", "/stale", "public", reason="gone"),
        entry("GET", "/ok/public", "public", reason="duplicate"),
        entry(
            "GET", "/x/unknown-permission", "permission", permission="nope.x", allow=[]
        ),
        entry(
            "GET",
            "/x/unknown-role",
            "permission",
            permission="user.view",
            allow=["owner", "janitor"],
        ),
        entry("GET", "/x/no-reason", "public"),
        entry("GET", "/x/blank-reason", "public", reason="  "),
        entry("GET", "/x/bad-access", "everyone"),
        entry("GET", "/x/extra", "authenticated", permission="user.view", colour="red"),
        entry("GET", "/x/no-allow", "permission", permission="user.view"),
        entry("get", "x/bad", "authenticated"),
    ]

    problems = check_matrix(synthetic_app(), parse_matrix({"route": matrix}))

    stale = " stale entry (no such route)"
    assert problems == sorted(
        [
            "GET /ok/public: duplicate entry",
            "GET /no-rule: no access rule",
            "GET /two-rules: more than one access rule (2)",
            "GET /unlisted: no matrix entry",
            "GET /stale:" + stale,
            "GET /mismatch: the route requires 'user.view' but the matrix says"
            " 'user.manage'",
            "GET /kind-mismatch: the route is authenticated but the matrix says"
            " permission",
            "GET /undocumented: 401 is not documented as ProblemDetails"
            " (pass AUTH_RESPONSES or PERMISSION_RESPONSES)",
            "GET /undocumented: 403 is not documented as ProblemDetails"
            " (pass AUTH_RESPONSES or PERMISSION_RESPONSES)",
            "GET /undocumented-403: 403 is not documented as ProblemDetails"
            " (pass AUTH_RESPONSES or PERMISSION_RESPONSES)",
            "GET /x/unknown-permission: unknown permission 'nope.x'",
            "GET /x/unknown-permission:" + stale,
            "GET /x/unknown-role: unknown role 'janitor'",
            "GET /x/unknown-role:" + stale,
            "GET /x/no-reason: a public entry needs a non-empty reason",
            "GET /x/no-reason:" + stale,
            "GET /x/blank-reason: a public entry needs a non-empty reason",
            "GET /x/blank-reason:" + stale,
            "GET /x/bad-access: access must be one of"
            " ['public', 'authenticated', 'permission']",
            "GET /x/bad-access:" + stale,
            "GET /x/extra: unknown field(s): colour",
            "GET /x/extra: permission is only for access = 'permission'",
            "GET /x/extra:" + stale,
            "GET /x/no-allow: a permission entry needs an allow list of role codes",
            "GET /x/no-allow:" + stale,
            "get x/bad: method must be one of"
            " ['DELETE', 'GET', 'OPTIONS', 'PATCH', 'POST', 'PUT']",
            "get x/bad: path must start with /",
            "get x/bad:" + stale,
        ]
    )


def test_unsupported_route_types_are_reported():
    app = correct_app()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None: ...

    app.mount("/static", FastAPI())

    assert check_matrix(app, parse_matrix({"route": CORRECT})) == [
        "APIWebSocketRoute /ws: route type not covered by the matrix",
        "Mount /static: route type not covered by the matrix",
    ]


def test_documentation_routes_must_be_public():
    matrix = [e for e in CORRECT if e["path"] != "/redoc"]
    matrix.append(entry("GET", "/redoc", "authenticated"))

    assert check_matrix(correct_app(), parse_matrix({"route": matrix})) == [
        "GET /redoc: a documentation route is public"
    ]


def test_a_missing_documentation_entry_is_reported():
    matrix = [e for e in CORRECT if e["path"] != "/openapi.json"]

    assert check_matrix(correct_app(), parse_matrix({"route": matrix})) == [
        "GET /openapi.json: no matrix entry"
    ]


def test_failure_message_lists_each_problem():
    message = failure_message(["GET /a: no matrix entry", "GET /b: no access rule"])

    assert "tests/authz/matrix.toml" in message
    assert message.endswith("GET /a: no matrix entry\nGET /b: no access rule")
