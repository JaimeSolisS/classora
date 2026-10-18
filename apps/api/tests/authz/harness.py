"""The permission-matrix harness (M2-03).

- :func:`load_matrix` reads ``matrix.toml`` (stdlib ``tomllib``).
- :func:`check_matrix` is the static check: it compares the matrix with an
  app's routes and their access rules, and lists every problem as
  ``METHOD path: reason``.
- :func:`matrix_cases` and :func:`run_case` are the runtime check: one request
  per entry and actor (anonymous, each role, and an Administrator holding the
  delegation for a delegable permission).
- :func:`principal_for` builds an actor's :class:`Principal` from a real
  database user. It is the single place #23 changes to real logins.
"""

import re
import tomllib
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Route

from app.core.authz import Principal, get_current_principal, route_access_rules
from app.core.errors import PROBLEM_DETAILS_REF, PROBLEM_JSON
from app.modules.identity import service as identity
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    DELEGABLE_PERMISSIONS,
    PERMISSION_CODES,
    ROLE_CODES,
)
from tests.factories.identity import persist_user

MATRIX_PATH = Path(__file__).with_name("matrix.toml")

ACCESS_KINDS = ("public", "authenticated", "permission")
ENTRY_FIELDS = frozenset(
    {"method", "path", "access", "permission", "allow", "reason", "path_params", "json"}
)
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

# Placeholder path parameter values when an entry gives none.
PLACEHOLDER_UUID = "00000000-0000-7000-8000-000000000001"
PLACEHOLDER_INT = "1"

_PATH_PARAM = re.compile(r"{([^}:]+)(?::[^}]*)?}")


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


def check_matrix(
    app: FastAPI,
    entries: list[MatrixEntry],
    *,
    permission_codes: Collection[str] = PERMISSION_CODES,
    role_codes: Collection[str] = ROLE_CODES,
) -> list[str]:
    """Every problem between ``app``'s routes and ``entries`` (sorted)."""
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
                entry, permission_codes=permission_codes, role_codes=role_codes
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
        entry = by_key.get((method, path))
        if entry is None:
            continue
        if rule.kind != entry.access:
            problems.append(
                f"{label}: the route is {rule.kind} but the matrix says {entry.access}"
            )
        elif rule.kind == "permission" and rule.permission != entry.permission:
            problems.append(
                f"{label}: the route requires {rule.permission!r}"
                f" but the matrix says {entry.permission!r}"
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

    @property
    def label(self) -> str:
        if self.role is None:
            return "anonymous"
        if self.delegation is not None:
            return f"{self.role} with delegated {self.delegation}"
        return self.role


ANONYMOUS = Actor()


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


def run_case(
    client: TestClient,
    case: Case,
    principals: Mapping[Actor, Principal],
) -> list[str]:
    """Send the case's request as its actor and return its problems.

    The actor's principal replaces :func:`get_current_principal` for this
    request; anonymous uses the real dependency.
    """
    app = client.app
    assert isinstance(app, FastAPI)
    overrides = app.dependency_overrides
    previous = overrides.pop(get_current_principal, None)
    if case.actor != ANONYMOUS:
        principal = principals[case.actor]
        overrides[get_current_principal] = lambda: principal
    try:
        response = client.request(
            str(case.entry.method),
            request_path(app, case.entry),
            json=case.entry.json,
        )
    finally:
        overrides.pop(get_current_principal, None)
        if previous is not None:
            overrides[get_current_principal] = previous
    return check_response(case, response)


def run_cases(
    client: TestClient,
    cases: Iterable[Case],
    principals: Mapping[Actor, Principal],
) -> list[str]:
    return [problem for case in cases for problem in run_case(client, case, principals)]


# --- Principals from real users ----------------------------------------------------


async def principal_for(session: AsyncSession, actor: Actor) -> Principal:
    """The principal of a new active user who is ``actor``.

    Creates the user with the #20 factory helper (one role, plus the
    delegation if any) and loads it with ``identity.service.load_principal``.
    #23 changes this one function to log in for real.
    """
    if actor.role is None:
        raise ValueError("anonymous has no principal")
    user = await persist_user(
        session,
        roles=[actor.role],
        delegations=[actor.delegation] if actor.delegation else [],
    )
    principal = await identity.load_principal(session, user.id)
    assert principal is not None
    return principal


async def principals_for(
    session: AsyncSession, actors: Iterable[Actor]
) -> dict[Actor, Principal]:
    """:func:`principal_for` for every non-anonymous actor (flushes only)."""
    return {
        actor: await principal_for(session, actor)
        for actor in actors
        if actor != ANONYMOUS
    }
