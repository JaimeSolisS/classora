"""Student login and consent routes (#35; ``app.modules.families.service``).

- ``PUT /api/v1/students/{student_id}/account``: ``require("family.manage")``
  (Owners, Administrators) links a login account to the profile, or unlinks
  it with ``null``.
- ``GET``, ``POST /api/v1/students/{student_id}/consents`` and ``POST
  /api/v1/students/{student_id}/consents/{consent_id}/revoke``:
  ``require("consent.record")`` (Owners, Administrators, students and
  guardians), then the consent record policy (#35 D1): a student or record
  the caller can't reach is ``404``.

No reauthentication: ADR-0007 D10 lists staff suspension, not consent, so a
guardian's withdrawal can suspend a student's login without a password
prompt.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.families import service
from app.modules.families.assignments import (
    TutorAssignmentSource,
    get_tutor_assignment_source,
)
from app.modules.families.permissions import CONSENT_RECORD, FAMILY_MANAGE
from app.modules.families.schemas import (
    ConsentCreate,
    ConsentOverview,
    ConsentRecordItem,
    ConsentRevocation,
    StudentAccountUpdate,
    StudentDetail,
)

router = APIRouter(tags=["consents"])

STUDENT = "/api/v1/students/{student_id}"
CONSENTS = STUDENT + "/consents"

AssignmentSource = Annotated[
    TutorAssignmentSource, Depends(get_tutor_assignment_source)
]

STUDENT_NOT_FOUND = problem_response_doc(
    "No such student, or not visible to the caller (not_found)."
)
CONSENT_REQUIRED = (
    "the student is a minor without a valid consent from a linked, active"
    " guardian (guardian_consent_required)"
)


@router.put(
    STUDENT + "/account",
    responses={
        **PERMISSION_RESPONSES,
        404: STUDENT_NOT_FOUND,
        409: problem_response_doc(
            "The student changed since it was loaded (version_conflict); the"
            " account is linked to another student (student_account_in_use);"
            f" or the account is active with the student role and {CONSENT_REQUIRED}."
        ),
        422: problem_response_doc(
            "An unknown field or a missing user_id or version"
            " (validation_error); an unknown or archived account"
            " (student_account_unavailable)."
        ),
    },
)
async def set_student_account(
    principal: Annotated[Principal, require(FAMILY_MANAGE)],
    student_id: uuid.UUID,
    body: StudentAccountUpdate,
    uow: UnitOfWorkDep,
    source: AssignmentSource,
) -> StudentDetail:
    """Link a login account to the student profile (the account then sees
    only this profile while it holds the student role), or unlink it with
    ``null``. A minor's account can be active only with a guardian's
    consent."""
    return await service.set_student_account(
        uow.session,
        principal=principal,
        source=source,
        student_id=student_id,
        body=body,
    )


@router.get(CONSENTS, responses={**PERMISSION_RESPONSES, 404: STUDENT_NOT_FOUND})
async def get_consents(
    principal: Annotated[Principal, require(CONSENT_RECORD)],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    source: AssignmentSource,
) -> ConsentOverview:
    """The student's consents the caller may see, the guardians they may name
    and the scopes they may record now."""
    return await service.get_consents(
        uow.session, principal=principal, source=source, student_id=student_id
    )


@router.post(
    CONSENTS,
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        404: STUDENT_NOT_FOUND,
        409: problem_response_doc(
            "An active consent for this guardian and scope already exists"
            " (consent_already_given)."
        ),
        422: problem_response_doc(
            "An unknown scope, method or field (validation_error); a method"
            " the caller may not use (consent_method_invalid); a guardian not"
            " linked to the student or inactive (consent_guardian_invalid);"
            " student_login for an adult or guardian_academic_access for a"
            " minor (consent_not_applicable)."
        ),
    },
)
async def record_consent(
    principal: Annotated[Principal, require(CONSENT_RECORD)],
    student_id: uuid.UUID,
    body: ConsentCreate,
    uow: UnitOfWorkDep,
    source: AssignmentSource,
) -> ConsentRecordItem:
    """Record a consent, stamped with the current privacy-notice version.
    Staff record paper or in-person consents; a guardian allows a minor's
    login in the app; an adult student shares academic records with one
    guardian in the app."""
    return await service.record_consent(
        uow.session,
        principal=principal,
        source=source,
        student_id=student_id,
        body=body,
    )


@router.post(
    CONSENTS + "/{consent_id}/revoke",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc(
            "No such student or consent, or not visible to the caller (not_found)."
        ),
        409: problem_response_doc(
            "The consent was already revoked (consent_already_revoked)."
        ),
    },
)
async def revoke_consent(
    principal: Annotated[Principal, require(CONSENT_RECORD)],
    student_id: uuid.UUID,
    consent_id: uuid.UUID,
    uow: UnitOfWorkDep,
    source: AssignmentSource,
) -> ConsentRevocation:
    """Revoke a consent. When it was a minor's last valid login consent, the
    student's login is suspended in the same transaction and its sessions
    end."""
    return await service.revoke_consent(
        uow.session,
        principal=principal,
        source=source,
        student_id=student_id,
        consent_id=consent_id,
    )
