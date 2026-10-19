"""Reauthentication for sensitive actions (#26; ADR-0007 D10).

A signed-in session is not enough for the actions in
:data:`SENSITIVE_ACTIONS`: the user must have confirmed their password in
**this session** within :data:`REAUTH_WINDOW` (5 minutes). The window starts
at the reauthentication (``POST /api/v1/auth/reauthenticate``, identity
module, which sets ``auth_sessions.reauthenticated_at``) and is **not**
extended by activity. Logging in does not count as a reauthentication.

**Recent** means ``reauthenticated_at is not None and now <
reauthenticated_at + REAUTH_WINDOW`` with ``now`` from
:func:`app.core.clock.now` (:func:`is_recent`): at exactly ``+5m`` the
session is no longer recent.

**Declaring it on a route.**

- :func:`require_recent_auth` (``dependencies=[...]`` or an annotated
  parameter; yields the :class:`~app.core.authz.Principal`) for an action
  that is always sensitive. Use it together with the route's access
  declaration (``require(...)`` or ``authenticated()``) and pass
  :data:`REAUTH_RESPONSES`.
- :func:`recent_auth_guard` for an action that is sensitive only for some
  request bodies (a staff-role invitation, a settings change touching payment
  methods or the time zone, a user-state change to suspended or archived). It
  yields a :class:`RecentAuthGuard`; the handler or service calls
  :meth:`RecentAuthGuard.ensure` only when the condition holds.

Both raise :class:`ReauthRequiredError` (``403 reauth_required``; the body
never names the action), and both carry a :class:`ReauthRule` marker (not an
:class:`~app.core.authz.AccessRule`, so a route still has exactly one access
rule; :func:`route_reauth_rules` finds it). An action code outside
:data:`SENSITIVE_ACTIONS` raises when the route module is built.

**Check order** on a route with ``require(permission)`` and the marker,
wherever the two declarations sit (router or route, either order):
``401`` (``authentication_required`` / ``session_expired``) → ``403
csrf_failed`` → ``403 permission_denied`` → ``403 reauth_required`` →
``422``. :func:`require_recent_auth` runs the route's own permission checks
(found through ``request.scope["route"]``) before its window check, so a user
without the permission is never asked for a password.

**Bound permissions.** A permission that only sensitive actions use is bound
to its action (``SensitiveAction.bound_permissions``). The permission-matrix
test fails for any ``POST``/``PUT``/``PATCH``/``DELETE`` route that requires
a bound permission without the marker of an action that binds it. Later
issues add a permission here when they add one to the catalog that only a
sensitive action uses.

**Permission changes** (ADR-0007 D10) have no action of their own: role
permission sets are fixed in code (ADR-0007 D4), so ``user.roles.change`` and
``user.delegations.change`` cover them.

Imports nothing from ``app.modules`` or ``app.workflows`` (ADR-0002), so the
permission codes below are plain strings; a unit test checks them against the
catalog.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Annotated, Any, Final

from fastapi import Depends, Request
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from app.core import clock
from app.core.authz import (
    PERMISSION_RESPONSES,
    CurrentPrincipal,
    Principal,
    ResolvedSession,
    as_principal,
    ensure_permission,
    ensure_privacy_notice_accepted,
    get_current_session,
    mark_dependency,
    route_access_rules,
    route_markers,
)
from app.core.errors import AppError, problem_response_doc

REAUTH_WINDOW: Final = timedelta(minutes=5)
"""How long a reauthentication lasts (ADR-0007 D10; a code constant, not a
setting)."""

ACTION_CODE_PATTERN: Final = re.compile(r"[a-z_]+(\.[a-z_]+)+")
"""The form of an action code. Action codes are their own namespace, separate
from permission codes even where the text is the same."""


@dataclass(frozen=True)
class SensitiveAction:
    """One ADR-0007 D10 sensitive action."""

    code: str
    description: str
    adr_item: str
    """The ADR-0007 D10 list item it comes from."""
    applied_by: str
    """The issue or task that applies it to its endpoint(s)."""
    bound_permissions: frozenset[str] = frozenset()
    """Permissions that only this action uses (see the module docstring)."""


_ACTIONS: Final = (
    SensitiveAction(
        "user.roles.change",
        "Assign or remove a user's roles.",
        "role and permission changes",
        "#170",
        frozenset(
            {"user.role.assign", "user.admin_role.assign", "user.owner_role.assign"}
        ),
    ),
    SensitiveAction(
        "user.delegations.change",
        "Grant or revoke an Administrator's delegated permissions.",
        "delegation changes",
        "#170",
        frozenset({"user.delegation.manage"}),
    ),
    SensitiveAction(
        "user.invitation.staff_role",
        "Invite someone for a staff role (conditional on the invited role).",
        "invitation for a staff role",
        "#29",
    ),
    SensitiveAction(
        "user.suspend_or_archive",
        "Suspend or archive a user (user.manage also covers non-sensitive edits).",
        "suspending or archiving a user",
        "#31",
    ),
    SensitiveAction(
        "user.password_reset.issue",
        "Issue a password-reset token for another user.",
        "issuing password-reset tokens",
        "#30",
        frozenset({"user.password_reset.issue"}),
    ),
    SensitiveAction(
        "payroll.finalize",
        "Finalize a payroll period.",
        "payroll finalization",
        "M7-07",
        frozenset({"payroll.finalize"}),
    ),
    SensitiveAction(
        "finance.refund_or_reversal",
        "Approve a refund or reverse a payment.",
        "refunds and reversals",
        "M6-15",
        frozenset({"finance.refund.approve"}),
    ),
    SensitiveAction(
        "finance.correction",
        "Correct a financial record.",
        "financial corrections",
        "M6-15",
    ),
    SensitiveAction(
        "data.retention_or_deletion",
        "Configure retention or permanently delete data.",
        "retention and permanent-deletion actions",
        "M8-12",
    ),
    SensitiveAction(
        "settings.payment_methods_or_time_zone",
        "Change the payment methods or the time zone in the business settings.",
        "business-settings changes to payment methods or time zone",
        "M8-04",
    ),
    SensitiveAction(
        "data.personal_export",
        "Export personal data in bulk.",
        "bulk exports of personal data",
        "M8-07",
    ),
)

SENSITIVE_ACTIONS: Final[Mapping[str, SensitiveAction]] = MappingProxyType(
    {action.code: action for action in _ACTIONS}
)
"""Every ADR-0007 D10 sensitive action, by code."""


def actions_binding(permission: str) -> frozenset[str]:
    """The codes of the actions that bind ``permission`` (empty: not bound)."""
    return frozenset(
        a.code for a in SENSITIVE_ACTIONS.values() if permission in a.bound_permissions
    )


# --- The rule, the error and the window ----------------------------------------------


@dataclass(frozen=True)
class ReauthRule:
    """The route marker of :func:`require_recent_auth` (``conditional=False``)
    and :func:`recent_auth_guard` (``conditional=True``)."""

    action: str
    conditional: bool = False


class ReauthRequiredError(AppError):
    """The session has no reauthentication in the last 5 minutes.

    Never names the action. Takes no arguments.
    """

    status = 403
    code = "reauth_required"
    detail = "Confirm your password to continue."

    def __init__(self) -> None:
        super().__init__()


def is_recent(reauthenticated_at: datetime | None) -> bool:
    """Whether a session reauthenticated at ``reauthenticated_at`` is still
    inside :data:`REAUTH_WINDOW` now."""
    return (
        reauthenticated_at is not None
        and clock.now() < reauthenticated_at + REAUTH_WINDOW
    )


def _checked_action(action: str) -> str:
    if action not in SENSITIVE_ACTIONS:
        raise ValueError(f"not a sensitive action: {action!r}")
    return action


def route_reauth_rules(route: APIRoute | RouteContext) -> list[ReauthRule]:
    """The reauthentication markers on ``route`` (at most one is expected)."""
    return route_markers(route, ReauthRule)


def _route_permissions(request: Request) -> list[str]:
    """The permissions the access rules of the request's route require.

    ``request.scope["route"]`` is the matched route without the dependencies
    added by ``include_router``; its contexts (``iter_route_contexts``) carry
    them. If one route object is included more than once, every one of its
    permissions is checked (stricter, never weaker).
    """
    route = request.scope.get("route")
    if route is None:
        return []
    return [
        rule.permission
        for context in iter_route_contexts(request.app.routes)
        if context.original_route is route
        for rule in route_access_rules(context)
        if rule.kind == "permission" and rule.permission is not None
    ]


SessionDep = Annotated[ResolvedSession, Depends(get_current_session)]


def require_recent_auth(action: str) -> Any:
    """The session must be recently reauthenticated for ``action``; yields the
    :class:`~app.core.authz.Principal`.

    ``401`` without a session, ``403 csrf_failed`` on a failed token check,
    ``403 privacy_notice_required`` before the privacy notice is accepted
    (#174; a sensitive action is never notice-exempt), ``403
    permission_denied`` without the route's permission, then ``403
    reauth_required``. The same order whichever dependency FastAPI solves
    first. ``action`` must be in :data:`SENSITIVE_ACTIONS`.
    """
    _checked_action(action)

    async def recent_auth_access(
        request: Request, resolved: SessionDep, principal: CurrentPrincipal
    ) -> Principal:
        checked = as_principal(principal)
        ensure_privacy_notice_accepted(checked)
        for permission in _route_permissions(request):
            ensure_permission(checked, permission)
        if not is_recent(resolved.reauthenticated_at):
            raise ReauthRequiredError()
        return checked

    return Depends(mark_dependency(recent_auth_access, ReauthRule(action)))


@dataclass(frozen=True)
class RecentAuthGuard:
    """Yielded by :func:`recent_auth_guard`: :meth:`ensure` when the request
    turns out to be sensitive."""

    action: str
    reauthenticated_at: datetime | None = field(repr=False)

    def is_recent(self) -> bool:
        return is_recent(self.reauthenticated_at)

    def ensure(self) -> None:
        """Raise :class:`ReauthRequiredError` unless the session is recent."""
        if not self.is_recent():
            raise ReauthRequiredError()


def recent_auth_guard(action: str) -> Any:
    """A conditional reauthentication check for ``action``; yields a
    :class:`RecentAuthGuard`.

    Checks nothing by itself (beyond the session and the CSRF token): the
    handler or service calls ``guard.ensure()`` when the request is the
    sensitive variant. ``action`` must be in :data:`SENSITIVE_ACTIONS`.
    """
    _checked_action(action)

    async def recent_auth_guard_access(
        resolved: SessionDep, principal: CurrentPrincipal
    ) -> RecentAuthGuard:
        as_principal(principal)
        return RecentAuthGuard(action, resolved.reauthenticated_at)

    return Depends(
        mark_dependency(recent_auth_guard_access, ReauthRule(action, conditional=True))
    )


REAUTH_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **PERMISSION_RESPONSES,
    403: problem_response_doc(
        "Permission denied (permission_denied); a failed Origin or X-CSRF-Token"
        " check (csrf_failed); the privacy notice must be accepted first"
        " (privacy_notice_required); or the session needs a recent password"
        " reauthentication (reauth_required)."
    ),
}
"""``responses=`` for routes declared with :func:`require_recent_auth` or
:func:`recent_auth_guard`."""
