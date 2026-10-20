"""Approval inbox routes (#40; ``app.modules.administration.approvals``).

Every route is ``authenticated()``: what a caller sees depends on the review
permission of each request's type. The list holds only the types the caller
may review (empty without any); the detail and the decisions answer ``404
not_found`` for a request the caller may not review, exactly as for a missing
one.

- ``GET /api/v1/approval-requests``: filtered by ``status`` (``open`` by
  default: ``pending`` and ``changes_requested``), ``request_type`` and
  ``assignee``; by priority (high first), then oldest first.
- ``GET /api/v1/approval-requests/{request_id}``.
- ``POST …/{request_id}/approve`` (no body), ``POST …/reject`` (``{reason?}``)
  and ``POST …/request-changes`` (``{reason}``).

Decision order: ``401`` → ``csrf_failed`` / ``privacy_notice_required`` →
``422`` (the body) → ``404`` → ``409 approval_action_unsupported`` → ``409
approval_transition_invalid`` → ``422`` on ``reason`` → the type's own errors
(for example ``409 guardian_consent_required`` from a registration).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError

from app.core.authz import (
    AUTH_RESPONSES,
    CSRF_RESPONSES,
    Principal,
    authenticated,
)
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.administration import approvals
from app.modules.administration.schemas import (
    ApprovalAssigneeFilter,
    ApprovalRejectBody,
    ApprovalRequestChangesBody,
    ApprovalRequestItem,
    ApprovalStatusFilter,
)

router = APIRouter(prefix="/api/v1/approval-requests", tags=["approvals"])

NOT_FOUND = problem_response_doc(
    "No such approval request, or one the caller may not review (not_found)."
)
DECISION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTH_RESPONSES,
    **CSRF_RESPONSES,
    404: NOT_FOUND,
    409: problem_response_doc(
        "The request's status doesn't allow the action"
        " (approval_transition_invalid), its type doesn't support it"
        " (approval_action_unsupported), or the type's own check refused it"
        " (for a registration: registration_already_decided,"
        " guardian_consent_required, student_profile_required)."
    ),
    422: problem_response_doc(
        "The body is not valid, or the reason is missing where the action needs"
        " one or longer than 1000 characters (validation_error on reason)."
    ),
}


def _request_type(
    request_type: Annotated[
        str | None,
        Query(
            max_length=64,
            description="One registered type, for example identity.registration.",
        ),
    ] = None,
) -> str | None:
    if request_type is not None and (
        request_type not in approvals.registered_approval_types()
    ):
        raise RequestValidationError(
            [
                {
                    "loc": ("query", "request_type"),
                    "type": "value_error",
                    "msg": "Unknown approval request type.",
                }
            ]
        )
    return request_type


LIST_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTH_RESPONSES,
    403: problem_response_doc(
        "The privacy notice must be accepted first (privacy_notice_required)."
    ),
}
DETAIL_RESPONSES: dict[int | str, dict[str, Any]] = {**LIST_RESPONSES, 404: NOT_FOUND}

Viewer = Annotated[Principal, authenticated()]


@router.get(
    "",
    responses={
        **LIST_RESPONSES,
        422: problem_response_doc(
            "An unknown status, type or assignee filter, or a limit or offset"
            " out of range (validation_error)."
        ),
    },
)
async def list_approval_requests(
    principal: Viewer,
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    request_type: Annotated[str | None, Depends(_request_type)],
    status: Annotated[
        ApprovalStatusFilter,
        Query(
            description="open (the default: pending and changes_requested),"
            " one status, or all."
        ),
    ] = "open",
    assignee: Annotated[
        ApprovalAssigneeFilter,
        Query(description="any (the default), me or unassigned."),
    ] = "any",
) -> OffsetPage[ApprovalRequestItem]:
    """The approval requests of the types the caller may review, by priority
    (high first), then oldest first. Empty for a caller who reviews none."""
    return await approvals.list_approval_requests(
        uow.session,
        principal=principal,
        filters=approvals.ApprovalRequestFilters(
            status=status, request_type=request_type, assignee=assignee
        ),
        page=page,
    )


@router.get("/{request_id}", responses=DETAIL_RESPONSES)
async def get_approval_request(
    principal: Viewer, request_id: uuid.UUID, uow: UnitOfWorkDep
) -> ApprovalRequestItem:
    """One approval request the caller may review."""
    return await approvals.get_approval_request(
        uow.session, principal=principal, request_id=request_id
    )


@router.post("/{request_id}/approve", responses=DECISION_RESPONSES)
async def approve_approval_request(
    principal: Viewer, request_id: uuid.UUID, uow: UnitOfWorkDep
) -> ApprovalRequestItem:
    """Approve a pending request; the type's handler applies the approval in
    the same transaction (a registration: the account becomes active with the
    requested role; confirm the person's identity first, out of band)."""
    return await approvals.decide_approval_request(
        uow.session, principal=principal, request_id=request_id, action="approve"
    )


@router.post("/{request_id}/reject", responses=DECISION_RESPONSES)
async def reject_approval_request(
    principal: Viewer,
    request_id: uuid.UUID,
    uow: UnitOfWorkDep,
    body: ApprovalRejectBody | None = None,
) -> ApprovalRequestItem:
    """Reject an open request, with a reason when the type requires one (a
    registration: the account is archived and can never sign in)."""
    return await approvals.decide_approval_request(
        uow.session,
        principal=principal,
        request_id=request_id,
        action="reject",
        reason=body.reason if body is not None else None,
    )


@router.post("/{request_id}/request-changes", responses=DECISION_RESPONSES)
async def request_changes_approval_request(
    principal: Viewer,
    request_id: uuid.UUID,
    uow: UnitOfWorkDep,
    body: ApprovalRequestChangesBody | None = None,
) -> ApprovalRequestItem:
    """Send a pending request back to its requester with a reason, when the
    type supports it."""
    return await approvals.decide_approval_request(
        uow.session,
        principal=principal,
        request_id=request_id,
        action="request_changes",
        reason=body.reason if body is not None else None,
    )
