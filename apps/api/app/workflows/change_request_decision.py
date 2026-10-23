"""Staff decisions on change requests (#62 C2, C4; spec §3 "admin approvals,
exception handling, and audit trails"; architecture §4.2; ADR-0005 D2, D6).

The review routes (:mod:`app.workflows.change_requests_router`) call these
functions. Each runs in the request's **one Unit of Work**: the change to
the session (through the existing occurrence services), the request's
decision and effective outcome, the audit trail, the billing and pay seams,
the inbox item and the notifications commit together or not at all.

**Approve** (:func:`approve_change_request`), after the route's ``401`` /
``403`` (``schedule.manage``), the body's ``422`` (``reason_required`` for an
exception without a reason) and the ``403`` for an ``override`` without
``schedule.override``:

1. ``scheduling.service.approve_change_request``: ``404`` → ``422`` for a
   member a cancellation can't take → the locks → the request ``FOR
   UPDATE`` → ``409 version_conflict`` / ``change_request_not_pending`` /
   ``change_request_session_started`` / ``change_request_session_changed``
   → ``422 exception_not_applicable`` → the change: #55
   ``cancel_class_session`` (which runs its registered cancellation steps,
   ``app.workflows.session_cancellation.on_session_cancelled``, with the
   request's ``initiated_by`` and ``change_request_id``) or the #54 move
   (every check live with the session's own tutor; the booking notice
   measured from the request's ``created_at``); a ``409`` / ``422`` of the
   move passes through and the request stays ``pending`` → the effective
   outcome → ``approved`` → the audit event
   ``scheduling.change_request_decided``;
2. for a reschedule, :func:`app.workflows.session_reschedule.on_session_rescheduled`;
3. ``administration.service.decide_approval_request(action="approve")``:
   the handler accepts because the request is already ``approved``;
4. the requester's ``scheduling.change_request_approved`` notification
   (F1). Everyone else affected got the services' ``session_cancelled`` /
   ``session_rescheduled``, without the requester.

**Reject** (:func:`reject_change_request`): the request ``FOR UPDATE``
(``404``) → ``409 version_conflict`` / ``change_request_not_pending`` →
``rejected`` with the reason → the audit event →
``decide_approval_request(action="reject", reason)`` → the requester's
``scheduling.change_request_rejected`` notification. The session is
untouched; allowed after the start (E6).

**Lock order:** for a cancellation the series (``FOR UPDATE``) → the session
(``FOR UPDATE``), for a reschedule the students (advisory) → the center zone
(``FOR SHARE``) → the series (``FOR SHARE``) → the session (``FOR UPDATE``);
then the change request (``FOR UPDATE``) → the approval row (``FOR
UPDATE``, taken by ``decide_approval_request``). A reject takes the request
→ the approval row, as the requester's withdraw. The services take their
locks again inside the same transaction, which is safe:
``pg_advisory_xact_lock`` and row locks are re-entrant within a transaction.

**Consumers (M6-14 billing, M7-04 tutor pay):** they add their steps to
``app/workflows/session_cancellation.py`` and
``app/workflows/session_reschedule.py`` (both receive the
``change_request_id``) and read the effective outcome (classification,
makeup, policy version) with
``scheduling.service.get_change_request_outcome``. A rejected or withdrawn
request has no money effect.
"""

import uuid
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.change_request_schemas import (
    ChangeRequestApprove,
    ChangeRequestApproved,
    ChangeRequestReject,
    ChangeRequestReview,
)
from app.workflows.session_reschedule import on_session_rescheduled

__all__ = ["approve_change_request", "reject_change_request"]


async def _resolve_inbox_item(
    session: AsyncSession,
    principal: Principal,
    change_request_id: uuid.UUID,
    *,
    action: Literal["approve", "reject"],
    reason: str | None = None,
) -> None:
    approval_id = await administration.approval_request_id_for_subject(
        session,
        request_type=scheduling.CHANGE_REQUEST_TYPE,
        subject_id=change_request_id,
    )
    if approval_id is None:  # pragma: no cover - every request is created with one
        return
    await administration.decide_approval_request(
        session,
        principal=principal,
        request_id=approval_id,
        action=action,
        reason=reason,
    )


async def approve_change_request(
    session: AsyncSession,
    principal: Principal,
    change_request_id: uuid.UUID,
    body: ChangeRequestApprove,
) -> ChangeRequestApproved:
    """``POST /api/v1/change-requests/{change_request_id}/approve``: see the
    module docstring."""
    change = await scheduling.approve_change_request(
        session, change_request_id, body, actor_id=principal.user_id
    )
    request = change.request
    if request.kind == "reschedule":
        await on_session_rescheduled(
            session,
            class_session_id=request.class_session_id,
            previous_starts_at=change.previous_starts_at,
            previous_ends_at=change.previous_ends_at,
            initiated_by="family" if request.initiated_by == "family" else "tutor",
            change_request_id=request.id,
            by_user_id=principal.user_id,
        )
    await _resolve_inbox_item(
        session, principal, request.id, action="approve", reason=body.reason
    )
    await scheduling.notify_change_request_decision(session, request)
    review = await scheduling.get_change_request_review(session, request.id)
    return ChangeRequestApproved(
        change_request=review,
        class_session=change.class_session,
        warnings=change.warnings,
    )


async def reject_change_request(
    session: AsyncSession,
    principal: Principal,
    change_request_id: uuid.UUID,
    body: ChangeRequestReject,
) -> ChangeRequestReview:
    """``POST /api/v1/change-requests/{change_request_id}/reject``: see the
    module docstring."""
    request = await scheduling.reject_change_request(
        session,
        change_request_id,
        version=body.version,
        reason=body.reason,
        actor_id=principal.user_id,
    )
    await _resolve_inbox_item(
        session, principal, request.id, action="reject", reason=body.reason
    )
    await scheduling.notify_change_request_decision(session, request)
    return await scheduling.get_change_request_review(session, request.id)
