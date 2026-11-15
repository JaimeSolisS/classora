"""Family and tutor routes of change requests (#62 B, D;
:mod:`app.modules.scheduling.change_requests` gives the orders).

All are ``authenticated()`` plus the change-request record policy
(ADR-0005 D9: no permission): a guardian or a student acts for a
participant of a confirmed session (#59 S1), a tutor for their own
session. Anyone else gets an empty list or ``404``. Staff review on the
routes of ``app.workflows.change_requests_router``.

- ``POST /api/v1/change-requests``: ``rate_limit("change_request.user")``
  (10 per hour per signed-in user, ADR-0008 amendment; every request
  counts, ``4xx`` included).
- ``GET /api/v1/change-requests`` and ``GET …/{change_request_id}``.
- ``POST /api/v1/change-requests/{change_request_id}/withdraw``: the
  requester only.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage
from app.core.ratelimit import RATE_LIMIT_RESPONSES, RateLimiter, rate_limit
from app.core.uow import UnitOfWorkDep
from app.modules.scheduling import service
from app.modules.scheduling.change_request_schemas import (
    CHANGE_REQUESTS_DEFAULT_LIMIT,
    CHANGE_REQUESTS_MAX_LIMIT,
    ChangeRequestCreate,
    ChangeRequestDetail,
    ChangeRequestItem,
    ChangeRequestStatusFilter,
    ChangeRequestWithdraw,
)

router = APIRouter(prefix="/api/v1/change-requests", tags=["change-requests"])

_NOT_FOUND = problem_response_doc(
    "No such request, or not one you may see (not_found)."
)


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "The student is an adult who hasn't given you consent"
            " (student_consent_required), or privacy_notice_required /"
            " csrf_failed."
        ),
        404: problem_response_doc(
            "No such session, or not one you may change (not_found): another"
            " family's or tutor's session, or one your family can't see yet."
        ),
        409: problem_response_doc(
            "Only individual sessions (change_request_kind_unsupported,"
            " details.kind); only a confirmed session"
            " (class_session_not_changeable, details.state); the session has"
            " started (change_request_session_started); it already has a"
            " pending request (change_request_pending, with"
            " details.change_request_id when you may see it); the proposed"
            " time isn't available (slot_unavailable)."
        ),
        422: problem_response_doc(
            "Invalid body (validation_error): proposed_starts_at is required"
            " for a reschedule (proposal_required) and not allowed for a"
            " cancellation (proposal_not_allowed); the proposal's bounds"
            " (naive_datetime, not_whole_minutes, crosses_local_date, ...),"
            " proposal_in_past, proposal_unchanged; a note over 500 characters."
        ),
        **RATE_LIMIT_RESPONSES,
    },
)
async def create_change_request(
    principal: Annotated[Principal, authenticated()],
    limiter: Annotated[RateLimiter, rate_limit("change_request.user")],
    body: ChangeRequestCreate,
    uow: UnitOfWorkDep,
) -> ChangeRequestDetail:
    """Ask the center to cancel or move one upcoming confirmed individual
    session: as a guardian or a student for your session, or as its tutor.
    A reschedule proposes one new start (same length). The scheduling
    policy's outcome (on time, late or excused, and whether a makeup is
    allowed) is recorded now and shown with the request. The center
    approves, approves as an exception or rejects it."""
    return await service.create_change_request(uow.session, principal, body)


@router.get("", responses=PERMISSION_RESPONSES)
async def list_change_requests(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
    student_id: uuid.UUID | None = None,
    class_session_id: uuid.UUID | None = None,
    status: ChangeRequestStatusFilter = "all",
    limit: Annotated[
        int, Query(ge=1, le=CHANGE_REQUESTS_MAX_LIMIT)
    ] = CHANGE_REQUESTS_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OffsetPage[ChangeRequestItem]:
    """The change requests you may see, newest first (an offset page:
    `limit` 1-100, default 25): your family's requests for the students you
    act for, or as a tutor the requests you made. Filters you can't use give
    an empty page."""
    return await service.list_change_requests(
        uow.session,
        principal,
        student_id=student_id,
        class_session_id=class_session_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/{change_request_id}", responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_change_request(
    principal: Annotated[Principal, authenticated()],
    change_request_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> ChangeRequestDetail:
    """One change request you may see."""
    return await service.get_change_request(uow.session, principal, change_request_id)


@router.post(
    "/{change_request_id}/withdraw",
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such request, or not one you made (not_found)."),
        409: problem_response_doc(
            "The request changed since you loaded it (version_conflict) or is"
            " no longer pending (change_request_not_pending)."
        ),
    },
)
async def withdraw_change_request(
    principal: Annotated[Principal, authenticated()],
    change_request_id: uuid.UUID,
    body: ChangeRequestWithdraw,
    uow: UnitOfWorkDep,
) -> ChangeRequestDetail:
    """Withdraw a pending request you made; its inbox item is cancelled.
    Allowed after the session has started."""
    return await service.withdraw_change_request(
        uow.session, principal, change_request_id, version=body.version
    )
