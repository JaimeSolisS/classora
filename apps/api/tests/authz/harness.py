"""The permission-matrix harness (M2-03).

- :func:`load_matrix` reads ``matrix.toml`` (stdlib ``tomllib``).
- :func:`check_matrix` is the static check: it compares the matrix with an
  app's routes and their access rules, and lists every problem as
  ``METHOD path: reason``.
- :func:`matrix_cases` and :func:`run_case` are the runtime check: one request
  per entry and actor (anonymous, each role, and an Administrator holding the
  delegation for a delegable permission).
- :func:`csrf_cases` and :func:`run_csrf_case` are the CSRF part of the
  runtime check (#24 F2): for every unsafe entry, ``csrf:no-origin`` (no
  ``Origin``: ``403 csrf_failed``) and ``csrf:no-token`` (no
  ``X-CSRF-Token``: ``403 csrf_failed`` on a protected route, anything but
  ``403`` on a public one), sent by an allowed role (anonymous for public).
- Reauthentication (#26): an entry's ``reauth`` must match the route's
  :class:`~app.core.reauth.ReauthRule` marker (static check, with the
  bound-permission rule). At runtime the allowed actors of an unconditional
  ``reauth`` entry reauthenticate before the request (denied actors do not, so
  ``permission_denied`` still comes first), and :func:`reauth_cases` adds one
  ``METHOD path reauth:none`` case per such entry: an allowed actor with a
  valid ``Origin`` and CSRF token but no reauthentication expects ``403
  reauth_required``.
- Rate limits (#27): an entry's ``rate_limit`` list must match the route's
  :class:`~app.core.ratelimit.RateLimitRule` codes (order ignored), a route
  with a marker documents ``429``, every unsafe ``public()`` route declares a
  policy or has ``rate_limit_exempt``, and (with ``pending_policies``) every
  policy is applied by a route or listed in
  :data:`~app.core.ratelimit.PENDING_POLICIES`, never both. Static only: the
  runtime cases send no rate-limit requests.
- Privacy notice (#174): an entry's ``notice_exempt`` must match the route's
  :class:`~app.core.authz.AccessRule` (static check; only for
  ``authenticated``). At runtime :func:`notice_cases` adds one ``METHOD path
  notice:pending`` case per ``authenticated`` or ``permission`` entry, sent by
  :data:`NOTICE_PENDING_OWNER` (an Owner who has not accepted the current
  version) with a valid ``Origin`` and CSRF token: ``403
  privacy_notice_required`` on every entry except the notice-exempt ones,
  which must not answer that code.
- :func:`matrix_user_for` creates an actor's real, active database user (the
  #20 factory helper, password :data:`TEST_USER_PASSWORD`) and loads its
  :class:`Principal`. Every actor accepts the current privacy-notice version
  (#174 E3) except one with ``notice_pending``.

Every request carries ``Origin: TEST_APP_ORIGIN``, and every unsafe request
of a signed-in actor its session's ``X-CSRF-Token``, so CSRF never hides a
``permission_denied`` (#24 F1); only the CSRF cases leave one of them out.

How a case signs in (``run_case(..., sign_in=...)``):

- ``"login"`` (the default; the real-app matrix,
  ``tests/integration/test_permission_matrix.py`` E4): the actor logs in
  through ``POST /api/v1/auth/login`` before **every** case and sends that
  fresh session's cookie, so the logout case never invalidates a later case.
  ``get_current_principal`` is not overridden.
- ``"override"`` (the self-test on the sample app of
  ``tests/unit/authz_app.py``, E5, which has no login route): the actor's
  principal, with the fixed :data:`OVERRIDE_SESSION_TOKEN`, replaces
  ``get_current_session`` for the request, so ``get_current_principal`` still
  runs the CSRF token check.

Anonymous sends no cookie and uses the real dependency in both modes.
"""

import re
import tomllib
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Route

from app.core import clock
from app.core.authz import (
    Principal,
    ResolvedSession,
    get_current_session,
    route_access_rules,
)
from app.core.csrf import UNSAFE_METHODS, csrf_token_for
from app.core.errors import PROBLEM_DETAILS_REF, PROBLEM_JSON
from app.core.ratelimit import (
    RATE_LIMIT_POLICIES,
    RateLimitPolicy,
    route_rate_limit_rules,
)
from app.core.reauth import (
    SENSITIVE_ACTIONS,
    SensitiveAction,
    route_reauth_rules,
)
from app.core.sse import EventStreamResponse
from app.modules.identity import service as identity
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    DELEGABLE_PERMISSIONS,
    OWNER,
    PERMISSION_CODES,
    ROLE_CODES,
)
from tests.authz.csrf import CSRF_HEADER, ORIGIN_HEADER
from tests.authz.streaming import ClientStream
from tests.factories.identity import TEST_USER_PASSWORD, persist_user

MATRIX_PATH = Path(__file__).with_name("matrix.toml")
LOGIN_PATH = "/api/v1/auth/login"
REAUTH_PATH = "/api/v1/auth/reauthenticate"

ACCESS_KINDS = ("public", "authenticated", "permission")
ENTRY_FIELDS = frozenset(
    {
        "method",
        "path",
        "access",
        "permission",
        "allow",
        "reason",
        "path_params",
        "json",
        "reauth",
        "reauth_conditional",
        "rate_limit",
        "rate_limit_exempt",
        "notice_exempt",
        "stream",
    }
)
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

# Placeholder path parameter values when an entry gives none.
PLACEHOLDER_UUID = "00000000-0000-7000-8000-000000000001"
PLACEHOLDER_INT = "1"

_PATH_PARAM = re.compile(r"{([^}:]+)(?::[^}]*)?}")

OVERRIDE_SESSION_TOKEN = b"classora-matrix-override-session"
"""The raw session token of every overridden actor (``sign_in="override"``)."""


# --- The matrix file -------------------------------------------------------------


@dataclass(frozen=True)
class MatrixEntry:
    """One ``[[route]]`` table, kept as written (checked by :func:`entry_problems`)."""

    method: Any
    path: Any
    access: Any
    permission: Any = None
    allow: Any = None
    reason: Any = None
    path_params: Any = None
    json: Any = None
    reauth: Any = None
    reauth_conditional: Any = None
    rate_limit: Any = None
    rate_limit_exempt: Any = None
    notice_exempt: Any = None
    stream: Any = None
    unknown_fields: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.method), str(self.path))

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def allowed_roles(self) -> frozenset[str]:
        return frozenset(self.allow or ())

    @property
    def is_notice_exempt(self) -> bool:
        """``notice_exempt = true`` (#174): callable before the privacy notice
        is accepted."""
        return self.notice_exempt is True

    @property
    def is_stream(self) -> bool:
        """``stream = true`` (#42): the route answers an endless event
        stream; the runtime check reads only its status and headers."""
        return self.stream is True

    @property
    def needs_reauth(self) -> bool:
        """An unconditional ``reauth`` entry (checked at runtime)."""
        return self.reauth is not None and self.reauth_conditional is not True


def parse_matrix(document: Mapping[str, Any]) -> list[MatrixEntry]:
    """The entries of a parsed matrix document (``{"route": [...]}``)."""
    entries = []
    for table in document.get("route", []):
        values = {name: table[name] for name in ENTRY_FIELDS if name in table}
        entries.append(
            MatrixEntry(
                **{"method": None, "path": None, "access": None, **values},
                unknown_fields=tuple(sorted(set(table) - ENTRY_FIELDS)),
            )
        )
    return entries


def load_matrix(path: Path = MATRIX_PATH) -> list[MatrixEntry]:
    with path.open("rb") as file:
        return parse_matrix(tomllib.load(file))


def order_problems(entries: Iterable[MatrixEntry]) -> list[str]:
    """Entries must be sorted by path, then method."""
    keys = [(str(e.path), str(e.method)) for e in entries]
    return [
        f"{after[1]} {after[0]}: out of order"
        f" (must come before {before[1]} {before[0]})"
        for before, after in zip(keys, keys[1:], strict=False)
        if after < before
    ]


def entry_problems(
    entry: MatrixEntry,
    *,
    permission_codes: Collection[str] = PERMISSION_CODES,
    role_codes: Collection[str] = ROLE_CODES,
    sensitive_actions: Collection[str] = SENSITIVE_ACTIONS,
    rate_limit_policies: Collection[str] = RATE_LIMIT_POLICIES,
) -> list[str]:
    """Format problems of one entry."""
    problems: list[str] = []
    if entry.unknown_fields:
        problems.append(f"unknown field(s): {', '.join(entry.unknown_fields)}")
    if entry.method not in HTTP_METHODS:
        problems.append(f"method must be one of {sorted(HTTP_METHODS)}")
    if not isinstance(entry.path, str) or not entry.path.startswith("/"):
        problems.append("path must start with /")
    if entry.access not in ACCESS_KINDS:
        problems.append(f"access must be one of {list(ACCESS_KINDS)}")
    if entry.access == "permission":
        if entry.permission not in permission_codes:
            problems.append(f"unknown permission {entry.permission!r}")
        if not isinstance(entry.allow, list):
            problems.append("a permission entry needs an allow list of role codes")
        else:
            problems.extend(
                f"unknown role {role!r}"
                for role in entry.allow
                if role not in role_codes
            )
    else:
        if entry.permission is not None:
            problems.append("permission is only for access = 'permission'")
        if entry.allow is not None:
            problems.append("allow is only for access = 'permission'")
    if entry.access == "public":
        if not isinstance(entry.reason, str) or not entry.reason.strip():
            problems.append("a public entry needs a non-empty reason")
    if entry.path_params is not None and not isinstance(entry.path_params, dict):
        problems.append("path_params must be a table")
    if entry.reauth is not None and entry.reauth not in sensitive_actions:
        problems.append(
            f"unknown reauth action {entry.reauth!r} (see app/core/reauth.py)"
        )
    if entry.reauth_conditional is not None:
        if not isinstance(entry.reauth_conditional, bool):
            problems.append("reauth_conditional must be true or false")
        if entry.reauth is None:
            problems.append("reauth_conditional is only for an entry with reauth")
    if entry.rate_limit is not None:
        if not isinstance(entry.rate_limit, list) or not all(
            isinstance(code, str) for code in entry.rate_limit
        ):
            problems.append("rate_limit must be a list of policy codes")
        else:
            problems.extend(
                f"unknown rate-limit policy {code!r} (see app/core/ratelimit.py)"
                for code in entry.rate_limit
                if code not in rate_limit_policies
            )
    if entry.rate_limit_exempt is not None and (
        not isinstance(entry.rate_limit_exempt, str)
        or not entry.rate_limit_exempt.strip()
    ):
        problems.append("rate_limit_exempt needs a non-empty reason")
    if entry.rate_limit is not None and entry.rate_limit_exempt is not None:
        problems.append("an entry has either rate_limit or rate_limit_exempt, not both")
    if entry.notice_exempt is not None:
        if not isinstance(entry.notice_exempt, bool):
            problems.append("notice_exempt must be true or false")
        if entry.access != "authenticated":
            problems.append("notice_exempt is only for access = 'authenticated'")
    if entry.stream is not None:
        if not isinstance(entry.stream, bool):
            problems.append("stream must be true or false")
        if entry.method != "GET":
            problems.append("stream is only for GET")
    return problems


# --- Static check ----------------------------------------------------------------


def documentation_paths(app: FastAPI) -> frozenset[str]:
    """FastAPI's own documentation routes (plain Starlette routes, no rules)."""
    paths = (
        app.openapi_url,
        app.docs_url,
        app.redoc_url,
        app.swagger_ui_oauth2_redirect_url if app.docs_url else None,
    )
    return frozenset(p for p in paths if p)


def api_route_contexts(app: FastAPI) -> list[RouteContext]:
    """Every route of ``app``, including those of (lazily) included routers."""
    return list(iter_route_contexts(app.routes))


@dataclass
class _Routes:
    api: dict[tuple[str, str], RouteContext] = field(default_factory=dict)
    documentation: set[tuple[str, str]] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)

    @property
    def keys(self) -> set[tuple[str, str]]:
        return set(self.api) | self.documentation


def _collect_routes(app: FastAPI) -> _Routes:
    routes = _Routes()
    docs = documentation_paths(app)
    for context in api_route_contexts(app):
        route = context.original_route
        if isinstance(route, APIRoute):
            for method in sorted(context.methods or ()):
                routes.api[(method, str(context.path))] = context
        elif type(route) is Route and context.path in docs:
            # HEAD is added implicitly by Starlette; it is not listed.
            for method in sorted((context.methods or set()) - {"HEAD"}):
                routes.documentation.add((method, str(context.path)))
        else:
            routes.problems.append(
                f"{type(route).__name__} {context.path}:"
                " route type not covered by the matrix"
            )
    return routes


def _documents_problem(
    document: Mapping[str, Any], method: str, path: str, status: str
) -> bool:
    operation = document.get("paths", {}).get(path, {}).get(method.lower(), {})
    response = operation.get("responses", {}).get(status, {})
    schema = response.get("content", {}).get(PROBLEM_JSON, {}).get("schema", {})
    return bool(schema.get("$ref") == PROBLEM_DETAILS_REF)


def _reauth_problems(
    method: str,
    path: str,
    route: RouteContext,
    rule_kind: str,
    permission: str | None,
    document: Mapping[str, Any],
    sensitive_actions: Mapping[str, SensitiveAction],
) -> list[str]:
    """The route-side reauthentication problems (#26 E2)."""
    label = f"{method} {path}"
    problems: list[str] = []
    markers = route_reauth_rules(route)
    if len(markers) > 1:
        problems.append(f"{label}: more than one reauth marker ({len(markers)})")
    for marker in markers:
        if marker.action not in sensitive_actions:
            problems.append(
                f"{label}: reauth action {marker.action!r} is not in SENSITIVE_ACTIONS"
            )
    if markers:
        if rule_kind == "public":
            problems.append(f"{label}: a public route cannot require reauthentication")
        if method not in UNSAFE_METHODS:
            problems.append(
                f"{label}: reauthentication is only for POST, PUT, PATCH and DELETE"
            )
        if not _documents_problem(document, method, path, "403"):
            problems.append(
                f"{label}: 403 reauth_required is not documented as ProblemDetails"
                " (pass REAUTH_RESPONSES)"
            )
    if method in UNSAFE_METHODS and rule_kind == "permission" and permission:
        binding = {
            a.code
            for a in sensitive_actions.values()
            if permission in a.bound_permissions
        }
        if binding and not any(m.action in binding for m in markers):
            problems.append(
                f"{label}: {permission!r} is bound to reauthentication"
                f" ({', '.join(sorted(binding))}): add require_recent_auth"
            )
    return problems


def _reauth_entry_problems(
    label: str, route: RouteContext, entry: MatrixEntry
) -> list[str]:
    """Marker and matrix entry must agree (#26 E2)."""
    markers = route_reauth_rules(route)
    if len(markers) > 1:
        return []
    if not markers:
        if entry.reauth is not None:
            return [
                f"{label}: the matrix says reauth {entry.reauth!r}"
                " but the route has no reauth marker"
            ]
        return []
    [marker] = markers
    if entry.reauth is None:
        return [
            f"{label}: the route requires reauthentication ({marker.action!r})"
            " but the matrix has no reauth"
        ]
    problems: list[str] = []
    if marker.action != entry.reauth:
        problems.append(
            f"{label}: the route's reauth action is {marker.action!r}"
            f" but the matrix says {entry.reauth!r}"
        )
    if marker.conditional != (entry.reauth_conditional is True):
        problems.append(
            f"{label}: the route's reauth_conditional is"
            f" {str(marker.conditional).lower()} but the matrix says"
            f" {str(entry.reauth_conditional is True).lower()}"
        )
    return problems


def _rate_limit_problems(
    method: str,
    path: str,
    route: RouteContext,
    rule_kind: str,
    entry: MatrixEntry | None,
    document: Mapping[str, Any],
    rate_limit_policies: Mapping[str, RateLimitPolicy],
) -> list[str]:
    """The rate-limit problems of one route (#27 G2)."""
    label = f"{method} {path}"
    problems: list[str] = []
    markers = route_rate_limit_rules(route)
    if len(markers) > 1:
        problems.append(f"{label}: more than one rate_limit marker ({len(markers)})")
    codes = {code for marker in markers for code in marker.codes}
    problems.extend(
        f"{label}: rate-limit policy {code!r} is not in RATE_LIMIT_POLICIES"
        for code in sorted(codes)
        if code not in rate_limit_policies
    )
    if markers and not _documents_problem(document, method, path, "429"):
        problems.append(
            f"{label}: 429 rate_limited is not documented as ProblemDetails"
            " (pass RATE_LIMIT_RESPONSES)"
        )
    if entry is None:
        return problems
    exempt = entry.rate_limit_exempt is not None
    if (
        method in UNSAFE_METHODS
        and rule_kind == "public"
        and not markers
        and not exempt
    ):
        problems.append(
            f"{label}: an unsafe public route needs rate_limit(...) or"
            " rate_limit_exempt (see app/core/ratelimit.py)"
        )
    listed = entry.rate_limit if isinstance(entry.rate_limit, list) else []
    if len(markers) <= 1 and codes != set(listed):
        problems.append(
            f"{label}: the route's rate limits are {sorted(codes)}"
            f" but the matrix says {sorted(set(listed))}"
        )
    return problems


def _policy_coverage_problems(
    routes: Mapping[tuple[str, str], RouteContext],
    rate_limit_policies: Mapping[str, RateLimitPolicy],
    pending_policies: Mapping[str, str],
) -> list[str]:
    """Each policy is applied by a route or pending, never both (#27 G2)."""
    applied = {
        code
        for route in routes.values()
        for marker in route_rate_limit_rules(route)
        for code in marker.codes
    }
    problems: list[str] = []
    for code in sorted(rate_limit_policies):
        if code not in applied and code not in pending_policies:
            problems.append(
                f"rate-limit policy {code!r}: no route applies it and it is not"
                " in PENDING_POLICIES"
            )
        if code in applied and code in pending_policies:
            problems.append(
                f"rate-limit policy {code!r}: a route applies it but it is still"
                f" in PENDING_POLICIES ({pending_policies[code]}): remove it"
            )
    problems.extend(
        f"rate-limit policy {code!r}: in PENDING_POLICIES but not in"
        " RATE_LIMIT_POLICIES"
        for code in sorted(pending_policies)
        if code not in rate_limit_policies
    )
    return problems


def _is_event_stream(route: RouteContext) -> bool:
    """Whether the route answers with an event stream (#42)."""
    original = route.original_route
    if not isinstance(original, APIRoute):
        return False
    response_class = original.response_class
    return isinstance(response_class, type) and issubclass(
        response_class, EventStreamResponse
    )


def check_matrix(
    app: FastAPI,
    entries: list[MatrixEntry],
    *,
    permission_codes: Collection[str] = PERMISSION_CODES,
    role_codes: Collection[str] = ROLE_CODES,
    sensitive_actions: Mapping[str, SensitiveAction] = SENSITIVE_ACTIONS,
    rate_limit_policies: Mapping[str, RateLimitPolicy] = RATE_LIMIT_POLICIES,
    pending_policies: Mapping[str, str] | None = None,
) -> list[str]:
    """Every problem between ``app``'s routes and ``entries`` (sorted).

    ``pending_policies`` (the real app passes
    :data:`~app.core.ratelimit.PENDING_POLICIES`) also checks that every
    rate-limit policy is applied by a route or pending, never both; a
    synthetic app leaves it out.
    """
    routes = _collect_routes(app)
    problems = list(routes.problems)
    document = app.openapi()

    counts = Counter(entry.key for entry in entries)
    problems.extend(
        f"{method} {path}: duplicate entry"
        for (method, path), n in counts.items()
        if n > 1
    )
    by_key = {entry.key: entry for entry in entries}
    for entry in entries:
        problems.extend(
            f"{entry.label}: {problem}"
            for problem in entry_problems(
                entry,
                permission_codes=permission_codes,
                role_codes=role_codes,
                sensitive_actions=sensitive_actions,
                rate_limit_policies=rate_limit_policies,
            )
        )
        if entry.key not in routes.keys:
            problems.append(f"{entry.label}: stale entry (no such route)")

    for key in sorted(routes.keys - set(by_key)):
        problems.append(f"{key[0]} {key[1]}: no matrix entry")

    for key in sorted(routes.documentation):
        entry = by_key.get(key)
        if entry is not None and entry.access != "public":
            problems.append(f"{entry.label}: a documentation route is public")

    for (method, path), route in sorted(routes.api.items()):
        label = f"{method} {path}"
        rules = route_access_rules(route)
        if not rules:
            problems.append(f"{label}: no access rule")
            continue
        if len(rules) > 1:
            problems.append(f"{label}: more than one access rule ({len(rules)})")
            continue
        rule = rules[0]
        statuses = {"authenticated": ["401"], "permission": ["401", "403"]}
        for status in statuses.get(rule.kind, []):
            if not _documents_problem(document, method, path, status):
                problems.append(
                    f"{label}: {status} is not documented as ProblemDetails"
                    " (pass AUTH_RESPONSES or PERMISSION_RESPONSES)"
                )
        if (
            rule.kind == "authenticated"
            and method in UNSAFE_METHODS
            and not _documents_problem(document, method, path, "403")
        ):
            problems.append(
                f"{label}: 403 csrf_failed is not documented as ProblemDetails"
                " (add CSRF_RESPONSES)"
            )
        problems.extend(
            _reauth_problems(
                method,
                path,
                route,
                rule.kind,
                rule.permission,
                document,
                sensitive_actions,
            )
        )
        entry = by_key.get((method, path))
        problems.extend(
            _rate_limit_problems(
                method, path, route, rule.kind, entry, document, rate_limit_policies
            )
        )
        if entry is None:
            continue
        problems.extend(_reauth_entry_problems(label, route, entry))
        streams = _is_event_stream(route)
        if streams != entry.is_stream:
            problems.append(
                f"{label}: the route's stream is {str(streams).lower()}"
                f" but the matrix says {str(entry.is_stream).lower()}"
            )
        if rule.notice_exempt != entry.is_notice_exempt:
            problems.append(
                f"{label}: the route's notice_exempt is"
                f" {str(rule.notice_exempt).lower()} but the matrix says"
                f" {str(entry.is_notice_exempt).lower()}"
            )
        if rule.kind != entry.access:
            problems.append(
                f"{label}: the route is {rule.kind} but the matrix says {entry.access}"
            )
        elif rule.kind == "permission" and rule.permission != entry.permission:
            problems.append(
                f"{label}: the route requires {rule.permission!r}"
                f" but the matrix says {entry.permission!r}"
            )
    if pending_policies is not None:
        problems.extend(
            _policy_coverage_problems(routes.api, rate_limit_policies, pending_policies)
        )
    return sorted(set(problems))


def failure_message(problems: list[str]) -> str:
    return (
        "Permission matrix problems (tests/authz/matrix.toml, see"
        " _docs/testing-guidelines.md 'Permission-matrix approach'):\n"
        + "\n".join(problems)
    )


# --- Runtime check ---------------------------------------------------------------

Outcome = Literal["allowed", "denied", "unauthenticated"]


@dataclass(frozen=True)
class Actor:
    """Who sends a request: anonymous, a role, or a delegated Administrator."""

    role: str | None = None
    delegation: str | None = None
    notice_pending: bool = False
    """A user who has not accepted the current privacy-notice version (#174)."""

    @property
    def label(self) -> str:
        if self.role is None:
            return "anonymous"
        if self.delegation is not None:
            return f"{self.role} with delegated {self.delegation}"
        if self.notice_pending:
            return f"{self.role} without the privacy notice"
        return self.role


ANONYMOUS = Actor()
NOTICE_PENDING_OWNER = Actor(OWNER, notice_pending=True)
"""The actor of the ``notice:pending`` cases (#174 E2)."""


@dataclass(frozen=True)
class Case:
    entry: MatrixEntry
    actor: Actor

    @property
    def id(self) -> str:
        return f"{self.entry.label} as {self.actor.label}"

    @property
    def expected(self) -> Outcome:
        entry, actor = self.entry, self.actor
        if entry.access == "public":
            return "allowed"
        if actor.role is None:
            return "unauthenticated"
        if entry.access == "authenticated":
            return "allowed"
        if actor.role in entry.allowed_roles or (
            actor.delegation is not None and actor.delegation == entry.permission
        ):
            return "allowed"
        return "denied"

    @property
    def reauthenticates(self) -> bool:
        """Only allowed actors reauthenticate (#26 E3): a denied one must
        still get ``permission_denied`` first."""
        return self.entry.needs_reauth and self.expected == "allowed"


def actors_for(
    entry: MatrixEntry,
    *,
    role_codes: Iterable[str] = ROLE_CODES,
    delegable: Collection[str] = DELEGABLE_PERMISSIONS,
) -> list[Actor]:
    actors = [ANONYMOUS, *(Actor(role) for role in role_codes)]
    if entry.access == "permission" and entry.permission in delegable:
        actors.append(Actor(ADMINISTRATOR, delegation=entry.permission))
    return actors


def matrix_cases(
    entries: Iterable[MatrixEntry],
    *,
    role_codes: Iterable[str] = ROLE_CODES,
    delegable: Collection[str] = DELEGABLE_PERMISSIONS,
) -> list[Case]:
    role_codes = tuple(role_codes)
    return [
        Case(entry, actor)
        for entry in entries
        for actor in actors_for(entry, role_codes=role_codes, delegable=delegable)
    ]


def all_actors(cases: Iterable[Case]) -> list[Actor]:
    return sorted({case.actor for case in cases}, key=lambda a: a.label)


def _placeholder(route: RouteContext | None, name: str) -> str:
    if route is not None:
        for param in route.dependant.path_params:
            if param.name == name and param.field_info.annotation is int:
                return PLACEHOLDER_INT
    return PLACEHOLDER_UUID


def request_path(app: FastAPI, entry: MatrixEntry) -> str:
    """The entry's path template with its parameters filled in."""
    route = next(
        (
            r
            for r in api_route_contexts(app)
            if isinstance(r.original_route, APIRoute)
            and r.path == entry.path
            and entry.method in (r.methods or ())
        ),
        None,
    )
    given = entry.path_params or {}
    return _PATH_PARAM.sub(
        lambda m: str(given.get(m.group(1), _placeholder(route, m.group(1)))),
        str(entry.path),
    )


def _problem_code(response: Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


def check_response(case: Case, response: Response) -> list[str]:
    """The problems of one response against the case's expected outcome."""
    status, code = response.status_code, _problem_code(response)
    got = f"got {status} {code}" if code else f"got {status}"
    problems: list[str] = []
    expected = case.expected
    if expected == "allowed" and status in (401, 403):
        problems.append(f"{case.id}: expected access, {got}")
    elif expected == "denied" and (status, code) != (403, "permission_denied"):
        problems.append(f"{case.id}: expected 403 permission_denied, {got}")
    elif expected == "unauthenticated" and (status, code) != (
        401,
        "authentication_required",
    ):
        problems.append(f"{case.id}: expected 401 authentication_required, {got}")
    if status in (401, 403) and response.headers.get("content-type") != PROBLEM_JSON:
        problems.append(f"{case.id}: {status} is not {PROBLEM_JSON}")
    return problems


SignIn = Literal["login", "override"]


@dataclass(frozen=True)
class MatrixUser:
    """A real, active database user who is one actor."""

    username: str
    principal: Principal


class LoginFailedError(AssertionError):
    """The harness could not sign an actor in."""


@dataclass(frozen=True)
class SignedIn:
    """A fresh session: the ``Cookie`` header to send and its CSRF token."""

    cookie: str
    csrf_token: str


def login_session(client: TestClient, username: str) -> SignedIn:
    """Log ``username`` in through the API; the new session's cookie and token.

    Parses the raw ``Set-Cookie`` header (a ``Secure`` cookie is not sent back
    over ``http://`` by an HTTP client's jar) and leaves the client's own cookie
    jar empty.
    """
    client.cookies.clear()
    response = client.post(
        LOGIN_PATH,
        json={"username": username, "password": TEST_USER_PASSWORD},
        headers=ORIGIN_HEADER,
    )
    client.cookies.clear()
    if response.status_code != 200:
        raise LoginFailedError(f"login as {username}: got {response.status_code}")
    [header] = response.headers.get_list("set-cookie")
    cookie = SimpleCookie()
    cookie.load(header)
    [(name, morsel)] = cookie.items()
    return SignedIn(f"{name}={morsel.value}", str(response.json()["csrf_token"]))


def login_cookie(client: TestClient, username: str) -> str:
    """Log ``username`` in through the API; the ``Cookie`` header to send."""
    return login_session(client, username).cookie


def reauthenticate_session(client: TestClient, signed_in: SignedIn) -> None:
    """Reauthenticate the session through the API (#26)."""
    client.cookies.clear()
    response = client.post(
        REAUTH_PATH,
        json={"password": TEST_USER_PASSWORD},
        headers={
            **ORIGIN_HEADER,
            "Cookie": signed_in.cookie,
            CSRF_HEADER: signed_in.csrf_token,
        },
    )
    client.cookies.clear()
    if response.status_code != 200:
        raise LoginFailedError(f"reauthenticate: got {response.status_code}")


CsrfCheck = Literal["no-origin", "no-token"]


def _send(
    client: TestClient,
    entry: MatrixEntry,
    actor: Actor,
    users: Mapping[Actor, MatrixUser],
    *,
    sign_in: SignIn,
    omit: CsrfCheck | None = None,
    reauthenticate: bool = False,
) -> Response:
    """Send ``entry``'s request as ``actor``.

    With ``Origin`` (unless ``omit="no-origin"``) and, for a signed-in actor
    on an unsafe method, the session's ``X-CSRF-Token`` (unless
    ``omit="no-token"``). ``reauthenticate`` first reauthenticates the
    actor's session (``login``: through the API; ``override``: the
    overriding session is reauthenticated now).
    """
    app = client.app
    assert isinstance(app, FastAPI)
    method = str(entry.method)
    headers: dict[str, str] = {}
    csrf_token: str | None = None
    overrides = app.dependency_overrides
    previous = overrides.pop(get_current_session, None)
    if actor != ANONYMOUS:
        if sign_in == "login":
            signed_in = login_session(client, users[actor].username)
            if reauthenticate:
                reauthenticate_session(client, signed_in)
            headers["Cookie"] = signed_in.cookie
            csrf_token = signed_in.csrf_token
        else:
            resolved = ResolvedSession(
                users[actor].principal,
                OVERRIDE_SESSION_TOKEN,
                reauthenticated_at=clock.now() if reauthenticate else None,
            )
            overrides[get_current_session] = lambda: resolved
            csrf_token = csrf_token_for(OVERRIDE_SESSION_TOKEN)
    if omit != "no-origin":
        headers.update(ORIGIN_HEADER)
    if csrf_token is not None and method in UNSAFE_METHODS and omit != "no-token":
        headers[CSRF_HEADER] = csrf_token
    try:
        client.cookies.clear()
        request = client.build_request(
            method, request_path(app, entry), json=entry.json, headers=headers
        )
        if omit == "no-origin" and "origin" in request.headers:
            del request.headers["origin"]
        if entry.is_stream:
            return _send_stream(client, request)
        return client.send(request)
    finally:
        client.cookies.clear()
        overrides.pop(get_current_session, None)
        if previous is not None:
            overrides[get_current_session] = previous


def _send_stream(client: TestClient, request: Any) -> Response:
    """A ``stream = true`` request: a ``200`` stream is closed as soon as its
    status and headers arrived (its body would never end); any other answer
    is read to the end."""
    with ClientStream(
        client, request.method, str(request.url), headers=dict(request.headers)
    ) as stream:
        if stream.status == 200:
            return Response(200, headers=stream.headers)
        return stream.response()


def run_case(
    client: TestClient,
    case: Case,
    users: Mapping[Actor, MatrixUser],
    *,
    sign_in: SignIn = "login",
) -> list[str]:
    """Send the case's request as its actor and return its problems.

    ``sign_in="login"``: a fresh login per case (cookie sent by hand).
    ``sign_in="override"``: the actor's principal replaces
    :func:`~app.core.authz.get_current_session` for this request.
    """
    response = _send(
        client,
        case.entry,
        case.actor,
        users,
        sign_in=sign_in,
        reauthenticate=case.reauthenticates,
    )
    return check_response(case, response)


def run_cases(
    client: TestClient,
    cases: Iterable[Case],
    users: Mapping[Actor, MatrixUser],
    *,
    sign_in: SignIn = "login",
) -> list[str]:
    return [
        problem
        for case in cases
        for problem in run_case(client, case, users, sign_in=sign_in)
    ]


# --- CSRF cases (#24 F2) -------------------------------------------------------------


@dataclass(frozen=True)
class CsrfCase:
    """One unsafe entry sent without ``Origin`` or without the CSRF token."""

    entry: MatrixEntry
    check: CsrfCheck
    actor: Actor

    @property
    def id(self) -> str:
        return f"{self.entry.label} csrf:{self.check}"

    @property
    def expects_csrf_failed(self) -> bool:
        return self.check == "no-origin" or self.entry.access != "public"


def _allowed_actor(
    entry: MatrixEntry,
    role_codes: tuple[str, ...],
    delegable: Collection[str],
) -> Actor:
    if entry.access == "public":
        return ANONYMOUS
    if entry.access == "permission":
        allowed = [role for role in role_codes if role in entry.allowed_roles]
        if allowed:
            return Actor(allowed[0])
        if entry.permission in delegable:
            return Actor(ADMINISTRATOR, delegation=entry.permission)
    return Actor(role_codes[0])


def csrf_cases(
    entries: Iterable[MatrixEntry],
    *,
    role_codes: Iterable[str] = ROLE_CODES,
    delegable: Collection[str] = DELEGABLE_PERMISSIONS,
) -> list[CsrfCase]:
    """``csrf:no-origin`` and ``csrf:no-token`` for every unsafe entry."""
    role_codes = tuple(role_codes)
    checks: tuple[CsrfCheck, ...] = ("no-origin", "no-token")
    return [
        CsrfCase(entry, check, _allowed_actor(entry, role_codes, delegable))
        for entry in entries
        if entry.method in UNSAFE_METHODS
        for check in checks
    ]


def check_csrf_response(case: CsrfCase, response: Response) -> list[str]:
    status, code = response.status_code, _problem_code(response)
    got = f"got {status} {code}" if code else f"got {status}"
    if case.expects_csrf_failed:
        if (status, code) != (403, "csrf_failed"):
            return [f"{case.id}: expected 403 csrf_failed, {got}"]
        if response.headers.get("content-type") != PROBLEM_JSON:
            return [f"{case.id}: 403 is not {PROBLEM_JSON}"]
        return []
    if status == 403:
        return [f"{case.id}: expected no CSRF token check, {got}"]
    return []


def run_csrf_case(
    client: TestClient,
    case: CsrfCase,
    users: Mapping[Actor, MatrixUser],
    *,
    sign_in: SignIn = "login",
) -> list[str]:
    response = _send(
        client, case.entry, case.actor, users, sign_in=sign_in, omit=case.check
    )
    return check_csrf_response(case, response)


def run_csrf_cases(
    client: TestClient,
    cases: Iterable[CsrfCase],
    users: Mapping[Actor, MatrixUser],
    *,
    sign_in: SignIn = "login",
) -> list[str]:
    return [
        problem
        for case in cases
        for problem in run_csrf_case(client, case, users, sign_in=sign_in)
    ]


# --- Reauthentication cases (#26 E3) ------------------------------------------------


@dataclass(frozen=True)
class ReauthCase:
    """An unconditional ``reauth`` entry sent by an allowed actor who has not
    reauthenticated (valid ``Origin`` and CSRF token)."""

    entry: MatrixEntry
    actor: Actor

    @property
    def id(self) -> str:
        return f"{self.entry.label} reauth:none"


def reauth_cases(
    entries: Iterable[MatrixEntry],
    *,
    role_codes: Iterable[str] = ROLE_CODES,
    delegable: Collection[str] = DELEGABLE_PERMISSIONS,
) -> list[ReauthCase]:
    """One ``reauth:none`` case per unconditional ``reauth`` entry."""
    role_codes = tuple(role_codes)
    return [
        ReauthCase(entry, _allowed_actor(entry, role_codes, delegable))
        for entry in entries
        if entry.needs_reauth
    ]


def check_reauth_response(case: ReauthCase, response: Response) -> list[str]:
    status, code = response.status_code, _problem_code(response)
    got = f"got {status} {code}" if code else f"got {status}"
    if (status, code) != (403, "reauth_required"):
        return [f"{case.id}: expected 403 reauth_required, {got}"]
    if response.headers.get("content-type") != PROBLEM_JSON:
        return [f"{case.id}: 403 is not {PROBLEM_JSON}"]
    return []


def run_reauth_case(
    client: TestClient,
    case: ReauthCase,
    users: Mapping[Actor, MatrixUser],
    *,
    sign_in: SignIn = "login",
) -> list[str]:
    response = _send(client, case.entry, case.actor, users, sign_in=sign_in)
    return check_reauth_response(case, response)


def run_reauth_cases(
    client: TestClient,
    cases: Iterable[ReauthCase],
    users: Mapping[Actor, MatrixUser],
    *,
    sign_in: SignIn = "login",
) -> list[str]:
    return [
        problem
        for case in cases
        for problem in run_reauth_case(client, case, users, sign_in=sign_in)
    ]


# --- Privacy-notice cases (#174 E2) ------------------------------------------------


@dataclass(frozen=True)
class NoticeCase:
    """A protected entry sent by an Owner who has not accepted the current
    privacy-notice version (valid ``Origin`` and CSRF token)."""

    entry: MatrixEntry
    actor: Actor = NOTICE_PENDING_OWNER

    @property
    def id(self) -> str:
        return f"{self.entry.label} notice:pending"


def notice_cases(entries: Iterable[MatrixEntry]) -> list[NoticeCase]:
    """One ``notice:pending`` case per ``authenticated`` or ``permission`` entry."""
    return [
        NoticeCase(entry)
        for entry in entries
        if entry.access in ("authenticated", "permission")
    ]


def check_notice_response(case: NoticeCase, response: Response) -> list[str]:
    status, code = response.status_code, _problem_code(response)
    got = f"got {status} {code}" if code else f"got {status}"
    gated = (status, code) == (403, "privacy_notice_required")
    if case.entry.is_notice_exempt:
        if gated:
            return [f"{case.id}: a notice-exempt route answered {status} {code}"]
        return []
    if not gated:
        return [f"{case.id}: expected 403 privacy_notice_required, {got}"]
    if response.headers.get("content-type") != PROBLEM_JSON:
        return [f"{case.id}: 403 is not {PROBLEM_JSON}"]
    return []


def run_notice_case(
    client: TestClient,
    case: NoticeCase,
    users: Mapping[Actor, MatrixUser],
    *,
    sign_in: SignIn = "login",
) -> list[str]:
    response = _send(client, case.entry, case.actor, users, sign_in=sign_in)
    return check_notice_response(case, response)


def run_notice_cases(
    client: TestClient,
    cases: Iterable[NoticeCase],
    users: Mapping[Actor, MatrixUser],
    *,
    sign_in: SignIn = "login",
) -> list[str]:
    return [
        problem
        for case in cases
        for problem in run_notice_case(client, case, users, sign_in=sign_in)
    ]


# --- Real users ----------------------------------------------------------------------


async def matrix_user_for(session: AsyncSession, actor: Actor) -> MatrixUser:
    """A new active user who is ``actor``, with its loaded principal.

    Created with the #20 factory helper (one role, plus the delegation if any;
    password :data:`TEST_USER_PASSWORD`) and loaded with
    ``identity.service.load_principal``. The user has accepted the current
    privacy-notice version (#174 E3) unless ``actor.notice_pending``.
    Flushes only.
    """
    if actor.role is None:
        raise ValueError("anonymous has no user")
    user = await persist_user(
        session,
        roles=[actor.role],
        delegations=[actor.delegation] if actor.delegation else [],
        privacy_notice_accepted=not actor.notice_pending,
    )
    principal = await identity.load_principal(session, user.id)
    assert principal is not None
    return MatrixUser(username=user.username, principal=principal)


async def matrix_users_for(
    session: AsyncSession, actors: Iterable[Actor]
) -> dict[Actor, MatrixUser]:
    """:func:`matrix_user_for` for every non-anonymous actor (flushes only)."""
    return {
        actor: await matrix_user_for(session, actor)
        for actor in actors
        if actor != ANONYMOUS
    }


async def principal_for(session: AsyncSession, actor: Actor) -> Principal:
    """The principal of a new active user who is ``actor`` (for tests that
    override ``get_current_principal`` instead of logging in). It has
    accepted the current privacy notice unless ``actor.notice_pending``."""
    return (await matrix_user_for(session, actor)).principal
