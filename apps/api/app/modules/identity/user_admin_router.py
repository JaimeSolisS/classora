"""User administration routes (#31; ``app.modules.identity.user_admin``).

- ``GET /api/v1/users`` and ``GET /api/v1/users/{user_id}``:
  ``require("user.view")``.
- ``PATCH /api/v1/users/{user_id}`` and ``POST …/{user_id}/reactivate``:
  ``require("user.manage")``.
- ``POST …/{user_id}/suspend`` and ``POST …/{user_id}/archive``:
  ``require("user.manage")`` plus
  ``require_recent_auth("user.suspend_or_archive")`` (ADR-0007 D10).

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
from app.modules.identity.permissions import USER_MANAGE, USER_VIEW
from app.modules.identity.schemas import (
    RoleCode,
    UserDetail,
    UserListItem,
    UserState,
    UserStateChange,
    UserUpdate,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])

SUSPEND_OR_ARCHIVE = "user.suspend_or_archive"

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
    " Owner (last_active_owner)."
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
