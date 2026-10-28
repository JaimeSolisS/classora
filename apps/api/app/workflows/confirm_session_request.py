"""Staff decisions on session requests (#60; spec W2.3-W2.4; architecture
§4.2; ADR-0005 D2, D3).

The review routes (:mod:`app.workflows.session_requests_router`) call these
functions. Each runs in the request's **one Unit of Work**: the decision of
the request, the session and its offer, the inbox item and the requester's
notification commit together or not at all.

**Confirm** (:func:`confirm_session_request`), after the route's ``401`` /
``403`` (``schedule.manage``), the body's ``422`` and the ``403`` for an
``override`` without ``schedule.override``:

1. ``scheduling.service.confirm_session_request``: ``404`` → the student
   lock → the zone ``FOR SHARE`` → the request ``FOR UPDATE`` → ``409
   version_conflict`` / ``session_request_not_pending`` /
   ``session_request_started`` → the #48 create path (#49 override, #50
   warnings, #52 offer to the chosen tutor; the booking notice measured from
   the request's ``created_at``, E3) → the request ``confirmed`` with its
   session. A ``409`` / ``422`` of the create path passes through and the
   request stays ``pending``;
2. ``administration.service.decide_approval_request(action="approve")``:
   the handler accepts because the request is already ``confirmed``;
3. the requester's ``scheduling.session_request_confirmed`` notification,
   when they may still act for the student (F2).

**Reject** (:func:`reject_session_request`): the request ``FOR UPDATE``
(``404``) → ``409 version_conflict`` / ``session_request_not_pending`` →
``rejected`` with the reason → ``decide_approval_request(action="reject",
reason)`` (the item stores the same reason) → the requester's
``scheduling.session_request_rejected`` notification (F2).

**Lock order:** students (advisory) → the center zone (``FOR SHARE``) → the
session request (``FOR UPDATE``) → the approval row (``FOR UPDATE``, taken by
``decide_approval_request``) for a confirm; the request → the approval row
for a reject, as the family's withdraw. The create path takes the student
lock and the zone again inside the same transaction, which is safe:
``pg_advisory_xact_lock`` and row locks are re-entrant within a transaction.

No charge is posted here: the session's charge is posted later by the
``billing:post_session_charges`` sweeper once a tutor confirms it (ADR-0006
E1; #91 removed the #60 seam).
"""

import uuid
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.request_schemas import (
    SessionRequestConfirm,
    SessionRequestConfirmed,
    SessionRequestReject,
    SessionRequestReview,
)

__all__ = [
    "confirm_session_request",
    "reject_session_request",
]


async def _resolve_inbox_item(
    session: AsyncSession,
    principal: Principal,
    request_id: uuid.UUID,
    *,
    action: Literal["approve", "reject"],
    reason: str | None = None,
) -> None:
    approval_id = await administration.approval_request_id_for_subject(
        session, request_type=scheduling.SESSION_REQUEST_TYPE, subject_id=request_id
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


async def confirm_session_request(
    session: AsyncSession,
    principal: Principal,
    request_id: uuid.UUID,
    body: SessionRequestConfirm,
) -> SessionRequestConfirmed:
    """``POST /api/v1/session-requests/{request_id}/confirm``: see the module
    docstring."""
    row, created = await scheduling.confirm_session_request(
        session, request_id, body, actor_id=principal.user_id
    )
    await _resolve_inbox_item(session, principal, row.id, action="approve")
    review = await scheduling.get_session_request_review(session, row.id)
    await scheduling.notify_request_decision(session, row, review)
    return SessionRequestConfirmed(request=review, class_session=created)


async def reject_session_request(
    session: AsyncSession,
    principal: Principal,
    request_id: uuid.UUID,
    body: SessionRequestReject,
) -> SessionRequestReview:
    """``POST /api/v1/session-requests/{request_id}/reject``: see the module
    docstring."""
    row = await scheduling.reject_session_request(
        session, request_id, version=body.version, reason=body.reason
    )
    await _resolve_inbox_item(
        session, principal, row.id, action="reject", reason=body.reason
    )
    review = await scheduling.get_session_request_review(session, row.id)
    await scheduling.notify_request_decision(session, row, review)
    return review
