"""Self-registration requests (#169): a public request, reviewed by staff.

Public route (``public()``; ``Origin`` check only, #24; ``rate_limit(
"registration.ip")``, 10 per IP per hour, ADR-0008 D15):

- ``POST /api/v1/auth/registrations``: a pending user without a role and a
  pending request (``202``).

Staff routes (``require("registration.review")``; Owner and Administrator):

- ``GET /api/v1/registration-requests``: the requests with one status.
- ``POST /api/v1/registration-requests/{request_id}/approve``.
- ``POST /api/v1/registration-requests/{request_id}/reject``.

No reauthentication: approval grants only Parent/Guardian or Student, like a
non-staff invitation (ADR-0007 D10).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.core.authz import (
    CSRF_RESPONSES,
    PERMISSION_RESPONSES,
    Principal,
    public,
    require,
)
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.ratelimit import RATE_LIMIT_RESPONSES, rate_limit
from app.core.uow import UnitOfWorkDep
from app.modules.identity import registrations
from app.modules.identity.errors import STUDENT_LOGIN_GATE_DOC
from app.modules.identity.permissions import REGISTRATION_REVIEW
from app.modules.identity.schemas import (
    RegistrationCreate,
    RegistrationRequestItem,
    RegistrationStatus,
    RegistrationSubmitted,
)

router = APIRouter(tags=["registrations"])

REGISTRATION_PUBLIC_REASON = (
    "The person has no account yet; the request stays pending until staff"
    " approve it. CSRF protection is the Origin check plus SameSite (#24)"
)

DECISION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **PERMISSION_RESPONSES,
    404: problem_response_doc("No such registration request (not_found)."),
    409: problem_response_doc(
        "The request was already approved or rejected, or its account is no"
        " longer pending (registration_already_decided). Approval only: "
        + STUDENT_LOGIN_GATE_DOC
    ),
}


@router.post(
    "/api/v1/auth/registrations",
    status_code=202,
    dependencies=[
        public(reason=REGISTRATION_PUBLIC_REASON),
        rate_limit("registration.ip"),
    ],
    responses={
        **CSRF_RESPONSES,
        409: problem_response_doc("The username is already taken (username_taken)."),
        422: problem_response_doc(
            "The body is not valid (validation_error; also any role other than"
            " guardian or student), the username is not valid"
            " (username_invalid), or the password breaks the policy"
            " (password_too_short, password_too_long, password_equals_username)."
        ),
        **RATE_LIMIT_RESPONSES,
    },
)
async def submit_registration(
    body: RegistrationCreate, uow: UnitOfWorkDep
) -> RegistrationSubmitted:
    """Request an account as a parent or guardian, or as a student.

    Creates a pending account that cannot sign in until an Owner or
    Administrator approves it. Never links or enrolls anyone. Needs the app's
    ``Origin``. Every request counts against the hourly limit.
    """
    await registrations.submit_registration(
        uow.session,
        username=body.username,
        display_name=body.display_name,
        password=body.password,
        role=body.role,
    )
    return RegistrationSubmitted(status="pending")


@router.get("/api/v1/registration-requests", responses=PERMISSION_RESPONSES)
async def list_registration_requests(
    principal: Annotated[Principal, require(REGISTRATION_REVIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    status: Annotated[RegistrationStatus, Query()] = "pending",
) -> OffsetPage[RegistrationRequestItem]:
    """The registration requests with ``status`` (default ``pending``),
    oldest first. Holds no password data."""
    return await registrations.list_registration_requests(
        uow.session, status=status, page=page
    )


@router.post(
    "/api/v1/registration-requests/{request_id}/approve",
    responses=DECISION_RESPONSES,
)
async def approve_registration(
    principal: Annotated[Principal, require(REGISTRATION_REVIEW)],
    request_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> RegistrationRequestItem:
    """Approve a pending request: the account becomes active with the
    requested role. Confirm the person's identity first, out of band."""
    return await registrations.approve_registration(
        uow.session, principal=principal, request_id=request_id
    )


@router.post(
    "/api/v1/registration-requests/{request_id}/reject",
    responses=DECISION_RESPONSES,
)
async def reject_registration(
    principal: Annotated[Principal, require(REGISTRATION_REVIEW)],
    request_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> RegistrationRequestItem:
    """Reject a pending request: the account is archived and can never sign
    in; its username can't be used again."""
    return await registrations.reject_registration(
        uow.session, principal=principal, request_id=request_id
    )
