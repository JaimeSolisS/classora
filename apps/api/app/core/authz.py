"""Authorization: the current user, route access rules and record policies.

Architecture §4.3 steps 4-5: the route checks a permission, the service runs
the record policy. ADR-0007 D4 defines the permissions; ``identity`` computes
them (``identity.service.load_principal``). This module stays below the domain
modules (ADR-0002) and imports none of them.

**Current user.** :func:`get_current_principal` is the only dependency that
yields the signed-in user. It gets the request's session from
:func:`get_current_session`, which calls the :data:`PrincipalResolver` that
the app registered with :func:`register_principal_resolver` (``build_app``
registers the identity module's session resolver, #23), so this module still
imports no domain module. Without a registered resolver it answers
``401 authentication_required`` (fails closed). Tests replace either
dependency through ``app.dependency_overrides``.

**CSRF (#24).** On ``POST``, ``PUT``, ``PATCH`` and ``DELETE``,
:func:`get_current_principal` checks the ``X-CSRF-Token`` header against the
resolved session's token (``app.core.csrf.verify_csrf_token``) before it
returns the principal. Every ``authenticated()`` and ``require()`` route is
therefore protected by construction, in the order ``401`` (no session), ``403
csrf_failed``, ``403 permission_denied``, then ``422``. ``public()`` routes
need no token; the ``Origin`` check (``app.core.csrf.require_trusted_origin``)
covers every route. Overriding ``get_current_principal`` in a test also skips
the token check; override :func:`get_current_session` to keep it.

**Route access.** Every route declares exactly one of:

- ``public(reason=...)``: no authentication (the reason is kept as metadata);
- ``authenticated()``: any signed-in user, yields the :class:`Principal`;
- ``require(permission)``: holders of one permission, yields the
  :class:`Principal`.

Each works in ``dependencies=[...]`` on a route or router, or as an annotated
parameter (``principal: Annotated[Principal, require("user.view")]``). Put it
before any other dependency so ``401``/``403`` come first. FastAPI solves the
dependencies before it validates the route's own path, query and body
parameters, so an unauthorized caller gets ``401``/``403``, never ``422``. The
exception: a body that is not parseable JSON is rejected with ``422`` before
any dependency runs (FastAPI 0.143.3); that response reveals nothing.
Protected routes pass :data:`AUTH_RESPONSES` or :data:`PERMISSION_RESPONSES`
as ``responses=`` so the OpenAPI document lists the errors; an unsafe
``authenticated()`` route adds :data:`CSRF_RESPONSES`.

**Audit actor.** ``authenticated()`` and ``require()`` record the principal
as the request's audit actor (``app.core.audit``), so every audit event the
request writes names it; a ``public()`` route records none.

**Records.** Services check record-level policies with
:func:`authorize_record`: a record that is missing or hidden from the caller is
``404 not_found`` (the same body as an unknown path); a visible record the
caller may not act on is ``403 permission_denied``. Policies are pure
``can_<action>_<resource>(principal, record, ...)`` functions in the module's
``permissions.py``.
"""

import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Annotated, Any, Final, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_request_actor
from app.core.config import Settings, get_settings
from app.core.csrf import verify_csrf_token
from app.core.errors import AppError, problem_response_doc
from app.core.uow import UnitOfWorkDep

# --- Principal and errors --------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """The signed-in user as authorization sees it.

    Holds no secret (no password hash, no session token). ``repr`` shows only
    the id and the role codes.
    """

    user_id: uuid.UUID
    roles: frozenset[str]
    permissions: frozenset[str] = field(repr=False)

    def has(self, permission: str) -> bool:
        return permission in self.permissions


class AuthenticationRequiredError(AppError):
    """No signed-in user. Takes no arguments, so nothing else can leak."""

    status = 401
    code = "authentication_required"
    detail = "Sign in to continue."

    def __init__(self) -> None:
        super().__init__()


class PermissionDeniedError(AppError):
    """The user lacks the permission. Never names the permission or record."""

    status = 403
    code = "permission_denied"
    detail = "You do not have permission to do this."

    def __init__(self) -> None:
        super().__init__()


class RecordNotFoundError(AppError):
    """A record that is missing or not visible to the caller.

    ``404 not_found`` with no ``detail``: the same body as an unknown path
    (apart from ``request_id``). Domains raise this, never their own 404, for a
    record that is missing or hidden.
    """

    status = 404
    code = "not_found"

    def __init__(self) -> None:
        super().__init__()


# --- The current-user seam -------------------------------------------------------


@dataclass(frozen=True)
class ResolvedSession:
    """The signed-in user and the raw token of the session that proved it.

    ``session_token`` is the secret from the session cookie: it is used only to
    check the CSRF token, and is never logged, stored or shown (``repr`` hides
    it).
    """

    principal: Principal
    session_token: bytes = field(repr=False)


PrincipalResolver = Callable[
    [Request, AsyncSession, Settings], Awaitable[ResolvedSession | None]
]
"""Resolves the request's credentials (the session cookie) to a session.

Called with the request, the request's Unit of Work session and the settings;
returns the principal together with the raw session token, or ``None`` when
there is no signed-in user. It must not write.
"""

PRINCIPAL_RESOLVER_STATE: Final = "principal_resolver"
"""``app.state`` attribute holding the registered :data:`PrincipalResolver`."""


def register_principal_resolver(app: FastAPI, resolver: PrincipalResolver) -> None:
    """Make ``resolver`` the way ``app`` finds the signed-in user."""
    setattr(app.state, PRINCIPAL_RESOLVER_STATE, resolver)


def _registered_resolver(request: Request) -> PrincipalResolver:
    # Solved before the Unit of Work and the settings, so an app without a
    # resolver (a test-only app without a database) answers 401, never 500.
    resolver: PrincipalResolver | None = getattr(
        request.app.state, PRINCIPAL_RESOLVER_STATE, None
    )
    if resolver is None:
        raise AuthenticationRequiredError()
    return resolver


async def get_current_session(
    request: Request,
    resolver: Annotated[PrincipalResolver, Depends(_registered_resolver)],
    uow: UnitOfWorkDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ResolvedSession:
    """The request's signed-in session (no CSRF check yet).

    Calls the app's registered :data:`PrincipalResolver` (since #23: session
    cookie -> live ``auth_sessions`` row -> ``identity.service.load_principal``)
    in the request's Unit of Work. No resolver, or no session, raises
    :class:`AuthenticationRequiredError`.
    """
    resolved = await resolver(request, uow.session, settings)
    if resolved is None:
        raise AuthenticationRequiredError()
    return resolved


async def get_current_principal(
    request: Request,
    resolved: Annotated[ResolvedSession, Depends(get_current_session)],
) -> Principal:
    """The signed-in user: the only dependency that yields it.

    The session comes from :func:`get_current_session`, which calls the app's
    registered :data:`PrincipalResolver` (``401`` without a session).
    On ``POST``, ``PUT``, ``PATCH`` and ``DELETE`` the ``X-CSRF-Token`` header
    must then match the session (``403 csrf_failed`` otherwise, #24). Tests
    replace it through ``app.dependency_overrides[get_current_principal]``
    (which skips the CSRF check) or :func:`get_current_session` (which keeps
    it), and the cookie mode through ``app.dependency_overrides[get_settings]``.
    """
    verify_csrf_token(request, resolved.session_token)
    return resolved.principal


def ensure_permission(principal: Principal, permission: str) -> None:
    """Raise :class:`PermissionDeniedError` unless ``principal`` holds it.

    Used by :func:`require`, and by services, workflows and jobs outside a route.
    """
    if not principal.has(permission):
        raise PermissionDeniedError()


# --- Route access declarations ---------------------------------------------------

AccessKind = Literal["public", "authenticated", "permission"]

PERMISSION_CODE_PATTERN: Final = re.compile(r"[a-z_]+(\.[a-z_]+)+")

ROUTE_MARKER_ATTR: Final = "__classora_route_marker__"
"""Attribute on a dependency callable holding its route marker (any type)."""


@dataclass(frozen=True)
class AccessRule:
    """The metadata of one access declaration (read by the matrix test)."""

    kind: AccessKind
    permission: str | None = None
    reason: str | None = None


def _mark[F: Callable[..., Any]](dependency: F, marker: object) -> F:
    setattr(dependency, ROUTE_MARKER_ATTR, marker)
    return dependency


def _as_principal(principal: object) -> Principal:
    # get_current_principal raises when there is no user; an override that
    # returns anything else (None in a test) still counts as anonymous.
    if not isinstance(principal, Principal):
        raise AuthenticationRequiredError()
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def public(*, reason: str) -> Any:
    """No authentication. ``reason`` (non-empty) says why the route is public."""
    if not reason.strip():
        raise ValueError("public() needs a non-empty reason")

    async def public_access() -> None:
        return None

    return Depends(_mark(public_access, AccessRule("public", reason=reason)))


def authenticated() -> Any:
    """Any signed-in user; yields the :class:`Principal`."""

    async def authenticated_access(
        request: Request, principal: CurrentPrincipal
    ) -> Principal:
        checked = _as_principal(principal)
        record_request_actor(request, checked.user_id)
        return checked

    return Depends(_mark(authenticated_access, AccessRule("authenticated")))


def require(permission: str) -> Any:
    """Holders of ``permission``; yields the :class:`Principal`.

    ``401`` without a principal, then ``403`` without the permission. The code
    format is checked here (so a typo fails at import time); catalog membership
    is checked by the permission-matrix test.
    """
    if PERMISSION_CODE_PATTERN.fullmatch(permission) is None:
        raise ValueError(f"not a permission code: {permission!r}")

    async def permission_access(
        request: Request, principal: CurrentPrincipal
    ) -> Principal:
        checked = _as_principal(principal)
        ensure_permission(checked, permission)
        record_request_actor(request, checked.user_id)
        return checked

    return Depends(
        _mark(permission_access, AccessRule("permission", permission=permission))
    )


def _walk(dependant: Dependant) -> Iterator[Callable[..., Any] | None]:
    for sub in dependant.dependencies:
        yield sub.call
        yield from _walk(sub)


def route_markers[M](route: APIRoute | RouteContext, marker_type: type[M]) -> list[M]:
    """Every marker of ``marker_type`` on ``route``'s dependency tree.

    Walks recursively, so markers set on the router, on the route and inside
    sub-dependencies are all found. A dependency reached twice counts once.
    Pass the contexts of ``fastapi.routing.iter_route_contexts(app.routes)``:
    FastAPI 0.143 includes routers lazily, so ``app.routes`` alone does not
    list their routes, and only the context carries the dependencies added by
    ``include_router``.
    """
    dependant: Dependant = route.dependant
    seen: set[int] = set()
    markers: list[M] = []
    for call in _walk(dependant):
        marker = getattr(call, ROUTE_MARKER_ATTR, None)
        if isinstance(marker, marker_type) and id(call) not in seen:
            seen.add(id(call))
            markers.append(marker)
    return markers


def route_access_rules(route: APIRoute | RouteContext) -> list[AccessRule]:
    """The access rules declared on ``route`` (exactly one is expected)."""
    return route_markers(route, AccessRule)


AUTH_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: problem_response_doc("Authentication required."),
}
"""``responses=`` for routes declared with :func:`authenticated`."""

CSRF_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    403: problem_response_doc(
        "The request could not be verified (csrf_failed): the Origin or the"
        " X-CSRF-Token check failed."
    ),
}
"""``responses=`` addition for unsafe routes (``POST``, ``PUT``, ``PATCH``,
``DELETE``) declared with :func:`authenticated` or :func:`public`."""

PERMISSION_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **AUTH_RESPONSES,
    403: problem_response_doc(
        "Permission denied (permission_denied); on POST, PUT, PATCH and DELETE"
        " also a failed Origin or X-CSRF-Token check (csrf_failed)."
    ),
}
"""``responses=`` for routes declared with :func:`require`."""


# --- Record-level policies -------------------------------------------------------


def authorize_record[T](
    record: T | None,
    *,
    can_view: Callable[[T], bool],
    can_act: Callable[[T], bool] | None = None,
) -> T:
    """Apply a record policy (the 404/403 rule).

    - ``record is None`` or ``can_view(record)`` is false: ``404 not_found``
      (:class:`RecordNotFoundError`): hidden looks exactly like missing.
    - ``can_act`` given and false: ``403 permission_denied`` (the caller may
      see the record, so this reveals nothing new).
    - Otherwise the record is returned.
    """
    if record is None or not can_view(record):
        raise RecordNotFoundError()
    if can_act is not None and not can_act(record):
        raise PermissionDeniedError()
    return record
