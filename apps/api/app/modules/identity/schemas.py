"""Identity request and response bodies (the public schemas of the module)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
