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

Tutor availability (#45; ``staff/tutor_availability.py`` has the orders),
local wall-clock times of the center:

- ``GET …/availability`` and ``GET …/availability/check``: whoever may see
  the tutor.
- ``PUT …/availability/weekly``, ``POST …/availability/exceptions``,
  ``DELETE …/availability/exceptions/{exception_id}``: the tutor themselves
  or ``tutor.manage`` (an Academic Coordinator gets ``403``). A change that
  affects scheduled sessions is ``409 availability_change_requires_approval``
  (the M4-18 seam, ``staff/assignment_impact.py``).
- ``PATCH …/availability/settings`` (#197): the teaching limits and
  preferred classrooms, same edit rights; no impact check (limits are
  warnings only).

The search text ``q`` is a query parameter: the access log line has no query
string (#9), so it is never logged.
"""

import uuid
from datetime import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.exceptions import RequestValidationError

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.catalog.schemas import LocalDate, LocalTime
from app.modules.identity.schemas import UserState
from app.modules.staff import service
from app.modules.staff.assignment_impact import (
    AvailabilityImpactSource,
    get_availability_impact_source,
)
from app.modules.staff.permissions import TUTOR_VIEW
from app.modules.staff.schemas import (
    AvailabilityCheck,
    AvailabilityExceptionCreate,
    AvailabilityExceptionItem,
    AvailabilitySettingsUpdate,
    QualificationCreate,
    QualificationItem,
    QualificationStatusFilter,
    TutorAvailabilityDetail,
    TutorDetail,
    TutorListItem,
    TutorUpdate,
    WeeklyAvailabilityUpdate,
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


# --- Tutor availability (#45) --------------------------------------------------------

ImpactSource = Annotated[
    AvailabilityImpactSource, Depends(get_availability_impact_source)
]

AVAILABILITY_EDITORS_ONLY = problem_response_doc(
    f"{PERMISSION_RESPONSES[403]['description']} Also: the caller may see the"
    " tutor but is neither the tutor themselves nor a holder of tutor.manage"
    " (permission_denied)."
)
REQUIRES_APPROVAL = (
    "The change affects scheduled classes and needs approval; nothing changed"
    " (availability_change_requires_approval)."
)


@router.get(
    "/{user_id}/availability",
    responses={**PERMISSION_RESPONSES, 404: NOT_FOUND},
)
async def get_availability(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> TutorAvailabilityDetail:
    """The tutor's weekly windows and exceptions, in the center's local
    wall-clock time, with the teaching limits and preferred classrooms."""
    return await service.get_availability(
        uow.session, principal=principal, user_id=user_id
    )


@router.put(
    "/{user_id}/availability/weekly",
    responses={
        **PERMISSION_RESPONSES,
        403: AVAILABILITY_EDITORS_ONLY,
        404: NOT_FOUND,
        409: problem_response_doc(
            "The weekly availability changed since it was loaded, or another"
            f" first change created it (version_conflict). Or: {REQUIRES_APPROVAL}"
        ),
        422: problem_response_doc(
            "The body is not valid: a missing or negative version, more than 42"
            " windows, a weekday outside 1-7, a time not in the HH:MM form, an"
            " end not after its start (on ends_at), two windows of one weekday"
            " that overlap (window_overlap on the later item), an unknown field"
            " (validation_error)."
        ),
    },
)
async def replace_weekly_availability(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    body: WeeklyAvailabilityUpdate,
    uow: UnitOfWorkDep,
    impact: ImpactSource,
) -> TutorAvailabilityDetail:
    """Replace the tutor's weekly windows (``[]`` clears them). ``version`` 0
    is a tutor whose availability never changed. A body that changes nothing
    keeps the version."""
    return await service.replace_weekly_availability(
        uow.session, principal=principal, user_id=user_id, body=body, impact=impact
    )


@router.patch(
    "/{user_id}/availability/settings",
    responses={
        **PERMISSION_RESPONSES,
        403: AVAILABILITY_EDITORS_ONLY,
        404: NOT_FOUND,
        409: problem_response_doc(
            "The availability changed since it was loaded, or another first"
            " change created it (version_conflict)."
        ),
        422: problem_response_doc(
            "The body is not valid: a missing or negative version, a daily"
            " limit outside 15-1440, a weekly limit outside 15-10080 or below"
            " the daily limit (weekly_below_daily, also against the stored"
            " daily limit), a break outside 0-240, more than 10 classrooms, a"
            " classroom twice (duplicate_classroom), an unknown or inactive"
            " new classroom (classroom_unavailable), null classrooms, an"
            " unknown field (validation_error)."
        ),
    },
)
async def update_availability_settings(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    body: AvailabilitySettingsUpdate,
    uow: UnitOfWorkDep,
) -> TutorAvailabilityDetail:
    """Change the tutor's teaching limits (minutes; null clears one) and
    preferred classrooms (the list sent replaces the stored one). Limits only
    produce warnings when scheduling. ``version`` is the availability's; a
    body that changes nothing keeps it."""
    return await service.update_availability_settings(
        uow.session, principal=principal, user_id=user_id, body=body
    )


@router.post(
    "/{user_id}/availability/exceptions",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: AVAILABILITY_EDITORS_ONLY,
        404: NOT_FOUND,
        409: problem_response_doc(REQUIRES_APPROVAL),
        422: problem_response_doc(
            "The body is not valid: an unknown kind, a date not in the"
            " YYYY-MM-DD form, an end date before the start or more than 366"
            " days after it, only one time, an end time not after the start,"
            " extra availability without times, a note longer than 200"
            " characters, an unknown field (validation_error)."
        ),
    },
)
async def create_availability_exception(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    body: AvailabilityExceptionCreate,
    uow: UnitOfWorkDep,
    impact: ImpactSource,
) -> AvailabilityExceptionItem:
    """Add time off (``unavailable``, whole days or hours) or extra
    availability (``available``, hours) on each day of an inclusive range."""
    return await service.create_exception(
        uow.session, principal=principal, user_id=user_id, body=body, impact=impact
    )


@router.delete(
    "/{user_id}/availability/exceptions/{exception_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        403: AVAILABILITY_EDITORS_ONLY,
        404: problem_response_doc(
            "No such tutor or exception of this tutor, or the caller may not"
            " see the tutor (not_found)."
        ),
        409: problem_response_doc(REQUIRES_APPROVAL),
    },
)
async def delete_availability_exception(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    exception_id: uuid.UUID,
    uow: UnitOfWorkDep,
    impact: ImpactSource,
) -> None:
    """Delete one of the tutor's exceptions."""
    await service.delete_exception(
        uow.session,
        principal=principal,
        user_id=user_id,
        exception_id=exception_id,
        impact=impact,
    )


def _ensure_start_before_end(start: str, end: str) -> None:
    # HH:MM strings compare like the times they hold.
    if end <= start:
        errors: list[dict[str, Any]] = [
            {
                "loc": ("query", "end"),
                "type": "value_error",
                "msg": "end must be after start",
            }
        ]
        raise RequestValidationError(errors)


@router.get(
    "/{user_id}/availability/check",
    responses={
        **PERMISSION_RESPONSES,
        404: NOT_FOUND,
        422: problem_response_doc(
            "A missing date, start or end, a date not in the YYYY-MM-DD form, a"
            " time not in the HH:MM form, or an end not after the start"
            " (validation_error)."
        ),
    },
)
async def check_availability(
    principal: Annotated[Principal, require(TUTOR_VIEW)],
    user_id: uuid.UUID,
    uow: UnitOfWorkDep,
    local_date: Annotated[
        LocalDate, Query(alias="date", description="The local date, YYYY-MM-DD.")
    ],
    start: Annotated[LocalTime, Query(description="Local start time, HH:MM.")],
    end: Annotated[LocalTime, Query(description="Local end time, HH:MM, after start.")],
) -> AvailabilityCheck:
    """Whether the tutor is available on the local date from ``start`` to
    ``end`` (the center's wall-clock time), center closures included, with
    every reason they are not."""
    _ensure_start_before_end(start, end)
    return await service.check_availability(
        uow.session,
        principal=principal,
        user_id=user_id,
        local_date=local_date,
        start=time.fromisoformat(start),
        end=time.fromisoformat(end),
    )
