"""The permission matrix: static coverage check and its self-test (M2-03 E1-E3, E6;
reauthentication #26 E2, E4)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, WebSocket

from app.core.authz import (
    AUTH_RESPONSES,
    CSRF_RESPONSES,
    PERMISSION_RESPONSES,
    ROUTE_MARKER_ATTR,
    Principal,
    authenticated,
    public,
    require,
)
from app.core.ratelimit import (
    PENDING_POLICIES,
    RATE_LIMIT_POLICIES,
    RATE_LIMIT_RESPONSES,
    RateLimiter,
    RateLimitRule,
    rate_limit,
)
from app.core.reauth import (
    REAUTH_RESPONSES,
    ReauthRule,
    RecentAuthGuard,
    recent_auth_guard,
    require_recent_auth,
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
    problems = check_matrix(
        build_app(include_dev_routes=True),
        load_matrix(),
        pending_policies=PENDING_POLICIES,
    )

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


def test_an_unsafe_authenticated_route_must_document_the_csrf_403():
    """#24 C6: POST/PUT/PATCH/DELETE authenticated() routes can answer
    403 csrf_failed, so their OpenAPI entry must say so."""
    app = FastAPI()
    router = APIRouter()

    @router.post("/csrf/undocumented", responses=AUTH_RESPONSES)
    async def undocumented(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.delete("/csrf/documented", responses={**AUTH_RESPONSES, **CSRF_RESPONSES})
    async def documented(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.get("/csrf/safe", responses=AUTH_RESPONSES)
    async def safe(who: Annotated[Principal, authenticated()]) -> None: ...

    app.include_router(router)
    matrix = [
        *DOCS,
        entry("DELETE", "/csrf/documented", "authenticated"),
        entry("GET", "/csrf/safe", "authenticated"),
        entry("POST", "/csrf/undocumented", "authenticated"),
    ]

    assert check_matrix(app, parse_matrix({"route": matrix})) == [
        "POST /csrf/undocumented: 403 csrf_failed is not documented as"
        " ProblemDetails (add CSRF_RESPONSES)"
    ]


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


def test_every_planted_notice_exempt_problem_is_reported():
    """#174 E1: ``notice_exempt`` must match the route's AccessRule, be a
    boolean and appear only on authenticated entries."""
    app = FastAPI()
    router = APIRouter()

    @router.get("/n/exempt", responses=AUTH_RESPONSES)
    async def exempt(
        who: Annotated[Principal, authenticated(notice_exempt=True)],
    ) -> None: ...

    @router.get("/n/exempt-unlisted", responses=AUTH_RESPONSES)
    async def exempt_unlisted(
        who: Annotated[Principal, authenticated(notice_exempt=True)],
    ) -> None: ...

    @router.get("/n/gated", responses=AUTH_RESPONSES)
    async def gated(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.get("/n/gated-ok", responses=AUTH_RESPONSES)
    async def gated_ok(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.get("/n/permission", responses=PERMISSION_RESPONSES)
    async def permission(who: Annotated[Principal, require("user.view")]) -> None: ...

    @router.get("/n/public", dependencies=[public(reason="probe")])
    async def open_route() -> None: ...

    app.include_router(router)
    matrix = [
        *DOCS,
        entry("GET", "/n/exempt", "authenticated", notice_exempt=True),
        entry("GET", "/n/exempt-unlisted", "authenticated"),
        entry("GET", "/n/gated", "authenticated", notice_exempt=True),
        entry("GET", "/n/gated-ok", "authenticated", notice_exempt=False),
        entry(
            "GET",
            "/n/permission",
            "permission",
            permission="user.view",
            allow=["owner"],
            notice_exempt=True,
        ),
        entry("GET", "/n/public", "public", reason="probe", notice_exempt="yes"),
    ]

    assert check_matrix(app, parse_matrix({"route": matrix})) == [
        "GET /n/exempt-unlisted: the route's notice_exempt is true but the matrix"
        " says false",
        "GET /n/gated: the route's notice_exempt is false but the matrix says true",
        "GET /n/permission: notice_exempt is only for access = 'authenticated'",
        "GET /n/permission: the route's notice_exempt is false but the matrix"
        " says true",
        "GET /n/public: notice_exempt is only for access = 'authenticated'",
        "GET /n/public: notice_exempt must be true or false",
    ]


def test_the_real_matrix_lists_exactly_the_notice_exempt_routes():
    """#174 C3 and #36 D3: exactly these five routes skip the privacy-notice
    gate."""
    exempt = {e.key for e in load_matrix() if e.is_notice_exempt}

    assert exempt == {
        ("GET", "/api/v1/auth/me"),
        ("PATCH", "/api/v1/auth/me/preferences"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/privacy-notice"),
        ("POST", "/api/v1/privacy-notice/acceptance"),
    }


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


# --- Reauthentication (#26 E2, E4) -------------------------------------------------

RESET = "user.password_reset.issue"


def _add_correct_reauth_routes(router: APIRouter) -> None:
    # A bound permission with its marker, and a conditional guard.
    @router.post(
        "/ok/reset",
        responses=REAUTH_RESPONSES,
        dependencies=[require_recent_auth(RESET)],
    )
    async def ok_reset(who: Annotated[Principal, require(RESET)]) -> None: ...

    @router.patch("/ok/state", responses=REAUTH_RESPONSES)
    async def ok_state(
        who: Annotated[Principal, require("user.manage")],
        guard: Annotated[RecentAuthGuard, recent_auth_guard("user.suspend_or_archive")],
    ) -> None: ...

    # A safe method with a bound permission is not affected (B2).
    @router.get("/ok/reset", responses=PERMISSION_RESPONSES)
    async def ok_get_reset(who: Annotated[Principal, require(RESET)]) -> None: ...


def correct_reauth_app() -> FastAPI:
    app = correct_app()
    router = APIRouter()
    _add_correct_reauth_routes(router)
    app.include_router(router)
    return app


def planted_reauth_app() -> FastAPI:
    """The correct routes plus one planted problem per E2 rule."""
    app = correct_reauth_app()
    router = APIRouter()
    correction = "finance.correction"

    @router.post("/bad/no-entry", responses=REAUTH_RESPONSES)
    async def no_entry(
        who: Annotated[Principal, authenticated()],
        _: Annotated[Principal, require_recent_auth(correction)],
    ) -> None: ...

    @router.post("/bad/no-marker", responses=REAUTH_RESPONSES)
    async def no_marker(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.post("/bad/other-code", responses=REAUTH_RESPONSES)
    async def other_code(
        who: Annotated[Principal, authenticated()],
        _: Annotated[Principal, require_recent_auth(correction)],
    ) -> None: ...

    @router.post("/bad/conditional", responses=REAUTH_RESPONSES)
    async def conditional(
        who: Annotated[Principal, authenticated()],
        guard: Annotated[RecentAuthGuard, recent_auth_guard(correction)],
    ) -> None: ...

    @router.post(
        "/bad/two-markers",
        responses=REAUTH_RESPONSES,
        dependencies=[
            require_recent_auth(correction),
            require_recent_auth("data.personal_export"),
        ],
    )
    async def two_markers(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.post(
        "/bad/public",
        responses=CSRF_RESPONSES,
        dependencies=[public(reason="probe"), require_recent_auth(correction)],
    )
    async def on_public() -> None: ...

    @router.get(
        "/bad/safe",
        responses=REAUTH_RESPONSES,
        dependencies=[require_recent_auth(correction)],
    )
    async def on_get(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.post(
        "/bad/undocumented",
        responses=AUTH_RESPONSES,
        dependencies=[require_recent_auth(correction)],
    )
    async def undocumented(who: Annotated[Principal, authenticated()]) -> None: ...

    @router.delete("/bad/bound", responses=PERMISSION_RESPONSES)
    async def bound(who: Annotated[Principal, require(RESET)]) -> None: ...

    @router.put(
        "/bad/bound-other-action",
        responses=REAUTH_RESPONSES,
        dependencies=[require_recent_auth(correction)],
    )
    async def bound_other(who: Annotated[Principal, require(RESET)]) -> None: ...

    app.include_router(router)
    return app


REAUTH_CORRECT = [
    *CORRECT,
    entry("GET", "/ok/reset", "permission", permission=RESET, allow=[]),
    entry("POST", "/ok/reset", "permission", permission=RESET, allow=[], reauth=RESET),
    entry(
        "PATCH",
        "/ok/state",
        "permission",
        permission="user.manage",
        allow=[],
        reauth="user.suspend_or_archive",
        reauth_conditional=True,
    ),
]


def test_a_correct_reauth_app_and_matrix_report_nothing():
    entries = parse_matrix({"route": REAUTH_CORRECT})

    assert check_matrix(correct_reauth_app(), entries) == []


def test_every_planted_reauth_problem_is_reported():
    correction = "finance.correction"
    matrix = [
        *REAUTH_CORRECT,
        entry("POST", "/bad/no-entry", "authenticated"),
        entry("POST", "/bad/no-marker", "authenticated", reauth=correction),
        entry(
            "POST", "/bad/other-code", "authenticated", reauth="data.personal_export"
        ),
        entry("POST", "/bad/conditional", "authenticated", reauth=correction),
        entry("POST", "/bad/two-markers", "authenticated", reauth=correction),
        entry(
            "POST",
            "/bad/public",
            "public",
            reason="probe",
            reauth=correction,
            rate_limit_exempt="probe",
        ),
        entry("GET", "/bad/safe", "authenticated", reauth=correction),
        entry("POST", "/bad/undocumented", "authenticated", reauth=correction),
        entry("DELETE", "/bad/bound", "permission", permission=RESET, allow=[]),
        entry(
            "PUT",
            "/bad/bound-other-action",
            "permission",
            permission=RESET,
            allow=[],
            reauth=correction,
        ),
        # Entry format problems (no such routes).
        entry("POST", "/x/unknown-action", "authenticated", reauth="nope.action"),
        entry("POST", "/x/flag-alone", "authenticated", reauth_conditional=True),
        entry(
            "POST",
            "/x/flag-not-bool",
            "authenticated",
            reauth=correction,
            reauth_conditional="yes",
        ),
    ]

    problems = check_matrix(planted_reauth_app(), parse_matrix({"route": matrix}))

    stale = " stale entry (no such route)"
    assert problems == sorted(
        [
            "POST /bad/no-entry: the route requires reauthentication"
            " ('finance.correction') but the matrix has no reauth",
            "POST /bad/no-marker: the matrix says reauth 'finance.correction'"
            " but the route has no reauth marker",
            "POST /bad/other-code: the route's reauth action is"
            " 'finance.correction' but the matrix says 'data.personal_export'",
            "POST /bad/conditional: the route's reauth_conditional is true"
            " but the matrix says false",
            "POST /bad/two-markers: more than one reauth marker (2)",
            "POST /bad/public: a public route cannot require reauthentication",
            "GET /bad/safe: reauthentication is only for POST, PUT, PATCH and DELETE",
            "POST /bad/undocumented: 403 reauth_required is not documented as"
            " ProblemDetails (pass REAUTH_RESPONSES)",
            "POST /bad/undocumented: 403 csrf_failed is not documented as"
            " ProblemDetails (add CSRF_RESPONSES)",
            "DELETE /bad/bound: 'user.password_reset.issue' is bound to"
            " reauthentication (user.password_reset.issue): add require_recent_auth",
            "PUT /bad/bound-other-action: 'user.password_reset.issue' is bound to"
            " reauthentication (user.password_reset.issue): add require_recent_auth",
            "POST /x/unknown-action: unknown reauth action 'nope.action'"
            " (see app/core/reauth.py)",
            "POST /x/unknown-action:" + stale,
            "POST /x/flag-alone: reauth_conditional is only for an entry with reauth",
            "POST /x/flag-alone:" + stale,
            "POST /x/flag-not-bool: reauth_conditional must be true or false",
            "POST /x/flag-not-bool:" + stale,
        ]
    )


def test_a_route_marker_with_an_unregistered_action_is_reported():
    """The registry check also covers a marker built behind the API's back."""
    app = correct_app()
    router = APIRouter()

    async def marker() -> None: ...

    setattr(marker, ROUTE_MARKER_ATTR, ReauthRule("ghost.action"))

    @router.post("/ghost", responses=REAUTH_RESPONSES, dependencies=[Depends(marker)])
    async def ghost(who: Annotated[Principal, authenticated()]) -> None: ...

    app.include_router(router)
    matrix = [
        *CORRECT,
        entry("POST", "/ghost", "authenticated", reauth="ghost.action"),
    ]

    assert check_matrix(app, parse_matrix({"route": matrix})) == [
        "POST /ghost: reauth action 'ghost.action' is not in SENSITIVE_ACTIONS",
        "POST /ghost: unknown reauth action 'ghost.action' (see app/core/reauth.py)",
    ]


def test_the_sample_app_has_a_reauth_route():
    rules = {
        e.key: e.reauth for e in parse_matrix(SAMPLE_MATRIX) if e.reauth is not None
    }

    assert rules == {
        ("POST", "/sample/payroll/{period_id}/finalize"): "payroll.finalize"
    }


def test_the_real_matrix_lists_reauthenticate_without_reauth():
    entries = {e.key: e for e in load_matrix()}
    reauthenticate = entries[("POST", "/api/v1/auth/reauthenticate")]

    assert reauthenticate.access == "authenticated"
    assert reauthenticate.json == {}
    assert reauthenticate.reauth is None


# --- Rate limits (#27 G1-G4) ------------------------------------------------------

RL_DOCS = {**CSRF_RESPONSES, **RATE_LIMIT_RESPONSES}


def _add_correct_rate_limit_routes(router: APIRouter) -> None:
    @router.post(
        "/rl/login",
        responses=RL_DOCS,
        dependencies=[public(reason="probe"), rate_limit("login.ip", "login.username")],
    )
    async def rl_login() -> None: ...

    @router.post(
        "/rl/exempt", responses=CSRF_RESPONSES, dependencies=[public(reason="probe")]
    )
    async def rl_exempt() -> None: ...

    @router.post("/rl/reauth", responses={**AUTH_RESPONSES, **RL_DOCS})
    async def rl_reauth(
        who: Annotated[Principal, authenticated()],
        limiter: Annotated[RateLimiter, rate_limit("reauth.user")],
    ) -> None: ...


def correct_rate_limit_app() -> FastAPI:
    app = correct_app()
    router = APIRouter()
    _add_correct_rate_limit_routes(router)
    app.include_router(router)
    return app


RL_CORRECT = [
    *CORRECT,
    entry(
        "POST",
        "/rl/login",
        "public",
        reason="probe",
        rate_limit=["login.username", "login.ip"],
    ),
    entry("POST", "/rl/exempt", "public", reason="probe", rate_limit_exempt="why"),
    entry("POST", "/rl/reauth", "authenticated", rate_limit=["reauth.user"]),
]


def planted_rate_limit_app() -> FastAPI:
    """The correct routes plus one planted problem per G2 rule."""
    app = correct_rate_limit_app()
    router = APIRouter()
    public_rule = public(reason="probe")

    @router.post("/rl/no-marker", responses=RL_DOCS, dependencies=[public_rule])
    async def no_marker() -> None: ...

    @router.post(
        "/rl/unlisted-codes",
        responses=RL_DOCS,
        dependencies=[public_rule, rate_limit("login.ip")],
    )
    async def unlisted_codes() -> None: ...

    @router.post(
        "/rl/other-codes",
        responses=RL_DOCS,
        dependencies=[public_rule, rate_limit("login.ip")],
    )
    async def other_codes() -> None: ...

    @router.post(
        "/rl/two-markers",
        responses=RL_DOCS,
        dependencies=[
            public_rule,
            rate_limit("login.ip"),
            rate_limit("registration.ip"),
        ],
    )
    async def two_markers() -> None: ...

    @router.post(
        "/rl/undocumented",
        responses=CSRF_RESPONSES,
        dependencies=[public_rule, rate_limit("registration.ip")],
    )
    async def undocumented() -> None: ...

    @router.delete(
        "/rl/unlimited", responses=CSRF_RESPONSES, dependencies=[public_rule]
    )
    async def unlimited() -> None: ...

    @router.post(
        "/rl/both",
        responses=RL_DOCS,
        dependencies=[public_rule, rate_limit("login.ip")],
    )
    async def both() -> None: ...

    async def ghost() -> None: ...

    setattr(ghost, ROUTE_MARKER_ATTR, RateLimitRule(("ghost.policy",)))

    @router.post(
        "/rl/ghost", responses=RL_DOCS, dependencies=[public_rule, Depends(ghost)]
    )
    async def ghost_route() -> None: ...

    app.include_router(router)
    return app


def test_a_correct_rate_limit_app_and_matrix_report_nothing():
    entries = parse_matrix({"route": RL_CORRECT})

    assert check_matrix(correct_rate_limit_app(), entries) == []


def test_every_planted_rate_limit_problem_is_reported():
    matrix = [
        *RL_CORRECT,
        entry("POST", "/rl/no-marker", "public", reason="p", rate_limit=["login.ip"]),
        entry("POST", "/rl/unlisted-codes", "public", reason="p"),
        entry(
            "POST",
            "/rl/other-codes",
            "public",
            reason="p",
            rate_limit=["registration.ip"],
        ),
        entry(
            "POST",
            "/rl/two-markers",
            "public",
            reason="p",
            rate_limit=["login.ip", "registration.ip"],
        ),
        entry(
            "POST",
            "/rl/undocumented",
            "public",
            reason="p",
            rate_limit=["registration.ip"],
        ),
        entry("DELETE", "/rl/unlimited", "public", reason="p"),
        entry(
            "POST",
            "/rl/both",
            "public",
            reason="p",
            rate_limit=["login.ip"],
            rate_limit_exempt="also exempt",
        ),
        entry("POST", "/rl/ghost", "public", reason="p", rate_limit=["ghost.policy"]),
        # Entry format problems (no such routes).
        entry("POST", "/x/not-a-list", "public", reason="p", rate_limit="login.ip"),
        entry("POST", "/x/blank-exempt", "public", reason="p", rate_limit_exempt=" "),
    ]

    problems = check_matrix(planted_rate_limit_app(), parse_matrix({"route": matrix}))

    stale = " stale entry (no such route)"
    assert problems == sorted(
        [
            "POST /rl/no-marker: the route's rate limits are [] but the matrix"
            " says ['login.ip']",
            "POST /rl/no-marker: an unsafe public route needs rate_limit(...) or"
            " rate_limit_exempt (see app/core/ratelimit.py)",
            "POST /rl/unlisted-codes: the route's rate limits are ['login.ip']"
            " but the matrix says []",
            "POST /rl/other-codes: the route's rate limits are ['login.ip']"
            " but the matrix says ['registration.ip']",
            "POST /rl/two-markers: more than one rate_limit marker (2)",
            "POST /rl/undocumented: 429 rate_limited is not documented as"
            " ProblemDetails (pass RATE_LIMIT_RESPONSES)",
            "DELETE /rl/unlimited: an unsafe public route needs rate_limit(...) or"
            " rate_limit_exempt (see app/core/ratelimit.py)",
            "POST /rl/both: an entry has either rate_limit or rate_limit_exempt,"
            " not both",
            "POST /rl/ghost: rate-limit policy 'ghost.policy' is not in"
            " RATE_LIMIT_POLICIES",
            "POST /rl/ghost: unknown rate-limit policy 'ghost.policy'"
            " (see app/core/ratelimit.py)",
            "POST /x/not-a-list: rate_limit must be a list of policy codes",
            "POST /x/not-a-list:" + stale,
            "POST /x/blank-exempt: rate_limit_exempt needs a non-empty reason",
            "POST /x/blank-exempt:" + stale,
        ]
    )


def test_policy_coverage_reports_unused_and_stale_pending_policies():
    app = correct_rate_limit_app()  # applies login.ip, login.username, reauth.user
    entries = parse_matrix({"route": RL_CORRECT})
    policies = {
        code: RATE_LIMIT_POLICIES[code]
        for code in ("login.ip", "login.username", "reauth.user", "registration.ip")
    }

    assert (
        check_matrix(
            app,
            entries,
            rate_limit_policies=policies,
            pending_policies={"registration.ip": "#29"},
        )
        == []
    )
    assert check_matrix(
        app,
        entries,
        rate_limit_policies=policies,
        pending_policies={"reauth.user": "#26", "ghost.policy": "#0"},
    ) == [
        "rate-limit policy 'ghost.policy': in PENDING_POLICIES but not in"
        " RATE_LIMIT_POLICIES",
        "rate-limit policy 'reauth.user': a route applies it but it is still in"
        " PENDING_POLICIES (#26): remove it",
        "rate-limit policy 'registration.ip': no route applies it and it is not in"
        " PENDING_POLICIES",
    ]


def test_the_real_matrix_declares_the_auth_rate_limits():
    entries = {e.key: e for e in load_matrix()}

    assert sorted(entries[("POST", "/api/v1/auth/login")].rate_limit) == [
        "login.ip",
        "login.username",
    ]
    assert entries[("POST", "/api/v1/auth/reauthenticate")].rate_limit == [
        "reauth.user"
    ]
    assert entries[("POST", "/api/v1/dev/demo-jobs")].rate_limit_exempt
    assert entries[("POST", "/api/v1/auth/password")].rate_limit == ["reauth.user"]
    for path in (
        "/api/v1/auth/invitation/lookup",
        "/api/v1/auth/invitation/redeem",
        "/api/v1/auth/password-reset/lookup",
        "/api/v1/auth/password-reset/redeem",
    ):
        assert entries[("POST", path)].rate_limit == ["token_redemption.ip"]
    assert entries[("POST", "/api/v1/auth/registrations")].rate_limit == [
        "registration.ip"
    ]
    # #169 applied the last pending policy.
    assert dict(PENDING_POLICIES) == {}


def test_removing_the_login_limit_is_reported():
    """Mutation 8: a route without its marker but with its matrix entry."""
    app = FastAPI()
    router = APIRouter()

    @router.post("/api/v1/auth/login", dependencies=[public(reason="probe")])
    async def login() -> None: ...

    app.include_router(router)
    matrix = [
        *DOCS,
        entry(
            "POST",
            "/api/v1/auth/login",
            "public",
            reason="probe",
            rate_limit=["login.ip", "login.username"],
        ),
    ]

    assert check_matrix(app, parse_matrix({"route": matrix})) == [
        "POST /api/v1/auth/login: an unsafe public route needs rate_limit(...) or"
        " rate_limit_exempt (see app/core/ratelimit.py)",
        "POST /api/v1/auth/login: the route's rate limits are [] but the matrix"
        " says ['login.ip', 'login.username']",
    ]
