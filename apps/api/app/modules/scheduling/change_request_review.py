"""Staff review of change requests (#62 C, F; spec §3 "admin approvals,
exception handling, and audit trails"; ADR-0005 D2, D6, D9).

The scheduling half of the decision. The workflow
(``app.workflows.change_request_decision``) calls these functions, then the
reschedule seam, then resolves the inbox item
(``administration.service.decide_approval_request``) and notifies the
requester (:func:`notify_change_request_decision`), all in the request's
one Unit of Work. Nothing here commits.

- :func:`get_change_request_review`: ``GET …/{id}/review``; ``404`` for an
  unknown id; at most six statements.
- :func:`approve_change_request` (C2 steps 3-11, 13): the request without a
  lock (``404``) → ``422`` for a member a cancellation can't take
  (``classroom_id``, ``override``) → the locks: a **cancellation** takes the
  #55 order (the series ``FOR UPDATE`` when there is one → the session ``FOR
  UPDATE``), a **reschedule** the #54 order (``lock_students`` → the zone
  ``FOR SHARE`` → the series ``FOR SHARE`` → the session ``FOR UPDATE``);
  then the request ``FOR UPDATE`` → ``409 version_conflict`` → ``409
  change_request_not_pending`` (``details.status``) → ``409
  change_request_session_started`` → ``409 change_request_session_changed``
  (``details.state``) → ``422 exception_not_applicable`` → the change through
  the existing services: #55 ``cancel_class_session`` (reason
  ``family_request`` / ``tutor_request``) or the #54 move
  (``update_class_session``: every check live with the session's own tutor,
  the booking notice measured from the request's ``created_at``, E4) with
  the requester left out of their notifications → the effective outcome
  (:mod:`~app.modules.scheduling.change_request_outcome`) → ``approved`` →
  the named audit event ``scheduling.change_request_decided``. The services
  take the same locks again: row locks and advisory transaction locks are
  re-entrant within one transaction. A ``409`` / ``422`` of the move passes
  through unchanged and the request stays ``pending`` (rollback).
- :func:`reject_change_request` (C4 steps 3-7): the request ``FOR UPDATE``
  (``404``) → ``409 version_conflict`` → ``409 change_request_not_pending``
  → ``rejected`` with the reason → the audit event. Allowed after the start
  (E6); the session is untouched.

**Notifications (F1):** ``scheduling.change_request_approved``
``{change_request_id, kind, starts_at, new_starts_at, makeup_until,
exception}`` and ``scheduling.change_request_rejected`` ``{change_request_id,
kind, starts_at}`` (``starts_at`` is the session's start when the request
was made): never a reason, a tutor, a room or a classification. Only to the
requester, while their account is active and, for a family requester,
they may still act for the student (#59 S1); otherwise nobody, silently.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.booking import get_class_session
from app.modules.scheduling.cancellation import cancel_class_session
from app.modules.scheduling.change_request_outcome import (
    effective_outcome,
    exception_applicable,
    final_outcome,
    request_outcome,
    shown_outcome,
)
from app.modules.scheduling.change_request_schemas import (
    ChangeRequestApprove,
    ChangeRequestOutcome,
    ChangeRequestPerson,
    ChangeRequestPolicy,
    ChangeRequestReview,
    ChangeRequestReviewSession,
    ChangeRequestRoom,
    ChangeRequestStudent,
    ChangeRequestSubject,
)
from app.modules.scheduling.change_requests import (
    CHANGE_REQUEST_TYPE,
    decision_model,
    outcome_model,
    student_of,
)
from app.modules.scheduling.errors import (
    ChangeRequestNotPendingError,
    ChangeRequestSessionChangedError,
    ChangeRequestSessionStartedError,
    SessionValidationError,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.models import (
    CHANGE_REQUEST_APPROVED,
    CHANGE_REQUEST_CANCELLATION,
    CHANGE_REQUEST_FAMILY,
    CHANGE_REQUEST_PENDING,
    CHANGE_REQUEST_REJECTED,
    ClassSeries,
    ClassSession,
    ClassSessionParticipant,
    SessionChangeRequest,
)
from app.modules.scheduling.offer_flow import lock_series, lock_sessions
from app.modules.scheduling.offer_records import rfc3339
from app.modules.scheduling.requests import user_may_act_for
from app.modules.scheduling.schemas import (
    ClassSessionDetail,
    ClassSessionUpdate,
    SchedulingWarning,
)
from app.modules.scheduling.session_edit import update_class_session
from app.modules.scheduling.states import SessionKind, SessionState

CHANGE_REQUEST_APPROVED_NOTIFICATION: Final = "scheduling.change_request_approved"
CHANGE_REQUEST_REJECTED_NOTIFICATION: Final = "scheduling.change_request_rejected"
DECIDED_EVENT: Final = "scheduling.change_request_decided"

FAMILY_REQUEST_REASON: Final = "family_request"
TUTOR_REQUEST_REASON: Final = "tutor_request"
"""The system codes stored as a cancelled session's reason (the web
translates them; the #54 ``series_changed`` style)."""

REVIEW_PATH: Final = "/schedule/change-requests/{change_request_id}"
"""The staff review page (web G1), named by ``409
change_request_review_route_required``."""

ACTIVE: Final = "active"

_MESSAGES: Final = {
    "reason_required": "Give a reason for the exception.",
    "exception_not_applicable": (
        "This request is already excused; approve it without an exception."
    ),
    "not_applicable": "Only a reschedule takes this.",
}


def review_path(change_request_id: uuid.UUID) -> str:
    return REVIEW_PATH.format(change_request_id=change_request_id)


def _error(field: str, code: str, message_key: str | None = None) -> FieldError:
    return {
        "path": ["body", field],
        "code": code,
        "message": _MESSAGES[message_key or code],
    }


class ChangeRequestApprovedParams(BaseModel):
    """``scheduling.change_request_approved`` (F1): ids, the kind, instants
    and the exception flag only."""

    change_request_id: str
    kind: str
    starts_at: str
    new_starts_at: str | None
    makeup_until: str | None
    exception: bool


class ChangeRequestRejectedParams(BaseModel):
    """``scheduling.change_request_rejected`` (F1)."""

    change_request_id: str
    kind: str
    starts_at: str


def register_change_request_notifications() -> None:
    """Register the two types (again: a no-op)."""
    administration.register_notification_type(
        CHANGE_REQUEST_APPROVED_NOTIFICATION, ChangeRequestApprovedParams
    )
    administration.register_notification_type(
        CHANGE_REQUEST_REJECTED_NOTIFICATION, ChangeRequestRejectedParams
    )


# --- C1: the staff read --------------------------------------------------------------


async def get_change_request_review(
    session: AsyncSession, change_request_id: uuid.UUID
) -> ChangeRequestReview:
    """``GET /api/v1/change-requests/{change_request_id}/review``: at most
    six statements (the request with its session and participant; the
    policy version with the inbox item's id; the student; the subject; the
    accounts; the room)."""
    participant = (
        select(ClassSessionParticipant.student_id)
        .where(ClassSessionParticipant.class_session_id == ClassSession.id)
        .order_by(ClassSessionParticipant.student_id)
        .limit(1)
        .correlate(ClassSession)
        .scalar_subquery()
    )
    found = (
        await session.execute(
            select(SessionChangeRequest, ClassSession, participant)
            .join(
                ClassSession, ClassSession.id == SessionChangeRequest.class_session_id
            )
            .where(SessionChangeRequest.id == change_request_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if found is None:
        raise RecordNotFoundError()
    request, row, participant_id = found
    policy, approval_id = await administration.get_policy_version_and_approval_id(
        session,
        request.policy_id,
        request_type=CHANGE_REQUEST_TYPE,
        subject_id=request.id,
    )
    student_id = student_of(request, participant_id)
    student = (
        None
        if student_id is None
        else (await families.student_references(session, [student_id])).get(student_id)
    )
    subject = (await catalog.subject_references(session, [row.subject_id])).get(
        row.subject_id
    )
    account_ids = {request.requested_by_user_id}
    if row.tutor_user_id is not None:
        account_ids.add(row.tutor_user_id)
    if request.decided_by_user_id is not None:
        account_ids.add(request.decided_by_user_id)
    accounts = await identity.get_account_references(session, account_ids)
    room = None
    if row.classroom_id is not None:
        room = (await catalog.classroom_references(session, [row.classroom_id])).get(
            row.classroom_id
        )

    def person(user_id: uuid.UUID | None) -> ChangeRequestPerson | None:
        if user_id is None:
            return None
        account = accounts.get(user_id)
        return ChangeRequestPerson(
            id=user_id, display_name="" if account is None else account.display_name
        )

    requested_by = person(request.requested_by_user_id)
    assert requested_by is not None  # noqa: S101 - always set
    stored = request_outcome(request)
    final = final_outcome(request)
    shown = shown_outcome(request)
    now = clock.now()
    return ChangeRequestReview(
        id=request.id,
        kind=request.kind,  # type: ignore[arg-type]
        status=request.status,  # type: ignore[arg-type]
        initiated_by=request.initiated_by,  # type: ignore[arg-type]
        requested_by=requested_by,
        student=None
        if student_id is None
        else ChangeRequestStudent(
            id=student_id, display_name="" if student is None else student.display_name
        ),
        subject=ChangeRequestSubject(
            id=row.subject_id,
            name="" if subject is None else subject.name,
            education_level_name=""
            if subject is None
            else subject.education_level_name,
        ),
        session=ChangeRequestReviewSession(
            id=row.id,
            state=SessionState(row.state).value,
            kind=SessionKind(row.kind).value,
            starts_at=row.starts_at.astimezone(UTC),
            ends_at=row.ends_at.astimezone(UTC),
            series_id=row.series_id,
            tutor=person(row.tutor_user_id),
            classroom=None
            if row.classroom_id is None
            else ChangeRequestRoom(
                id=row.classroom_id, name="" if room is None else room.name
            ),
            version=row.version,
        ),
        session_starts_at=request.session_starts_at.astimezone(UTC),
        session_ends_at=request.session_ends_at.astimezone(UTC),
        proposed_starts_at=request.proposed_starts_at,
        proposed_ends_at=request.proposed_ends_at,
        note=request.note,
        policy=ChangeRequestPolicy(
            id=request.policy_id,
            effective_from=request.created_at
            if policy is None
            else policy.effective_from,
        ),
        request_outcome=_outcome(
            stored.classification, stored.makeup_allowed, stored.makeup_until
        ),
        final_outcome=None
        if final is None
        else _outcome(final.classification, final.makeup_allowed, final.makeup_until),
        outcome=_outcome(
            shown.classification, shown.makeup_allowed, shown.makeup_until
        ),
        decision=decision_model(request),
        decided_by=person(request.decided_by_user_id),
        approval_request_id=approval_id,
        created_at=request.created_at,
        resolved_at=request.resolved_at,
        version=request.version,
        can_decide=request.status == CHANGE_REQUEST_PENDING
        and row.state == SessionState.CONFIRMED
        and row.starts_at > now,
    )


def _outcome(
    classification: str, allowed: bool, until: datetime | None
) -> ChangeRequestOutcome:
    return outcome_model(classification, allowed, until)


# --- C2: approve ---------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovedChange:
    """What the approval changed, for the workflow's seam and response."""

    request: SessionChangeRequest
    class_session: ClassSessionDetail
    warnings: list[SchedulingWarning]
    previous_starts_at: datetime
    previous_ends_at: datetime


async def _locked_request(
    session: AsyncSession, change_request_id: uuid.UUID
) -> SessionChangeRequest:
    locked: SessionChangeRequest | None = await session.scalar(
        select(SessionChangeRequest)
        .where(SessionChangeRequest.id == change_request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise RecordNotFoundError()
    return locked


async def _lock_for_cancellation(
    session: AsyncSession, class_session_id: uuid.UUID
) -> ClassSession:
    """The #55 order: the series ``FOR UPDATE`` → the session ``FOR UPDATE``."""
    series_id = await session.scalar(
        select(ClassSession.series_id).where(ClassSession.id == class_session_id)
    )
    if series_id is not None:
        await lock_series(session, series_id)
    [row] = await lock_sessions(session, [class_session_id])
    return row


async def _lock_for_move(
    session: AsyncSession, class_session_id: uuid.UUID
) -> ClassSession:
    """The #54 B1 order: ``lock_students`` → the zone ``FOR SHARE`` → the
    series ``FOR SHARE`` → the session ``FOR UPDATE``."""
    found = await session.get(ClassSession, class_session_id)
    if found is None:  # pragma: no cover - sessions are never deleted
        raise RecordNotFoundError()
    students = sorted(
        await session.scalars(
            select(ClassSessionParticipant.student_id).where(
                ClassSessionParticipant.class_session_id == class_session_id
            )
        )
    )
    await lock_students(session, students)
    await administration.get_center_time_zone(session, for_share=True)
    if found.series_id is not None:
        await session.scalar(
            select(ClassSeries.id)
            .where(ClassSeries.id == found.series_id)
            .with_for_update(read=True)
        )
    row = await session.scalar(
        select(ClassSession)
        .where(ClassSession.id == class_session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:  # pragma: no cover - sessions are never deleted
        raise RecordNotFoundError()
    return row


def _not_applicable(body: ChangeRequestApprove) -> list[FieldError]:
    return [
        _error(field, f"{field.removesuffix('_id')}_not_applicable", "not_applicable")
        for field in ("classroom_id", "override")
        if field in body.model_fields_set
    ]


async def approve_change_request(
    session: AsyncSession,
    change_request_id: uuid.UUID,
    body: ChangeRequestApprove,
    *,
    actor_id: uuid.UUID,
) -> ApprovedChange:
    """C2 steps 3-11 and 13 (module docstring)."""
    found = await session.get(SessionChangeRequest, change_request_id)
    if found is None:
        raise RecordNotFoundError()
    is_cancellation = found.kind == CHANGE_REQUEST_CANCELLATION
    if is_cancellation:
        errors = _not_applicable(body)
        if errors:
            raise SessionValidationError(errors)
        row = await _lock_for_cancellation(session, found.class_session_id)
    else:
        row = await _lock_for_move(session, found.class_session_id)
    request = await _locked_request(session, change_request_id)
    ensure_version(request, body.version)
    if request.status != CHANGE_REQUEST_PENDING:
        raise ChangeRequestNotPendingError(request.status)
    now = clock.now()
    if row.starts_at <= now:
        raise ChangeRequestSessionStartedError()
    if row.state != SessionState.CONFIRMED:
        raise ChangeRequestSessionChangedError(row.state)
    if body.exception and not exception_applicable(request.classification):
        raise SessionValidationError([_error("exception", "exception_not_applicable")])

    previous_starts_at, previous_ends_at = row.starts_at, row.ends_at
    requester = [request.requested_by_user_id]
    warnings: list[SchedulingWarning] = []
    if is_cancellation:
        await cancel_class_session(
            session,
            row.id,
            expected_version=row.version,
            reason=FAMILY_REQUEST_REASON
            if request.initiated_by == CHANGE_REQUEST_FAMILY
            else TUTOR_REQUEST_REASON,
            by_user_id=actor_id,
            initiated_by="family"
            if request.initiated_by == CHANGE_REQUEST_FAMILY
            else "tutor",
            change_request_id=request.id,
            exclude_recipient_ids=requester,
        )
        detail = await get_class_session(session, row.id)
    else:
        assert request.proposed_starts_at is not None  # noqa: S101 - CHECK
        assert request.proposed_ends_at is not None  # noqa: S101 - CHECK
        move = {
            "version": row.version,
            "starts_at": request.proposed_starts_at,
            "ends_at": request.proposed_ends_at,
        }
        if "classroom_id" in body.model_fields_set:
            move["classroom_id"] = body.classroom_id
        if body.override is not None:
            move["override"] = body.override
        updated = await update_class_session(
            session,
            row.id,
            ClassSessionUpdate.model_validate(move),
            actor_id=actor_id,
            notice_reference_at=request.created_at,
            exclude_recipient_ids=requester,
        )
        warnings = list(updated.warnings)
        detail = ClassSessionDetail.model_validate(
            updated.model_dump(exclude={"warnings"})
        )

    policy = (
        await administration.get_scheduling_policy_version(session, request.policy_id)
        if body.exception
        else None
    )
    final = effective_outcome(
        stored=request_outcome(request),
        exception=body.exception,
        kind=request.kind,
        policy=policy,
        session_starts_at=request.session_starts_at,
        requested_at=request.created_at,
    )
    request.status = CHANGE_REQUEST_APPROVED
    request.exception = body.exception
    request.decision_reason = body.reason
    request.decided_by_user_id = actor_id
    request.resolved_at = now
    request.final_classification = final.classification
    request.final_makeup_allowed = final.makeup_allowed
    request.final_makeup_until = final.makeup_until
    await session.flush()
    _record_decision(session, request, decision="approved")
    return ApprovedChange(
        request=request,
        class_session=detail,
        warnings=warnings,
        previous_starts_at=previous_starts_at,
        previous_ends_at=previous_ends_at,
    )


def _record_decision(
    session: AsyncSession, request: SessionChangeRequest, *, decision: str
) -> None:
    """C2 step 13 / C4 step 7: the named audit event."""
    record_audit_event(
        session,
        action=DECIDED_EVENT,
        record_type="session_change_requests",
        record_id=request.id,
        details={
            "change_request_id": request.id,
            "class_session_id": request.class_session_id,
            "kind": request.kind,
            "initiated_by": request.initiated_by,
            "decision": decision,
            "exception": request.exception,
            "classification": request.classification,
            "final_classification": request.final_classification,
            "final_makeup_until": request.final_makeup_until,
            "policy_id": request.policy_id,
            "reason": request.decision_reason,
        },
    )


# --- C4: reject ----------------------------------------------------------------------


async def reject_change_request(
    session: AsyncSession,
    change_request_id: uuid.UUID,
    *,
    version: int,
    reason: str,
    actor_id: uuid.UUID,
) -> SessionChangeRequest:
    """C4 steps 3-7 (module docstring). ``reason`` is already trimmed and
    1-1000 characters long (the body)."""
    request = await _locked_request(session, change_request_id)
    ensure_version(request, version)
    if request.status != CHANGE_REQUEST_PENDING:
        raise ChangeRequestNotPendingError(request.status)
    request.status = CHANGE_REQUEST_REJECTED
    request.decision_reason = reason
    request.decided_by_user_id = actor_id
    request.resolved_at = clock.now()
    await session.flush()
    _record_decision(session, request, decision="rejected")
    return request


# --- F1: the requester's notification ------------------------------------------------


async def _requester_may_hear(
    session: AsyncSession, request: SessionChangeRequest
) -> bool:
    if request.initiated_by == CHANGE_REQUEST_FAMILY and request.student_id is not None:
        return await user_may_act_for(
            session, request.requested_by_user_id, request.student_id
        )
    account = (
        await identity.get_account_references(session, [request.requested_by_user_id])
    ).get(request.requested_by_user_id)
    return account is not None and account.state == ACTIVE


async def notify_change_request_decision(
    session: AsyncSession, request: SessionChangeRequest
) -> bool:
    """F1: the requester's notification of an approved or rejected request,
    when they may still hear about it. Returns whether one was sent."""
    if request.status not in (CHANGE_REQUEST_APPROVED, CHANGE_REQUEST_REJECTED):
        return False  # pragma: no cover - only decided requests are notified
    if not await _requester_may_hear(session, request):
        return False
    params: BaseModel
    if request.status == CHANGE_REQUEST_APPROVED:
        notification_type = CHANGE_REQUEST_APPROVED_NOTIFICATION
        params = ChangeRequestApprovedParams(
            change_request_id=str(request.id),
            kind=request.kind,
            starts_at=rfc3339(request.session_starts_at),
            new_starts_at=None
            if request.proposed_starts_at is None
            else rfc3339(request.proposed_starts_at),
            makeup_until=None
            if request.final_makeup_until is None
            else rfc3339(request.final_makeup_until),
            exception=request.exception,
        )
    else:
        notification_type = CHANGE_REQUEST_REJECTED_NOTIFICATION
        params = ChangeRequestRejectedParams(
            change_request_id=str(request.id),
            kind=request.kind,
            starts_at=rfc3339(request.session_starts_at),
        )
    await administration.create_notification(
        session,
        recipient_user_id=request.requested_by_user_id,
        notification_type=notification_type,
        params=params,
    )
    return True
