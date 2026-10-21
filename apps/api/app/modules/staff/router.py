"""Tutor profile routes (#43; ``app.modules.staff.service``).

Every route is ``require("tutor.view")`` (Owners, Administrators, Academic
Coordinators and Tutors; owner decision E1 (a)); the record policy then
limits a tutor to their own profile, and ``tutor.manage`` (Owners and
Administrators) is checked inside the service for edits:

- ``GET /api/v1/tutors``: the tutors the caller may see.
- ``GET /api/v1/tutors/{user_id}``: one profile; ``404`` when the user is
  unknown, doesn't hold ``tutor`` or isn't visible.
- ``PATCH /api/v1/tutors/{user_id}``: no reauthentication (profile edits are
  not in ADR-0007 D10).

Check order of the ``PATCH``: ``401`` → ``csrf_failed`` →
``permission_denied`` → ``422`` → ``404`` → ``403 permission_denied`` (a
field the caller may not edit) → ``409 version_conflict``.

Tutor qualifications (#44; ``staff/qualifications.py`` has the orders):

- ``GET /api/v1/tutors/{user_id}/qualifications``: whoever may see the tutor.
- ``POST /api/v1/tutors/{user_id}/qualifications``: only the tutor
  themselves (another caller who may see the tutor gets ``403``).
- ``POST …/qualifications/{qualification_id}/cancel``: only the tutor, only
  while ``pending``.

Reviewers decide through the approval inbox (``/api/v1/approval-requests``,
``qualification.review``).

- ``POST …/qualifications/{qualification_id}/revoke`` (#198):
  ``require("qualification.review")`` (Owners and Administrators); order
  ``403`` → ``422`` → ``404`` → ``409 version_conflict`` → ``409
  qualification_not_approved`` (``staff/qualification_revocation.py``).

The search text ``q`` is a query parameter: the access log line has no query
string (#9), so it is never logged.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.identity.schemas import UserState
from app.modules.staff import service
from app.modules.staff.permissions import TUTOR_VIEW
from app.modules.staff.qualification_approval import QUALIFICATION_REVIEW
from app.modules.staff.schemas import (
    QualificationCreate,
    QualificationItem,
    QualificationRevoke,
    QualificationStatusFilter,
    TutorDetail,
    TutorListItem,
    TutorUpdate,
)

router = APIRouter(prefix="/api/v1/tutors", tags=["tutors"])

NOT_FOUND = problem_response_doc(
    "No such user, the user doesn't hold the tutor role, or the caller may not"
    " see this tutor (not_found)."
)


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "An unknown state, q longer than 100 characters, or a limit or"
            " offset out of range (validation_error)."
        ),
    },
)
async def list_tutors(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    q: Annotated[
        str | None,
        Query(
            max_length=100,
            description=(
                "Part of the username or display name (not case-sensitive;"
                " accents are not folded)."
            ),
        ),
    ] = None,
    state: Annotated[UserState | None, Query(description="One user state.")] = None,
) -> OffsetPage[TutorListItem]:
    """The users holding the tutor role, by display name, then username, with
    their contact fields. A tutor sees only themselves."""
    return await service.list_tutors(
        uow.session,
        principal=principal,
        q=(q or "").strip() or None,
        state=state,
        page=page,
    )


@router.get("/{user_id}", responses={**PERMISSION_RESPONSES, 404: NOT_FOUND})
async def get_tutor(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> TutorDetail:
    """One tutor profile, with the fields the caller may edit."""
    return await service.get_tutor(uow.session, principal=principal, user_id=user_id)


@router.patch(
    "/{user_id}",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            f"{PERMISSION_RESPONSES[403]['description']} Also: a sent field is"
            " not in the caller's editable_fields; nothing changes"
            " (permission_denied)."
        ),
        404: NOT_FOUND,
        409: problem_response_doc(
            "The profile changed since it was loaded, or another first edit"
            " created it (version_conflict); reload and retry."
        ),
        422: problem_response_doc(
            "The body is not valid: a missing or negative version, a blank"
            " display name, an email or phone that doesn't match its pattern,"
            " an experience longer than 4000 characters, an unknown field"
            " (validation_error)."
        ),
    },
)
async def update_tutor(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    body: TutorUpdate,
    uow: UnitOfWorkDep,
) -> TutorDetail:
    """Change the sent fields. Administrators edit every field; a tutor edits
    their own email, phone and professional experience. ``version`` 0 is a
    tutor without a stored profile: the first change creates it."""
    return await service.update_tutor(
        uow.session, principal=principal, user_id=user_id, body=body
    )


# --- Tutor qualifications (#44) ------------------------------------------------------

QUALIFICATION_SELF_ONLY = problem_response_doc(
    f"{PERMISSION_RESPONSES[403]['description']} Also: the caller may see the"
    " tutor but isn't the tutor themselves (permission_denied)."
)


@router.get(
    "/{user_id}/qualifications",
    responses={
        **PERMISSION_RESPONSES,
        404: NOT_FOUND,
        422: problem_response_doc("An unknown status (validation_error)."),
    },
)
async def list_qualifications(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    uow: UnitOfWorkDep,
    status: Annotated[
        QualificationStatusFilter,
        Query(description="One status, or all (the default)."),
    ] = "all",
) -> list[QualificationItem]:
    """The tutor's qualifications, by the level's order, the subject name,
    then the request time."""
    return await service.list_qualifications(
        uow.session, principal=principal, user_id=user_id, status=status
    )


@router.post(
    "/{user_id}/qualifications",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: QUALIFICATION_SELF_ONLY,
        404: NOT_FOUND,
        409: problem_response_doc(
            "The subject or its education level is inactive (subject_inactive),"
            " or the tutor already has a pending or approved qualification for"
            " it (qualification_exists)."
        ),
        422: problem_response_doc(
            "The body is not valid, or the subject doesn't exist (validation_error)."
        ),
    },
)
async def request_qualification(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    body: QualificationCreate,
    uow: UnitOfWorkDep,
) -> QualificationItem:
    """The tutor requests to teach a subject: a pending qualification and a
    staff.qualification approval request. Only the tutor themselves."""
    return await service.request_qualification(
        uow.session, principal=principal, user_id=user_id, subject_id=body.subject_id
    )


@router.post(
    "/{user_id}/qualifications/{qualification_id}/cancel",
    responses={
        **PERMISSION_RESPONSES,
        403: QUALIFICATION_SELF_ONLY,
        404: problem_response_doc(
            "No such tutor or qualification of this tutor, or the caller may"
            " not see the tutor (not_found)."
        ),
        409: problem_response_doc(
            "The qualification is no longer pending (approval_transition_invalid)."
        ),
    },
)
async def cancel_qualification(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    qualification_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> QualificationItem:
    """The tutor withdraws a pending request. Only the tutor themselves."""
    return await service.cancel_qualification(
        uow.session,
        principal=principal,
        user_id=user_id,
        qualification_id=qualification_id,
    )


@router.post(
    "/{user_id}/qualifications/{qualification_id}/revoke",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such qualification, or it isn't this tutor's (not_found)."
        ),
        409: problem_response_doc(
            "The qualification changed since it was loaded (version_conflict),"
            " or it isn't approved (qualification_not_approved)."
        ),
        422: problem_response_doc(
            "The body is not valid: a blank reason or one longer than 1000"
            " characters, a missing version or one below 1, an unknown field"
            " (validation_error)."
        ),
    },
)
async def revoke_qualification(
    principal: Annotated[Principal, require(QUALIFICATION_REVIEW)],
    user_id: uuid.UUID,
    qualification_id: uuid.UUID,
    body: QualificationRevoke,
    uow: UnitOfWorkDep,
) -> QualificationItem:
    """A reviewer withdraws an approved qualification with a reason. The row
    stays as revoked, stops counting for eligibility at once, and the tutor
    is notified. The approval request stays approved."""
    return await service.revoke_qualification(
        uow.session,
        principal=principal,
        user_id=user_id,
        qualification_id=qualification_id,
        reason=body.reason,
        version=body.version,
    )
