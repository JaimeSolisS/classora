"""Invitations (#29): staff issue one-time links; the invited person redeems one.

Staff routes (``require("user.invite")``):

- ``POST /api/v1/invitations``: a pending user with one role and a link.
- ``GET /api/v1/invitations``: the open invitations.
- ``POST /api/v1/invitations/{user_id}/reissue``: a new link replaces the old.

Both issuing routes carry ``recent_auth_guard("user.invitation.staff_role")``:
a staff role needs a recent reauthentication, the other roles do not (#26).

Public routes (``public()``; ``Origin`` check only, #24; ``rate_limit(
"token_redemption.ip")``, 20 per IP per 15 minutes, ADR-0008 D15):

- ``POST /api/v1/auth/invitation/lookup``: who a link is for.
- ``POST /api/v1/auth/invitation/redeem``: set the password and activate.

The token travels in request bodies, never in a URL the API sees; every
invalid token is the same ``410 token_invalid``.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.authz import (
    CSRF_RESPONSES,
    PERMISSION_RESPONSES,
    Principal,
    public,
    require,
)
from app.core.config import Settings, get_settings
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.ratelimit import RATE_LIMIT_RESPONSES, rate_limit
from app.core.reauth import REAUTH_RESPONSES, RecentAuthGuard, recent_auth_guard
from app.core.uow import UnitOfWorkDep
from app.modules.identity import invitations
from app.modules.identity.errors import STUDENT_LOGIN_GATE_DOC
from app.modules.identity.permissions import USER_INVITE
from app.modules.identity.schemas import (
    InvitationCreate,
    InvitationIssued,
    InvitationListItem,
    InvitationLookup,
    InvitationRedeemRequest,
    InvitationTokenRequest,
)

router = APIRouter(tags=["invitations"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
StaffRoleGuard = Annotated[
    RecentAuthGuard, recent_auth_guard("user.invitation.staff_role")
]

REDEMPTION_PUBLIC_REASON = (
    "The invited person has no session yet; the one-time token is the proof."
    " CSRF protection is the Origin check plus SameSite (#24)"
)

TOKEN_INVALID_RESPONSE: dict[int | str, dict[str, Any]] = {
    410: problem_response_doc(
        "The link is unknown, malformed, used, replaced or expired, or the"
        " invitation can no longer be redeemed (token_invalid)."
    )
}


@router.post(
    "/api/v1/invitations",
    status_code=201,
    responses={
        **REAUTH_RESPONSES,
        409: problem_response_doc("The username is already taken (username_taken)."),
        422: problem_response_doc(
            "The body is not valid (validation_error) or the username is not"
            " valid (username_invalid)."
        ),
    },
)
async def create_invitation(
    principal: Annotated[Principal, require(USER_INVITE)],
    guard: StaffRoleGuard,
    body: InvitationCreate,
    uow: UnitOfWorkDep,
    settings: SettingsDep,
) -> InvitationIssued:
    """Invite a person for one role: a pending user without a password and a
    one-time link, valid for 24 hours, shown in this response only.

    The caller must be allowed to assign the role (``403 permission_denied``).
    Owner, Administrator, Academic coordinator and Finance staff need a
    password reauthentication in the last 5 minutes (``403
    reauth_required``).
    """
    return await invitations.create_invitation(
        uow.session,
        principal=principal,
        guard=guard,
        username=body.username,
        display_name=body.display_name,
        role=body.role,
        app_origin=settings.app_origin,
    )


@router.get("/api/v1/invitations", responses=PERMISSION_RESPONSES)
async def list_invitations(
    principal: Annotated[Principal, require(USER_INVITE)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
) -> OffsetPage[InvitationListItem]:
    """The open invitations (pending users without a password who have a
    link), latest link first. Holds no link or token."""
    return await invitations.list_invitations(uow.session, page)


@router.post(
    "/api/v1/invitations/{user_id}/reissue",
    responses={
        **REAUTH_RESPONSES,
        404: problem_response_doc("Not an open invitation (not_found)."),
    },
)
async def reissue_invitation(
    principal: Annotated[Principal, require(USER_INVITE)],
    guard: StaffRoleGuard,
    user_id: uuid.UUID,
    uow: UnitOfWorkDep,
    settings: SettingsDep,
) -> InvitationIssued:
    """Issue a new link for an open invitation; the earlier link stops
    working. The same role and reauthentication rules as creating one, for
    every role the user holds."""
    return await invitations.reissue_invitation(
        uow.session,
        principal=principal,
        guard=guard,
        user_id=user_id,
        app_origin=settings.app_origin,
    )


@router.post(
    "/api/v1/auth/invitation/lookup",
    dependencies=[
        public(reason=REDEMPTION_PUBLIC_REASON),
        rate_limit("token_redemption.ip"),
    ],
    responses={**CSRF_RESPONSES, **TOKEN_INVALID_RESPONSE, **RATE_LIMIT_RESPONSES},
)
async def lookup_invitation(
    body: InvitationTokenRequest, uow: UnitOfWorkDep
) -> InvitationLookup:
    """Who an invitation link is for (the username to sign in with). Changes
    nothing. Needs the app's ``Origin``."""
    return await invitations.lookup_invitation(uow.session, body.token)


@router.post(
    "/api/v1/auth/invitation/redeem",
    status_code=204,
    dependencies=[
        public(reason=REDEMPTION_PUBLIC_REASON),
        rate_limit("token_redemption.ip"),
    ],
    responses={
        **CSRF_RESPONSES,
        **TOKEN_INVALID_RESPONSE,
        409: problem_response_doc(STUDENT_LOGIN_GATE_DOC + " The link still works."),
        422: problem_response_doc(
            "The body is not valid (validation_error) or the password breaks"
            " the policy (password_too_short, password_too_long,"
            " password_equals_username); the link still works."
        ),
        **RATE_LIMIT_RESPONSES,
    },
)
async def redeem_invitation(body: InvitationRedeemRequest, uow: UnitOfWorkDep) -> None:
    """Set the invited person's password and activate the account; the link
    is used up. Creates no session: the person signs in on the login page.
    Needs the app's ``Origin``."""
    await invitations.redeem_invitation(
        uow.session, raw_token=body.token, password=body.password
    )
