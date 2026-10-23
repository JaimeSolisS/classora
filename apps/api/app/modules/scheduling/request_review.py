"""Staff review of session requests (#60 B, C, D, F; spec W2.3-W2.4;
ADR-0005 D2, D3, D9).

The scheduling half of the decision. The workflow
(``app.workflows.confirm_session_request``) calls these functions and then
resolves the inbox item (``administration.service.decide_approval_request``)
and notifies the requester (:func:`notify_request_decision`), all in the
request's one Unit of Work. Nothing here commits.

- :func:`get_session_request_review`: ``GET …/{request_id}/review``; ``404``
  for an unknown id; at most six statements.
- :func:`confirm_session_request` (C2 steps 3-9): the request without a lock
  (``404``) → ``lock_students`` → the zone ``FOR SHARE`` → the request ``FOR
  UPDATE`` → ``409 version_conflict`` → ``409 session_request_not_pending``
  (``details.status``) → ``409 session_request_started`` (``starts_at <=
  now``) → the #48 create path (with #49's override, #50's warnings and
  #52's offer) for the request's student, subject and time, the chosen tutor
  and room, the booking notice measured from the request's ``created_at``
  (E3) → the request ``confirmed`` with the session. A ``409``/``422`` of
  the create path passes through unchanged and the request stays
  ``pending`` (the Unit of Work rolls back). The create path takes the
  student lock and the zone again: advisory transaction locks and row locks
  are re-entrant within one transaction.
- :func:`reject_session_request` (D2 steps 3-6): the request ``FOR UPDATE``
  (``404``) → ``409 version_conflict`` → ``409 session_request_not_pending``
  → ``rejected`` with the reason. Allowed after the start (it frees the
  student's pending cap).

**Notifications (F):** :data:`SESSION_REQUEST_CONFIRMED` and
:data:`SESSION_REQUEST_REJECTED`, params ``{session_request_id,
student_name, subject_name, starts_at}``: never a tutor, a room, a session
id or the reason. Only the requester, and only while their account is
``active`` and they may still act for the student (#59 S1,
``requests.user_may_act_for``); otherwise nobody, silently.
"""

import uuid
from typing import Final

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.booking import create_class_session
from app.modules.scheduling.errors import (
    SessionRequestNotPendingError,
    SessionRequestStartedError,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.models import (
    SESSION_REQUEST_CONFIRMED,
    SESSION_REQUEST_PENDING,
    SESSION_REQUEST_REJECTED,
    SessionRequest,
)
from app.modules.scheduling.offer_records import rfc3339
from app.modules.scheduling.request_schemas import (
    SessionRequestConfirm,
    SessionRequestRequester,
    SessionRequestReview,
    SessionRequestReviewStudent,
    SessionRequestReviewSubject,
)
from app.modules.scheduling.requests import SESSION_REQUEST_TYPE, user_may_act_for
from app.modules.scheduling.schemas import ClassSessionCreate, ClassSessionCreated

SESSION_REQUEST_CONFIRMED_NOTIFICATION: Final = "scheduling.session_request_confirmed"
SESSION_REQUEST_REJECTED_NOTIFICATION: Final = "scheduling.session_request_rejected"

REVIEW_PATH: Final = "/schedule/requests/{request_id}"
"""The staff review page (web H1), named by ``409
session_request_review_route_required``."""


def review_path(request_id: uuid.UUID) -> str:
    return REVIEW_PATH.format(request_id=request_id)


class SessionRequestDecisionParams(BaseModel):
    """The params of both decision notifications (F1): ids, names and the
    requested start only."""

    session_request_id: str
    student_name: str
    subject_name: str
    starts_at: str


def register_request_notifications() -> None:
    """Register the two types (again: a no-op)."""
    administration.register_notification_type(
        SESSION_REQUEST_CONFIRMED_NOTIFICATION, SessionRequestDecisionParams
    )
    administration.register_notification_type(
        SESSION_REQUEST_REJECTED_NOTIFICATION, SessionRequestDecisionParams
    )


# --- B: the staff read ---------------------------------------------------------------


async def get_session_request_review(
    session: AsyncSession, request_id: uuid.UUID
) -> SessionRequestReview:
    """``GET /api/v1/session-requests/{request_id}/review``: at most six
    statements (the request, its inbox item, the student, the subject and
    the requester)."""
    row = await session.scalar(
        select(SessionRequest)
        .where(SessionRequest.id == request_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise RecordNotFoundError()
    approval_id = await administration.approval_request_id_for_subject(
        session, request_type=SESSION_REQUEST_TYPE, subject_id=row.id
    )
    student = (await families.student_references(session, [row.student_id])).get(
        row.student_id
    )
    subject = (await catalog.subject_references(session, [row.subject_id])).get(
        row.subject_id
    )
    requester = (
        await identity.get_account_references(session, [row.requested_by_user_id])
    ).get(row.requested_by_user_id)
    return SessionRequestReview(
        id=row.id,
        status=row.status,  # type: ignore[arg-type]
        version=row.version,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        student=SessionRequestReviewStudent(
            id=row.student_id,
            display_name="" if student is None else student.display_name,
            is_active=student is not None and student.is_active,
        ),
        subject=SessionRequestReviewSubject(
            id=row.subject_id,
            name="" if subject is None else subject.name,
            education_level_name=""
            if subject is None
            else subject.education_level_name,
            is_active=subject is not None
            and subject.is_active
            and subject.education_level_is_active,
        ),
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        duration_minutes=row.duration_minutes,
        note=row.note,
        requested_by=SessionRequestRequester(
            id=row.requested_by_user_id,
            display_name="" if requester is None else requester.display_name,
        ),
        approval_request_id=approval_id,
        class_session_id=row.class_session_id,
        rejection_reason=row.rejection_reason,
        can_decide=row.status == SESSION_REQUEST_PENDING
        and row.starts_at > clock.now(),
    )


# --- C and D: the decisions ----------------------------------------------------------


async def _locked(session: AsyncSession, request_id: uuid.UUID) -> SessionRequest:
    locked = await session.scalar(
        select(SessionRequest)
        .where(SessionRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise RecordNotFoundError()
    return locked


def _ensure_pending(row: SessionRequest, version: int) -> None:
    ensure_version(row, version)
    if row.status != SESSION_REQUEST_PENDING:
        raise SessionRequestNotPendingError(row.status)


async def confirm_session_request(
    session: AsyncSession,
    request_id: uuid.UUID,
    body: SessionRequestConfirm,
    *,
    actor_id: uuid.UUID,
) -> tuple[SessionRequest, ClassSessionCreated]:
    """C2 steps 3-9 (module docstring). Returns the confirmed request and
    the created session (``offered`` to the tutor)."""
    found = await session.get(SessionRequest, request_id)
    if found is None:
        raise RecordNotFoundError()
    # Lock order: students → zone → request (→ the approval row, taken by
    # the workflow's decide_approval_request afterwards).
    await lock_students(session, [found.student_id])
    await administration.get_center_time_zone(session, for_share=True)
    row = await _locked(session, request_id)
    _ensure_pending(row, body.version)
    if row.starts_at <= clock.now():
        raise SessionRequestStartedError()
    created = await create_class_session(
        session,
        ClassSessionCreate(
            kind="individual",
            student_id=row.student_id,
            subject_id=row.subject_id,
            tutor_user_id=body.tutor_user_id,
            classroom_id=body.classroom_id,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            respond_by=body.respond_by,
            override=body.override,
        ),
        actor_id=actor_id,
        notice_reference_at=row.created_at,
    )
    row.status = SESSION_REQUEST_CONFIRMED
    row.class_session_id = created.id
    row.resolved_at = clock.now()
    await session.flush()
    return row, created


async def reject_session_request(
    session: AsyncSession, request_id: uuid.UUID, *, version: int, reason: str
) -> SessionRequest:
    """D2 steps 3-6 (module docstring). ``reason`` is already trimmed and
    1-1000 characters long (the body)."""
    row = await _locked(session, request_id)
    _ensure_pending(row, version)
    row.status = SESSION_REQUEST_REJECTED
    row.rejection_reason = reason
    row.resolved_at = clock.now()
    await session.flush()
    return row


async def notify_request_decision(
    session: AsyncSession, row: SessionRequest, review: SessionRequestReview
) -> bool:
    """F1/F2: the requester's notification of a confirmed or rejected
    request, when they may still act for the student. Returns whether one
    was sent."""
    if row.status == SESSION_REQUEST_CONFIRMED:
        notification_type = SESSION_REQUEST_CONFIRMED_NOTIFICATION
    elif row.status == SESSION_REQUEST_REJECTED:
        notification_type = SESSION_REQUEST_REJECTED_NOTIFICATION
    else:  # pragma: no cover - only decided requests are notified
        return False
    if not await user_may_act_for(session, row.requested_by_user_id, row.student_id):
        return False
    await administration.create_notification(
        session,
        recipient_user_id=row.requested_by_user_id,
        notification_type=notification_type,
        params=SessionRequestDecisionParams(
            session_request_id=str(row.id),
            student_name=review.student.display_name,
            subject_name=review.subject.name,
            starts_at=rfc3339(row.starts_at),
        ),
    )
    return True
