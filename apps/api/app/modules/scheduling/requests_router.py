"""Family routes: available slots and session requests (#59;
:mod:`app.modules.scheduling.requests` gives the orders).

All are ``authenticated()`` plus the family record policy (ADR-0005 D9: no
permission): a guardian acts for their linked children (an adult child only
with that student's academic-access consent), a student for themselves.
Anyone else gets an empty list or ``404``, never another family's data.

- ``GET /api/v1/available-slots``: ``rate_limit("available_slots.user")``.
- ``POST /api/v1/session-requests``: ``rate_limit("session_request.user")``.
- ``GET /api/v1/session-requests`` and ``GET …/{request_id}``.
- ``POST /api/v1/session-requests/{request_id}/withdraw``: the requester only.

The limits are counted per signed-in user (ADR-0008 amendment), every
request including ``4xx`` ones; over the limit → ``429 rate_limited`` with
``Retry-After``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.ratelimit import RATE_LIMIT_RESPONSES, RateLimiter, rate_limit
from app.core.uow import UnitOfWorkDep
from app.modules.scheduling import service
from app.modules.scheduling.request_schemas import (
    SESSION_REQUESTS_DEFAULT_LIMIT,
    SESSION_REQUESTS_MAX_LIMIT,
    AvailableSlots,
    AvailableSlotsQuery,
    SessionRequestCreate,
    SessionRequestDetail,
    SessionRequestPage,
    SessionRequestStatusFilter,
    SessionRequestWithdraw,
)

slots_router = APIRouter(prefix="/api/v1/available-slots", tags=["session-requests"])
requests_router = APIRouter(
    prefix="/api/v1/session-requests", tags=["session-requests"]
)

_STUDENT_RESPONSES = {
    **PERMISSION_RESPONSES,
    403: problem_response_doc(
        "The student is an adult who hasn't given you consent"
        " (student_consent_required), or privacy_notice_required."
    ),
    404: problem_response_doc(
        "No such student, or not one you may act for (not_found)."
    ),
    422: problem_response_doc(
        "Invalid query or body (validation_error with field errors)."
    ),
    **RATE_LIMIT_RESPONSES,
}


@slots_router.get("", responses=_STUDENT_RESPONSES)
async def list_available_slots(
    principal: Annotated[Principal, authenticated()],
    limiter: Annotated[RateLimiter, rate_limit("available_slots.user")],
    query: Annotated[AvailableSlotsQuery, Query()],
    uow: UnitOfWorkDep,
) -> AvailableSlots:
    """The times your child (or you, as a student) can request for an
    individual session of the subject and length, for every local date from
    `from` to `to` (at most 14 dates, from today up to 60 days ahead, center
    zone).

    Slots start on the hour or the half hour within the center's opening
    hours, on dates the center is open, meet the booking notice, don't
    overlap the student's sessions, and at least one qualified tutor is
    free and available (which tutor is never shown). Rooms and other
    families' pending requests are not considered. `already_requested`
    marks a slot overlapping one of this student's pending requests.

    Range errors (422, on the field): `range_invalid`, `range_too_long`,
    `range_in_past`, `range_too_far`; references: `student_inactive`,
    `subject_unknown`, `subject_inactive`. At most 60 searches per 15
    minutes per user (429)."""
    return await service.available_slots(uow.session, principal, query)


@requests_router.post(
    "",
    status_code=201,
    responses={
        **_STUDENT_RESPONSES,
        409: problem_response_doc(
            "The student already has 5 pending requests"
            " (session_request_limit_reached), a pending request at an"
            " overlapping time (session_request_overlap), or the time is no"
            " longer available (slot_unavailable)."
        ),
    },
)
async def create_session_request(
    principal: Annotated[Principal, authenticated()],
    limiter: Annotated[RateLimiter, rate_limit("session_request.user")],
    body: SessionRequestCreate,
    uow: UnitOfWorkDep,
) -> SessionRequestDetail:
    """Request an individual session at an available time. It is a request,
    not a booking: it stays `pending` until the center confirms it or lets
    you know, and holds no time meanwhile. The center chooses the tutor.

    `starts_at` is on the 30-minute local grid (422 `not_on_grid`
    otherwise); other 422 codes on `starts_at`: `naive_datetime`,
    `not_whole_minutes`, `crosses_local_date`. At most 10 requests per
    hour per user (429)."""
    return await service.create_session_request(uow.session, principal, body)


@requests_router.get("", responses=PERMISSION_RESPONSES)
async def list_session_requests(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
    status: SessionRequestStatusFilter = "all",
    limit: Annotated[
        int, Query(ge=1, le=SESSION_REQUESTS_MAX_LIMIT)
    ] = SESSION_REQUESTS_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionRequestPage:
    """The requests of every student you may act for, newest first. A
    `student_id` you can't act for, or a caller without the guardian or
    student role, gets an empty page."""
    return await service.list_session_requests(
        uow.session,
        principal,
        student_id=student_id,
        status=status,
        limit=limit,
        offset=offset,
    )


_REQUEST_RESPONSES = {
    **PERMISSION_RESPONSES,
    404: problem_response_doc(
        "No such request, or not one of a student you may act for (not_found)."
    ),
}


@requests_router.get("/{request_id}", responses=_REQUEST_RESPONSES)
async def get_session_request(
    principal: Annotated[Principal, authenticated()],
    request_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> SessionRequestDetail:
    """One request of a student you may act for."""
    return await service.get_session_request(uow.session, principal, request_id)


@requests_router.post(
    "/{request_id}/withdraw",
    responses={
        **_REQUEST_RESPONSES,
        403: problem_response_doc(
            "You didn't make this request (permission_denied), or"
            " privacy_notice_required / csrf_failed."
        ),
        409: problem_response_doc(
            "The request changed since you loaded it (version_conflict) or is"
            " no longer pending (session_request_not_pending)."
        ),
    },
)
async def withdraw_session_request(
    principal: Annotated[Principal, authenticated()],
    request_id: uuid.UUID,
    body: SessionRequestWithdraw,
    uow: UnitOfWorkDep,
) -> SessionRequestDetail:
    """Withdraw a pending request you made; its inbox item is cancelled and
    the student's pending limit and time are freed."""
    return await service.withdraw_session_request(
        uow.session, principal, request_id, version=body.version
    )
