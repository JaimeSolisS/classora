"""Identity request and response bodies (the public schemas of the module)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.modules.identity.models import EMAIL_MAX_LENGTH, PHONE_MAX_LENGTH

# Re-exported: the preferences bodies (#36) live next to their constants.
from app.modules.identity.preferences import UserPreferences as UserPreferences
from app.modules.identity.preferences import (
    UserPreferencesUpdate as UserPreferencesUpdate,
)

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
    "course.manage",
    "course.view",
    "family.manage",
    "finance.refund.approve",
    "guardian.view",
    "payroll.finalize",
    "qualification.review",
    "registration.review",
    "schedule.override",
    "settings.manage",
    "student.manage",
    "student.view",
    "tutor.manage",
    "tutor.view",
    "user.admin_role.assign",
    "user.delegation.manage",
    "user.invite",
    "user.manage",
    "user.owner_role.assign",
    "user.password_reset.issue",
    "user.role.assign",
    "user.view",
]
# The delegable permissions (ADR-0007 D4), the four ``DELEGABLE_PERMISSIONS``.
type DelegablePermissionCode = Literal[
    "finance.refund.approve",
    "payroll.finalize",
    "schedule.override",
    "user.admin_role.assign",
]
# The user states (spec §2), in ``USER_STATES`` order.
type UserState = Literal["pending", "active", "suspended", "archived"]


class LoginRequest(BaseModel):
    """Username and password. The bounds do not depend on any account."""

    username: str = Field(min_length=1, max_length=256)
    # repr=False: the password never appears in a repr or a log line.
    password: str = Field(min_length=1, max_length=1024, repr=False)


class SessionUser(BaseModel):
    """The signed-in user (``POST /auth/login``, ``GET /auth/me`` and
    ``POST /auth/password``).

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
    privacy_notice_required: bool = Field(
        description=(
            "True until the user accepts the current privacy-notice version"
            " (#174). Meanwhile every route except /auth/me, /auth/logout,"
            " /auth/me/preferences and the privacy-notice routes answers 403"
            " privacy_notice_required."
        )
    )
    preferences: UserPreferences = Field(
        description=(
            "The user's stored UI language and theme (#36); null when never"
            " stored. The web app applies them at sign-in."
        )
    )
    center_time_zone: str = Field(
        description=(
            "The center's IANA time zone (business settings, #37). The web app"
            " shows instants and computes today's date in it."
        )
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


# --- User administration (#31) -------------------------------------------------------

# The contact-field patterns of ``models.EMAIL_PATTERN`` / ``PHONE_PATTERN``
# (PostgreSQL ``[:space:]`` is ``\s`` here). A unit test checks that both
# accept and refuse the same samples.
EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
PHONE_REGEX = r"^\+?[0-9][0-9 ().-]{5,30}[0-9]$"


def _blank_to_none(value: Any) -> Any:
    """Trim a contact field; an empty string clears it (``None``)."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


ContactEmail = Annotated[
    Annotated[str, StringConstraints(max_length=EMAIL_MAX_LENGTH, pattern=EMAIL_REGEX)]
    | None,
    BeforeValidator(_blank_to_none),
]
ContactPhone = Annotated[
    Annotated[str, StringConstraints(max_length=PHONE_MAX_LENGTH, pattern=PHONE_REGEX)]
    | None,
    BeforeValidator(_blank_to_none),
]


class UserListItem(BaseModel):
    """One user in the list. No contact fields, password data or delegations
    (spec §16: minimal exposure)."""

    id: uuid.UUID
    username: str
    display_name: str
    state: UserState
    roles: list[RoleCode] = Field(description="Role codes, in the catalog order.")
    created_at: datetime


class UserAllowedActions(BaseModel):
    """What the caller may do to this user, computed by the server with the
    routes' rules (the routes stay the authority). The last-active-Owner guard
    is not considered (the server answers ``409 last_active_owner``)."""

    edit: bool
    suspend: bool
    archive: bool
    reactivate: bool
    reset_password: bool
    assignable_roles: list[RoleCode] = Field(
        description=(
            "Roles the caller may add to and remove from this user, in the"
            " catalog order (empty: the roles are read-only)."
        )
    )
    manage_delegations: bool = Field(
        description=(
            "The caller may grant and revoke this user's delegated permissions"
            " (an Owner, and the user holds administrator)."
        )
    )


class UserDetail(UserListItem):
    """One user's account. Holds no password hash or token."""

    email: str | None
    phone: str | None
    version: int = Field(description="Send it back with every change.")
    updated_at: datetime
    delegations: list[DelegablePermissionCode] = Field(
        description=(
            "Delegated permission codes, sorted (changed with PUT"
            " /api/v1/users/{user_id}/delegations)."
        )
    )
    password_set: bool = Field(
        description=(
            "Whether the account has a password: a pending account without one"
            " is an open invitation, with one a registration request."
        )
    )
    allowed_actions: UserAllowedActions


class UserUpdate(BaseModel):
    """A profile change (``PATCH /api/v1/users/{user_id}``). Fields that are not
    sent stay unchanged; ``""`` or ``null`` clears ``email`` or ``phone``. The
    username never changes (ADR-0007 D7)."""

    version: int = Field(ge=1)
    display_name: DisplayName | None = None
    email: ContactEmail = None
    phone: ContactPhone = None

    @field_validator("display_name")
    @classmethod
    def _display_name_not_null(cls, value: str | None) -> str | None:
        # Runs only when the field is sent: null is not a display name.
        if value is None:
            raise ValueError("The display name cannot be empty.")
        return value


class UserStateChange(BaseModel):
    """Suspend, archive or reactivate: the version the caller loaded."""

    version: int = Field(ge=1)


def _distinct[T](values: list[T]) -> list[T]:
    if len(set(values)) != len(values):
        raise ValueError("Each code may appear only once.")
    return values


class UserRolesUpdate(BaseModel):
    """The user's complete set of roles (``PUT /api/v1/users/{user_id}/roles``)
    and the version the caller loaded. At least one role: a roleless account
    is only a registration request (#169)."""

    roles: list[RoleCode] = Field(min_length=1, max_length=7)
    version: int = Field(ge=1)

    @field_validator("roles")
    @classmethod
    def _roles_distinct(cls, value: list[str]) -> list[str]:
        return _distinct(value)


class UserDelegationsUpdate(BaseModel):
    """The Administrator's complete set of delegated permissions (``PUT
    /api/v1/users/{user_id}/delegations``) and the version the caller
    loaded."""

    permissions: list[DelegablePermissionCode] = Field(max_length=4)
    version: int = Field(ge=1)

    @field_validator("permissions")
    @classmethod
    def _permissions_distinct(cls, value: list[str]) -> list[str]:
        return _distinct(value)


class AccountReference(BaseModel):
    """What another module may know about a user account (#34 B1; ADR-0002:
    other modules read accounts only through ``identity.service``). Never a
    password hash, a token or the account's contact fields."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    username: str
    display_name: str
    state: UserState
    roles: frozenset[RoleCode] = Field(description="The account's role codes.")


class RoleMember(BaseModel):
    """A user holding a given role, **with** the account's contact fields
    (#43 C1: ``identity.service.list_role_members`` / ``get_role_member``).
    Only for modules whose record policy already allows the contact data (the
    tutor profiles of ``staff``). Never a password hash or a token."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    username: str
    display_name: str
    state: UserState
    email: str | None
    phone: str | None
    roles: frozenset[RoleCode] = Field(description="The account's role codes.")


# --- Privacy notice (#174) -----------------------------------------------------------


class PrivacyNotice(BaseModel):
    """One published privacy-notice version (``identity.service``; #35 stamps
    consent records with its ``id``)."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    version: int
    published_at: datetime
    body_es: str
    body_en: str


class PrivacyNoticeRead(BaseModel):
    """The current privacy notice (``GET /api/v1/privacy-notice``)."""

    version: int = Field(description="The current version number.")
    published_at: datetime
    body_es: str = Field(
        description="The Spanish text: plain text, paragraphs separated by blank lines."
    )
    body_en: str = Field(
        description="The English text: plain text, paragraphs separated by blank lines."
    )
    accepted_at: datetime | None = Field(
        description="When the caller accepted this version, or null."
    )


class PrivacyNoticeAcceptanceRequest(BaseModel):
    """The version the user read and accepts
    (``POST /api/v1/privacy-notice/acceptance``)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(
        description="The version shown to the user; it must be the current one."
    )


class PrivacyNoticeAccepted(BaseModel):
    """An acceptance of the current version (the first one, when repeated)."""

    version: int
    accepted_at: datetime
