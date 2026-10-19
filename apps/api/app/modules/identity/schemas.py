"""Identity request and response bodies (the public schemas of the module)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

# Named string enums in OpenAPI, so the generated web client gets
# ``RoleCode`` and ``PermissionCode`` unions (#28 F3). They list exactly the
# catalog in ``app.modules.identity.permissions`` (sorted); a unit test keeps
# them equal to ``ROLE_CODES`` and ``PERMISSION_CODES``.
type RoleCode = Literal[
    "academic_coordinator",
    "administrator",
    "finance_staff",
    "guardian",
    "owner",
    "student",
    "tutor",
]
type PermissionCode = Literal[
    "audit.view",
    "catalog.manage",
    "consent.record",
    "family.manage",
    "finance.refund.approve",
    "guardian.view",
    "payroll.finalize",
    "registration.review",
    "schedule.override",
    "student.manage",
    "student.view",
    "user.admin_role.assign",
    "user.delegation.manage",
    "user.invite",
    "user.manage",
    "user.owner_role.assign",
    "user.password_reset.issue",
    "user.role.assign",
    "user.view",
]


class LoginRequest(BaseModel):
    """Username and password. The bounds do not depend on any account."""

    username: str = Field(min_length=1, max_length=256)
    # repr=False: the password never appears in a repr or a log line.
    password: str = Field(min_length=1, max_length=1024, repr=False)


class SessionUser(BaseModel):
    """The signed-in user (``POST /auth/login`` and ``GET /auth/me``).

    Holds no password hash, session token or session id. ``csrf_token`` is
    the session's CSRF token: the SPA keeps it in memory and sends it as
    ``X-CSRF-Token`` on ``POST``, ``PUT``, ``PATCH`` and ``DELETE``.
    """

    id: uuid.UUID
    username: str
    display_name: str
    roles: list[RoleCode] = Field(description="Role codes, sorted.")
    permissions: list[PermissionCode] = Field(
        description="Effective permission codes, sorted."
    )
    csrf_token: str = Field(
        description=(
            "The session's CSRF token: send it as X-CSRF-Token on POST, PUT,"
            " PATCH and DELETE. Keep it in memory only."
        ),
        min_length=43,
        max_length=43,
    )


class ReauthenticateRequest(BaseModel):
    """The current user's password (#26). The same bounds as login."""

    password: str = Field(min_length=1, max_length=1024, repr=False)


class ReauthenticateResponse(BaseModel):
    """The end of the session's reauthentication window (#26).

    Holds no token, hash or session id.
    """

    reauthenticated_until: datetime = Field(
        description=(
            "When the 5-minute reauthentication window of this session ends"
            " (ISO 8601, UTC). Activity does not extend it."
        )
    )


# --- Invitations (#29) ---------------------------------------------------------------

DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class InvitationCreate(BaseModel):
    """Invite one person for one role (``POST /api/v1/invitations``)."""

    username: str = Field(
        min_length=1,
        max_length=256,
        description=(
            "Normalized like a login username: 3 to 64 characters from a-z,"
            " 0-9, '.', '_' and '-' (letters are not case-sensitive)."
        ),
    )
    display_name: DisplayName
    role: RoleCode


class InvitationIssued(BaseModel):
    """A new invitation link (``201``) or a replacement (``200`` on reissue).

    ``invitation_url`` holds the one-time token in its fragment. It is shown
    in this response only: never stored, logged or returned again.
    """

    user_id: uuid.UUID
    username: str
    display_name: str
    roles: list[RoleCode] = Field(description="Role codes, sorted.")
    expires_at: datetime
    invitation_url: str = Field(
        description=(
            "APP_ORIGIN + '/invite#token=' + the one-time token. Show it once;"
            " never store or log it."
        )
    )


class InvitationListItem(BaseModel):
    """One open invitation: a pending user without a password who has an
    invitation link. Holds no token or hash."""

    user_id: uuid.UUID
    username: str
    display_name: str
    roles: list[RoleCode] = Field(description="Role codes, sorted.")
    status: Literal["active", "expired"] = Field(
        description="active: the latest link can still be used."
    )
    expires_at: datetime = Field(description="When the latest link expires.")
    issued_at: datetime = Field(description="When the latest link was issued.")


class InvitationTokenRequest(BaseModel):
    """The token from an invitation link (``POST …/invitation/lookup``)."""

    token: str = Field(min_length=1, max_length=256, repr=False)


class InvitationRedeemRequest(BaseModel):
    """The token and the new password (``POST …/invitation/redeem``)."""

    token: str = Field(min_length=1, max_length=256, repr=False)
    password: str = Field(min_length=1, max_length=1024, repr=False)


class InvitationLookup(BaseModel):
    """Who an invitation link is for, so the page can show the username."""

    username: str
    display_name: str
    roles: list[RoleCode] = Field(description="Role codes, sorted.")
    expires_at: datetime


# --- Password reset and change (#30) -------------------------------------------------


class PasswordResetCreate(BaseModel):
    """Issue a reset link for one user (``POST /api/v1/password-resets``)."""

    username: str = Field(
        min_length=1,
        max_length=256,
        description=(
            "The user's username (normalized like a login username; letters"
            " are not case-sensitive)."
        ),
    )


class PasswordResetIssued(BaseModel):
    """A new password-reset link (``201``).

    ``reset_url`` holds the one-time token in its fragment. It is shown in this
    response only: never stored, logged or returned again.
    """

    user_id: uuid.UUID
    username: str
    display_name: str
    expires_at: datetime
    reset_url: str = Field(
        description=(
            "APP_ORIGIN + '/reset#token=' + the one-time token. Show it once;"
            " never store or log it."
        )
    )


class PasswordResetTokenRequest(BaseModel):
    """The token from a reset link (``POST …/password-reset/lookup``)."""

    token: str = Field(min_length=1, max_length=256, repr=False)


class PasswordResetRedeemRequest(BaseModel):
    """The token and the new password (``POST …/password-reset/redeem``)."""

    token: str = Field(min_length=1, max_length=256, repr=False)
    password: str = Field(min_length=1, max_length=1024, repr=False)


class PasswordResetLookup(BaseModel):
    """Who a reset link is for, so the page can show the username."""

    username: str
    display_name: str
    expires_at: datetime


class PasswordChange(BaseModel):
    """The signed-in user's current and new password (``POST /auth/password``)."""

    current_password: str = Field(min_length=1, max_length=1024, repr=False)
    new_password: str = Field(min_length=1, max_length=1024, repr=False)


# --- Self-registration requests (#169) -----------------------------------------------

# A named string enum in OpenAPI (``RegistrationRole`` in the web client): the
# only roles a person may request (ADR-0007 D13). Anything else is 422.
type RegistrationRole = Literal["guardian", "student"]
type RegistrationStatus = Literal["pending", "approved", "rejected"]


class RegistrationCreate(BaseModel):
    """A self-registration request (``POST /api/v1/auth/registrations``)."""

    username: str = Field(
        min_length=1,
        max_length=256,
        description=(
            "Normalized like a login username: 3 to 64 characters from a-z,"
            " 0-9, '.', '_' and '-' (letters are not case-sensitive)."
        ),
    )
    display_name: DisplayName
    password: str = Field(min_length=1, max_length=1024, repr=False)
    role: RegistrationRole


class RegistrationSubmitted(BaseModel):
    """The answer to a registration request (``202``). Holds no id."""

    status: Literal["pending"]


class RegistrationRequestItem(BaseModel):
    """One registration request in the review list. Holds no password data."""

    id: uuid.UUID
    username: str
    display_name: str
    requested_role: RegistrationRole
    status: RegistrationStatus
    submitted_at: datetime
    decided_at: datetime | None
