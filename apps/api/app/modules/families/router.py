"""Student profile routes (#33; ``app.modules.families.service``).

- ``GET /api/v1/students`` and ``GET /api/v1/students/{student_id}``:
  ``require("student.view")`` (every role holds it); the record policy then
  limits tutors, guardians and student users. A hidden student is ``404``.
- ``POST /api/v1/students`` and ``PATCH /api/v1/students/{student_id}``:
  ``require("student.manage")`` (Owners, Administrators, Academic
  Coordinators). No reauthentication (profile edits are not in ADR-0007 D10)
  and no ``DELETE`` (deletion is a retention action, M10-07).

Check order of the ``PATCH``: ``401`` → ``csrf_failed`` →
``permission_denied`` → ``422`` (the body) → ``404`` → ``409
version_conflict`` → ``422`` (future birthdate, ``emergency_contact_unknown``,
``education_level_unavailable``).

The search text ``q`` is a query parameter: the access log line has no query
string (#9), so it is never logged.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.families import service
from app.modules.families.assignments import (
    TutorAssignmentSource,
    get_tutor_assignment_source,
)
from app.modules.families.permissions import STUDENT_MANAGE, STUDENT_VIEW
from app.modules.families.schemas import (
    SearchText,
    StudentCreate,
    StudentDetail,
    StudentListItem,
    StudentStatus,
    StudentUpdate,
)

router = APIRouter(prefix="/api/v1/students", tags=["students"])

AssignmentSource = Annotated[
    TutorAssignmentSource, Depends(get_tutor_assignment_source)
]

NOT_FOUND = problem_response_doc("No such student, or not visible (not_found).")
BODY_RULES = (
    "a blank or too long name, a birthdate before 1900-01-01 or after today"
    " (in the center's time zone), a malformed email or phone, 0 or more than"
    " 5 emergency contacts, an unknown field (validation_error); an unknown or"
    " inactive education level (education_level_unavailable)"
)


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "An unknown status, a search longer than 100 characters or with"
            " more than five words, or a limit or offset out of range"
            " (validation_error)."
        ),
    },
)
async def list_students(
    principal: Annotated[Principal, require(STUDENT_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    source: AssignmentSource,
    q: Annotated[
        SearchText | None,
        Query(
            description=(
                "Name search, ignoring case and accents, in any word order and"
                " tolerant of typos (1-100 characters, at most 5 words)."
            )
        ),
    ] = None,
    status: Annotated[
        StudentStatus, Query(description="active (the default), inactive or all.")
    ] = "active",
    education_level_id: Annotated[
        uuid.UUID | None,
        Query(description="Only this level's students (unknown: an empty page)."),
    ] = None,
) -> OffsetPage[StudentListItem]:
    """The students the caller may see. With ``q``: by relevance, then by
    family names and given names; without: by family names, given names."""
    return await service.list_students(
        uow.session,
        principal=principal,
        source=source,
        q=q,
        status=status,
        education_level_id=education_level_id,
        page=page,
    )


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(f"The body is not valid: {BODY_RULES}."),
    },
)
async def create_student(
    principal: Annotated[Principal, require(STUDENT_MANAGE)],
    body: StudentCreate,
    uow: UnitOfWorkDep,
) -> StudentDetail:
    """Add an active student profile with 1 to 5 emergency contacts."""
    return await service.create_student(uow.session, principal=principal, body=body)


@router.get(
    "/{student_id}",
    responses={**PERMISSION_RESPONSES, 404: NOT_FOUND},
)
async def get_student(
    principal: Annotated[Principal, require(STUDENT_VIEW)],
    student_id: uuid.UUID,
    uow: UnitOfWorkDep,
    source: AssignmentSource,
) -> StudentDetail:
    """One student profile. ``personal`` is ``null`` for a viewer with
    limited access."""
    return await service.get_student(
        uow.session, principal=principal, source=source, student_id=student_id
    )


@router.patch(
    "/{student_id}",
    responses={
        **PERMISSION_RESPONSES,
        404: NOT_FOUND,
        409: problem_response_doc(
            "The student changed since it was loaded (version_conflict);"
            " reload and retry."
        ),
        422: problem_response_doc(
            f"The body is not valid: {BODY_RULES}; a null required field or a"
            " missing version (validation_error); an emergency contact id that"
            " is not this student's or is sent twice (emergency_contact_unknown)."
        ),
    },
)
async def update_student(
    principal: Annotated[Principal, require(STUDENT_MANAGE)],
    student_id: uuid.UUID,
    body: StudentUpdate,
    uow: UnitOfWorkDep,
    source: AssignmentSource,
) -> StudentDetail:
    """Change a profile: the fields sent, ``is_active`` to deactivate or
    reactivate, and ``emergency_contacts`` as the complete new list (items
    with an ``id`` keep that contact, without one add a contact, contacts
    left out are removed). Fields not sent stay unchanged."""
    return await service.update_student(
        uow.session,
        principal=principal,
        source=source,
        student_id=student_id,
        body=body,
    )
