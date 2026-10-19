"""Password resets (#30): staff issue one-time links; the person redeems one.

Staff route: ``POST /api/v1/password-resets`` with
``require("user.password_reset.issue")`` and
``require_recent_auth("user.password_reset.issue")`` (ADR-0007 D10, D12).

Public routes (``public()``; ``Origin`` check only, #24; ``rate_limit(
"token_redemption.ip")``, 20 per IP per 15 minutes, shared with invitation
redemption, ADR-0008 D15):

- ``POST /api/v1/auth/password-reset/lookup``: who a link is for.
- ``POST /api/v1/auth/password-reset/redeem``: set the new password.

The token travels in request bodies, never in a URL the API sees; every
invalid token is the same ``410 token_invalid``. The self-service change
(``POST /api/v1/auth/password``) is in ``router.py`` with the other
``/auth`` routes.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.authz import CSRF_RESPONSES, Principal, public, require
from app.core.config import Settings, get_settings
from app.core.errors import problem_response_doc
from app.core.ratelimit import RATE_LIMIT_RESPONSES, rate_limit
from app.core.reauth import REAUTH_RESPONSES, require_recent_auth
from app.core.uow import UnitOfWorkDep
from app.modules.identity import recovery
from app.modules.identity.permissions import USER_PASSWORD_RESET_ISSUE
from app.modules.identity.schemas import (
    PasswordResetCreate,
    PasswordResetIssued,
    PasswordResetLookup,
    PasswordResetRedeemRequest,
    PasswordResetTokenRequest,
)

router = APIRouter(tags=["password-resets"])

SettingsDep = Annotated[Settings, Depends(get_settings)]

RESET_PUBLIC_REASON = (
    "The person has no session; the one-time token is the proof. CSRF"
    " protection is the Origin check plus SameSite (#24)"
)

TOKEN_INVALID_RESPONSE: dict[int | str, dict[str, Any]] = {
    410: problem_response_doc(
        "The link is unknown, malformed, used, replaced or expired, or the"
        " account is no longer active (token_invalid)."
    )
}


@router.post(
    "/api/v1/password-resets",
    status_code=201,
    dependencies=[require_recent_auth(USER_PASSWORD_RESET_ISSUE)],
    responses={
        **REAUTH_RESPONSES,
        404: problem_response_doc("No user has this username (not_found)."),
        409: problem_response_doc("The user is not active (user_not_active)."),
        422: problem_response_doc("The body is not valid (validation_error)."),
    },
)
async def issue_password_reset(
    principal: Annotated[Principal, require(USER_PASSWORD_RESET_ISSUE)],
    body: PasswordResetCreate,
    uow: UnitOfWorkDep,
    settings: SettingsDep,
) -> PasswordResetIssued:
    """Issue a one-time password-reset link for another user, valid for 24
    hours, shown in this response only. A new link replaces the previous one.

    Needs a password reauthentication in the last 5 minutes (``403
    reauth_required``). Only an Owner may reset an Owner or an Administrator,
    and nobody resets their own account (``403 permission_denied``; use
    ``POST /api/v1/auth/password``). The user must be active (``409
    user_not_active``). Their sessions end when the link is redeemed.
    """
    return await recovery.issue_password_reset(
        uow.session,
        principal=principal,
        username=body.username,
        app_origin=settings.app_origin,
    )


@router.post(
    "/api/v1/auth/password-reset/lookup",
    dependencies=[
        public(reason=RESET_PUBLIC_REASON),
        rate_limit("token_redemption.ip"),
    ],
    responses={**CSRF_RESPONSES, **TOKEN_INVALID_RESPONSE, **RATE_LIMIT_RESPONSES},
)
async def lookup_password_reset(
    body: PasswordResetTokenRequest, uow: UnitOfWorkDep
) -> PasswordResetLookup:
    """Who a reset link is for (the username to sign in with). Changes
    nothing. Needs the app's ``Origin``."""
    return await recovery.lookup_password_reset(uow.session, body.token)


@router.post(
    "/api/v1/auth/password-reset/redeem",
    status_code=204,
    dependencies=[
        public(reason=RESET_PUBLIC_REASON),
        rate_limit("token_redemption.ip"),
    ],
    responses={
        **CSRF_RESPONSES,
        **TOKEN_INVALID_RESPONSE,
        422: problem_response_doc(
            "The body is not valid (validation_error) or the password breaks"
            " the policy (password_too_short, password_too_long,"
            " password_equals_username); the link still works."
        ),
        **RATE_LIMIT_RESPONSES,
    },
)
async def redeem_password_reset(
    body: PasswordResetRedeemRequest, uow: UnitOfWorkDep
) -> None:
    """Set a new password with a reset link; the link is used up and every
    session of the account ends. Creates no session: the person signs in on
    the login page. Needs the app's ``Origin``."""
    await recovery.redeem_password_reset(
        uow.session, raw_token=body.token, password=body.password
    )
