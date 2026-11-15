"""Approval inbox routes (#40; ``app.modules.administration.approvals``).

Every route is ``authenticated()``: what a caller sees depends on the review
permission of each request's type. The list holds only the types the caller
may review (empty without any); the detail and the decisions answer ``404
not_found`` for a request the caller may not review, exactly as for a missing
one.

- ``GET /api/v1/approval-requests``: filtered by ``status`` (``open`` by
  default: ``pending`` and ``changes_requested``), ``request_type``,
  ``assignee`` and ``priority`` (#118); ordered by ``sort``: ``priority``
  (the default: high first, then oldest first), ``oldest`` or ``newest``.
- ``GET /api/v1/approval-requests/{request_id}``.
- ``POST …/{request_id}/approve`` (no body), ``POST …/reject`` (``{reason?}``)
  and ``POST …/request-changes`` (``{reason}``).
- ``POST …/{request_id}/assign`` (``{assignee_user_id | null, version}``)
  and ``GET …/{request_id}/assignee-options`` (#118).

Decision order: ``401`` → ``csrf_failed`` / ``privacy_notice_required`` →
``422`` (the body) → ``404`` → for a type decided on its own page that says
so (session closeouts, #312): its own ``409`` (``closeout_review_route_required``)
whatever the request's status → ``409 approval_action_unsupported`` → ``409
approval_transition_invalid`` → ``422`` on ``reason`` → the type's own errors
(for example ``409 guardian_consent_required`` from a registration). The
decisions pass ``via_inbox=True``; no other caller does.

Assignment order: ``401`` → ``csrf_failed`` / ``privacy_notice_required`` →
``422`` (the body) → ``404`` → ``409 version_conflict`` → ``409
approval_request_closed`` → ``422 approval_assignee_ineligible``. Not
``idempotent()``: it posts no ledger row, and ``version`` guards a resend.
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
    ApprovalAssigneeOptions,
    ApprovalPriority,
    ApprovalRejectBody,
    ApprovalRequestChangesBody,
    ApprovalRequestItem,
    ApprovalSort,
    ApprovalStatusFilter,
    AssignApprovalBody,
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
        " guardian_consent_required, student_profile_required). A type decided"
        " on its own page answers its own 409 first: a session closeout always"
        " answers closeout_review_route_required with details.review_path,"
        " whatever its status."
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
ASSIGN_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTH_RESPONSES,
    **CSRF_RESPONSES,
    404: NOT_FOUND,
    409: problem_response_doc(
        "The request changed since it was read (version_conflict), or it is"
        " approved, rejected or cancelled (approval_request_closed)."
    ),
    422: problem_response_doc(
        "The body is not valid (validation_error), or the assignee is unknown,"
        " not active or doesn't hold the type's review permission now"
        " (approval_assignee_ineligible)."
    ),
}

Viewer = Annotated[Principal, authenticated()]


@router.get(
    "",
    responses={
        **LIST_RESPONSES,
        422: problem_response_doc(
            "An unknown status, type, assignee or priority filter, an unknown"
            " sort, or a limit or offset out of range (validation_error)."
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
    priority: Annotated[
        ApprovalPriority | None,
        Query(description="high, normal or low; omitted for any."),
    ] = None,
    sort: Annotated[
        ApprovalSort,
        Query(
            description="priority (the default: high first, then oldest first),"
            " oldest or newest."
        ),
    ] = "priority",
) -> OffsetPage[ApprovalRequestItem]:
    """The approval requests of the types the caller may review, in ``sort``
    order (by default by priority, high first, then oldest first). Empty for
    a caller who reviews none."""
    return await approvals.list_approval_requests(
        uow.session,
        principal=principal,
        filters=approvals.ApprovalRequestFilters(
            status=status,
            request_type=request_type,
            assignee=assignee,
            priority=priority,
            sort=sort,
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
        uow.session,
        principal=principal,
        request_id=request_id,
        action="approve",
        via_inbox=True,
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
        via_inbox=True,
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
        via_inbox=True,
    )


@router.get("/{request_id}/assignee-options", responses=DETAIL_RESPONSES)
async def list_assignee_options(
    principal: Viewer, request_id: uuid.UUID, uow: UnitOfWorkDep
) -> ApprovalAssigneeOptions:
    """The users the request can be assigned to now: the active holders of
    its type's review permission (the caller included when eligible), by
    display name. Works for closed requests too."""
    return ApprovalAssigneeOptions(
        items=await approvals.list_assignee_options(
            uow.session, principal=principal, request_id=request_id
        )
    )


@router.post("/{request_id}/assign", responses=ASSIGN_RESPONSES)
async def assign_approval_request(
    principal: Viewer,
    request_id: uuid.UUID,
    body: AssignApprovalBody,
    uow: UnitOfWorkDep,
) -> ApprovalRequestItem:
    """Assign an open request to an eligible reviewer, reassign it, or
    unassign it (``assignee_user_id: null``). Assigning the current assignee
    changes nothing. The new assignee is notified when someone else assigned
    them."""
    return await approvals.assign_approval_request(
        uow.session,
        principal=principal,
        request_id=request_id,
        assignee_user_id=body.assignee_user_id,
        version=body.version,
    )
