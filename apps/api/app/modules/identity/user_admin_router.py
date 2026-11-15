"""User administration routes (#31; ``app.modules.identity.user_admin``).

- ``GET /api/v1/users`` and ``GET /api/v1/users/{user_id}``:
  ``require("user.view")``.
- ``PATCH /api/v1/users/{user_id}`` and ``POST …/{user_id}/reactivate``:
  ``require("user.manage")``.
- ``POST …/{user_id}/suspend`` and ``POST …/{user_id}/archive``:
  ``require("user.manage")`` plus
  ``require_recent_auth("user.suspend_or_archive")`` (ADR-0007 D10).
- ``PUT …/{user_id}/roles`` (#170): ``require("user.role.assign")`` plus
  ``require_recent_auth("user.roles.change")``.
- ``PUT …/{user_id}/delegations`` (#170): ``require("user.delegation.manage")``
  (Owners only) plus ``require_recent_auth("user.delegations.change")``.

Check order of the state routes (#26 C6): ``401`` → ``csrf_failed`` →
``permission_denied`` → ``reauth_required`` (suspend, archive) → ``422`` →
``404`` → ``403 permission_denied`` (who may manage whom, own account) →
``409 version_conflict`` → ``409 user_state_transition_invalid`` → ``409
last_active_owner``.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.reauth import REAUTH_RESPONSES, require_recent_auth
from app.core.uow import UnitOfWorkDep
from app.modules.identity import user_admin
from app.modules.identity.errors import STUDENT_LOGIN_GATE_DOC
from app.modules.identity.permissions import (
    USER_DELEGATION_MANAGE,
    USER_MANAGE,
    USER_ROLE_ASSIGN,
    USER_VIEW,
)
from app.modules.identity.schemas import (
    RoleCode,
    UserDelegationsUpdate,
    UserDetail,
    UserListItem,
    UserRolesUpdate,
    UserState,
    UserStateChange,
    UserUpdate,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])

SUSPEND_OR_ARCHIVE = "user.suspend_or_archive"
ROLES_CHANGE = "user.roles.change"
DELEGATIONS_CHANGE = "user.delegations.change"

NOT_FOUND = problem_response_doc("No such user (not_found).")
VERSION_CONFLICT = (
    "The user changed since it was loaded (version_conflict); reload and retry."
)
MANAGE_DENIED = problem_response_doc(
    "The caller lacks user.manage, or may not manage this user: they must be"
    " able to assign every role the user holds; nobody changes their own"
    " state (permission_denied)."
)
STATE_CONFLICTS = problem_response_doc(
    f"{VERSION_CONFLICT} Or the user cannot move to this state from their"
    " current one (user_state_transition_invalid), or this is the only active"
    " Owner (last_active_owner). Reactivation only: " + STUDENT_LOGIN_GATE_DOC
)
BODY_INVALID = problem_response_doc("The body is not valid (validation_error).")


def _state_responses(*, reauth: bool) -> dict[int | str, dict[str, Any]]:
    responses: dict[int | str, dict[str, Any]] = {
        **(REAUTH_RESPONSES if reauth else PERMISSION_RESPONSES),
        404: NOT_FOUND,
        409: STATE_CONFLICTS,
        422: BODY_INVALID,
    }
    responses[403] = problem_response_doc(
        f"{responses[403]['description']} Also: the caller may not manage this"
        " user, or it is their own account (permission_denied)."
    )
    return responses


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "An unknown role or state, or q longer than 100 characters"
            " (validation_error)."
        ),
    },
)
async def list_users(
    principal: Annotated[Principal, require(USER_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    role: Annotated[RoleCode | None, Query()] = None,
    state: Annotated[UserState | None, Query()] = None,
) -> OffsetPage[UserListItem]:
    """Search users by part of the username or display name (not
    case-sensitive; accents are not folded), filtered by one role and one
    state. Sorted by display name, then username. No contact fields."""
    return await user_admin.list_users(
        uow.session,
        q=(q or "").strip() or None,
        role=role,
        state=state,
        page=page,
    )


@router.get("/{user_id}", responses={**PERMISSION_RESPONSES, 404: NOT_FOUND})
async def get_user(
    principal: Annotated[Principal, require(USER_VIEW)],
    user_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> UserDetail:
    """One user's account with the caller's ``allowed_actions``."""
    return await user_admin.get_user_detail(
        uow.session, principal=principal, user_id=user_id
    )


@router.patch(
    "/{user_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: MANAGE_DENIED,
        404: NOT_FOUND,
        409: problem_response_doc(VERSION_CONFLICT),
        422: problem_response_doc(
            "The body is not valid: a missing version, a blank display name,"
            " or an email or phone that doesn't match its pattern"
            " (validation_error)."
        ),
    },
)
async def update_user(
    principal: Annotated[Principal, require(USER_MANAGE)],
    user_id: uuid.UUID,
    body: UserUpdate,
    uow: UnitOfWorkDep,
) -> UserDetail:
    """Change the display name, email or phone (fields not sent stay
    unchanged; ``""`` or ``null`` clears a contact field). Contact fields are
    never used to sign in. The username never changes."""
    return await user_admin.update_user_profile(
        uow.session, principal=principal, user_id=user_id, body=body
    )


@router.post(
    "/{user_id}/suspend",
    dependencies=[require_recent_auth(SUSPEND_OR_ARCHIVE)],
    responses=_state_responses(reauth=True),
)
async def suspend_user(
    principal: Annotated[Principal, require(USER_MANAGE)],
    user_id: uuid.UUID,
    body: UserStateChange,
    uow: UnitOfWorkDep,
) -> UserDetail:
    """Suspend an active user: every session ends and they cannot sign in
    until reactivated. Needs a password reauthentication in the last 5
    minutes."""
    return await user_admin.change_state(
        uow.session,
        principal=principal,
        user_id=user_id,
        action="suspend",
        version=body.version,
    )


@router.post(
    "/{user_id}/archive",
    dependencies=[require_recent_auth(SUSPEND_OR_ARCHIVE)],
    responses=_state_responses(reauth=True),
)
async def archive_user(
    principal: Annotated[Principal, require(USER_MANAGE)],
    user_id: uuid.UUID,
    body: UserStateChange,
    uow: UnitOfWorkDep,
) -> UserDetail:
    """Archive an active or suspended user, or cancel an open invitation (a
    pending user without a password). Every session ends; the history is
    kept. Needs a password reauthentication in the last 5 minutes."""
    return await user_admin.change_state(
        uow.session,
        principal=principal,
        user_id=user_id,
        action="archive",
        version=body.version,
    )


@router.post("/{user_id}/reactivate", responses=_state_responses(reauth=False))
async def reactivate_user(
    principal: Annotated[Principal, require(USER_MANAGE)],
    user_id: uuid.UUID,
    body: UserStateChange,
    uow: UnitOfWorkDep,
) -> UserDetail:
    """Reactivate a suspended or archived user. Old sessions stay ended and
    the password is unchanged (a user without one needs a reset link)."""
    return await user_admin.change_state(
        uow.session,
        principal=principal,
        user_id=user_id,
        action="reactivate",
        version=body.version,
    )


@router.put(
    "/{user_id}/roles",
    dependencies=[require_recent_auth(ROLES_CHANGE)],
    responses={
        **REAUTH_RESPONSES,
        403: problem_response_doc(
            f"{REAUTH_RESPONSES[403]['description']} Also: the caller may not"
            " manage this user, or may not assign or remove one of the roles"
            " added or removed (permission_denied)."
        ),
        404: NOT_FOUND,
        409: problem_response_doc(
            f"{VERSION_CONFLICT} Or the change removes owner from the only"
            " active Owner (last_active_owner); nothing changes. Adding student"
            " to an active account: " + STUDENT_LOGIN_GATE_DOC
        ),
        422: problem_response_doc(
            "The body is not valid: no role, more than 7, a repeated or"
            " unknown role, or a missing version (validation_error)."
        ),
    },
)
async def update_user_roles(
    principal: Annotated[Principal, require(USER_ROLE_ASSIGN)],
    user_id: uuid.UUID,
    body: UserRolesUpdate,
    uow: UnitOfWorkDep,
) -> UserDetail:
    """Replace the user's roles with ``roles`` (at least one). Owners assign
    every role; Administrators every role except Administrator (only with the
    ``user.admin_role.assign`` delegation) and Owner. Removing a role needs
    the same authority as adding it. Any change ends every session of the
    user, also the caller's own when they change their own roles (the
    response is still ``200``). Removing Administrator also removes the
    user's delegated permissions. The same set changes nothing. Needs a
    password reauthentication in the last 5 minutes."""
    return await user_admin.update_roles(
        uow.session,
        principal=principal,
        user_id=user_id,
        roles=body.roles,
        version=body.version,
    )


@router.put(
    "/{user_id}/delegations",
    dependencies=[require_recent_auth(DELEGATIONS_CHANGE)],
    responses={
        **REAUTH_RESPONSES,
        403: problem_response_doc(
            f"{REAUTH_RESPONSES[403]['description']} Also: only an active Owner"
            " delegates (delegation_forbidden)."
        ),
        404: NOT_FOUND,
        409: problem_response_doc(VERSION_CONFLICT),
        422: problem_response_doc(
            "The body is not valid: more than 4 codes, a repeated code, a"
            " permission that can't be delegated, or a missing version"
            " (validation_error). Or a permission is added for a user who"
            " isn't an Administrator, or is archived"
            " (delegation_target_invalid)."
        ),
    },
)
async def update_user_delegations(
    principal: Annotated[Principal, require(USER_DELEGATION_MANAGE)],
    user_id: uuid.UUID,
    body: UserDelegationsUpdate,
    uow: UnitOfWorkDep,
) -> UserDetail:
    """Replace an Administrator's delegated permissions with ``permissions``
    (Owners only). Granting needs an Administrator who isn't archived;
    removing works for any user. The Administrator stays signed in and their
    next request uses the new permissions. The same set changes nothing.
    Needs a password reauthentication in the last 5 minutes."""
    return await user_admin.update_delegations(
        uow.session,
        principal=principal,
        user_id=user_id,
        permissions=body.permissions,
        version=body.version,
    )
