"""Closeout review and corrections (#78 M5-11; spec §2, §3 "Approved hours
feed payroll. Tutors can request corrections and admins reapprove;
finalized payroll remains locked", §7, W3.2–W3.5; architecture §4.1,
§4.2, ISSUE-F1, ISSUE-F7).

Academic reviewers (``teaching_hours.approve``: Owner, Administrator,
Academic Coordinator) decide a submitted closeout on the staff session
page; its tutor resubmits after changes are requested and asks for a
correction after approval. Approved records are never edited: every
approval is an immutable revision (``session_closeout_approvals``), and a
re-approval inserts the next one, superseding the current one (M7-08 turns
that into a payroll adjustment when the earlier revision sits in a locked
period). The routes are in :mod:`app.workflows.session_closeout_router`.

**Status machine** (``session_closeouts.status``; the approval request in
brackets): ``submitted`` → approve → ``approved`` (a new revision;
``pending → approved``) | request changes → ``changes_requested``
(``pending → changes_requested``) | staff correction → ``submitted`` (the
values change; still ``pending``); ``changes_requested`` → the tutor's
``PUT`` → ``submitted`` (``resubmit_approval_request``); ``approved`` →
the tutor's ``PUT`` with a reason → ``submitted`` (the current revision
stays in force; the request is reopened with ``open_approval_request``) |
staff correction → ``approved`` (a new superseding revision; the request
stays ``approved``). A staff correction while ``changes_requested`` is
``409 closeout_with_tutor``. There is no reject and no tutor withdraw.

**Reviewer order** (:func:`approve_closeout`, :func:`request_closeout_changes`,
:func:`correct_closeout`; the route adds ``401``, ``403 permission_denied``
and the body's ``422`` first): the closeout ``FOR UPDATE`` (``404`` for an
unknown session or one without a closeout, the same body) → the session
(``FOR SHARE`` to approve or send back, ``FOR UPDATE`` to correct) → ``403
closeout_self_review`` (the submitter, unless they hold the Owner role, E7)
→ ``409 version_conflict`` (the closeout's version) → the status check
(``409 closeout_not_submitted`` / ``closeout_with_tutor``) → the writes →
the approval request (``administration.service.decide_approval_request``,
which the handler accepts because the closeout already has the target
status) → the named audit event → :func:`~app.workflows.closeout_approval.
on_closeout_approved` for a new revision → the tutor's notification.

**Tutor order** (:func:`resubmit_closeout`, ``PUT``): ``404`` (the #77
record policy, or no closeout) → ``403 closeout_tutor_only`` (visible staff,
or a current tutor who didn't submit it) → the closeout ``FOR UPDATE`` →
the session ``FOR UPDATE`` → ``409 version_conflict`` → ``409
closeout_under_review`` → ``422 reason_required`` (from ``approved``) →
``409 closeout_attendance_incomplete`` → ``422 summary_required`` → ``422
closeout_unchanged`` (from ``approved`` with identical minutes and
summary) → the writes → the approval request → ``academics.
closeout_resubmitted``. No notification (the inbox lists it again).

**Lock order:** closeout → session → approval request, for every writer
here. #77's submit takes the session first, but no closeout exists before
it; #76's attendance writers take only the session row, and the E6 unlock
predicate reads the closeout without a lock after it.
"""

import uuid
from typing import Any, Final, Literal

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import Principal, RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import StaleVersionError
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.offer_records import rfc3339
from app.workflows.closeout_approval import on_closeout_approved
from app.workflows.session_closeout import (
    SESSION_CLOSEOUT_TYPE,
    get_session_closeout,
)
from app.workflows.session_closeout_schemas import SessionCloseoutView

CLOSEOUT_RECORD_TYPE: Final = "session_closeouts"
APPROVED_EVENT: Final = "academics.closeout_approved"
CHANGES_REQUESTED_EVENT: Final = "academics.closeout_changes_requested"
CORRECTED_EVENT: Final = "academics.closeout_corrected"
RESUBMITTED_EVENT: Final = "academics.closeout_resubmitted"

APPROVED_NOTIFICATION: Final = "academics.closeout_approved"
CHANGES_REQUESTED_NOTIFICATION: Final = "academics.closeout_changes_requested"
CORRECTED_NOTIFICATION: Final = "academics.closeout_corrected"

_MESSAGES: Final = {
    "summary_required": "Fill in this part of the summary.",
    "too_long": "At most 2000 characters.",
    "reason_required": "Give a reason.",
}


# --- Notifications (F1) -----------------------------------------------------------


class CloseoutChangesRequestedParams(BaseModel):
    """``academics.closeout_changes_requested``: no reason, summary or
    student data (the tutor reads the reason on the closeout)."""

    class_session_id: str
    starts_at: str
    subject_name: str


class CloseoutDecisionParams(CloseoutChangesRequestedParams):
    """``academics.closeout_approved`` and ``academics.closeout_corrected``:
    also the minutes now approved (``null`` for a correction of a closeout
    that isn't approved)."""

    approved_minutes: int | None


def register_closeout_notifications() -> None:
    """Register the three types (again: a no-op); ``app.wiring`` calls it."""
    administration.register_notification_type(
        APPROVED_NOTIFICATION, CloseoutDecisionParams
    )
    administration.register_notification_type(
        CORRECTED_NOTIFICATION, CloseoutDecisionParams
    )
    administration.register_notification_type(
        CHANGES_REQUESTED_NOTIFICATION, CloseoutChangesRequestedParams
    )


async def _notify(
    session: AsyncSession,
    closeout: academics.SessionCloseout,
    locked: scheduling.ClosingSession,
    notification_type: str,
    *,
    approved_minutes: int | None = None,
) -> None:
    """One notification to the submitting tutor (in any account state)."""
    subject = (await catalog.subject_references(session, [locked.subject_id])).get(
        locked.subject_id
    )
    params: dict[str, Any] = {
        "class_session_id": str(locked.id),
        "starts_at": rfc3339(locked.starts_at),
        "subject_name": "" if subject is None else subject.name,
    }
    if notification_type != CHANGES_REQUESTED_NOTIFICATION:
        params["approved_minutes"] = approved_minutes
    await administration.create_notification(
        session,
        recipient_user_id=closeout.tutor_user_id,
        notification_type=notification_type,
        params=params,
    )


# --- Shared steps -----------------------------------------------------------------


def _raise_review_block(block: str | None) -> None:
    if block == academics.NOT_SUBMITTED:
        raise academics.CloseoutNotSubmittedError()
    if block == academics.WITH_TUTOR:
        raise academics.CloseoutWithTutorError()
    if block == academics.UNDER_REVIEW:
        raise academics.CloseoutUnderReviewError()


async def _lock_for_review(
    session: AsyncSession,
    principal: Principal,
    class_session_id: uuid.UUID,
    version: int,
    *,
    share: bool,
) -> tuple[academics.SessionCloseout, scheduling.ClosingSession]:
    """Reviewer steps 4–7: the closeout ``FOR UPDATE`` (``404``), the
    session, ``403 closeout_self_review``, ``409 version_conflict``."""
    closeout = await academics.lock_closeout(session, class_session_id)
    if closeout is None:
        raise RecordNotFoundError()
    locked = await scheduling.lock_closeout_session(
        session, class_session_id, share=share
    )
    if academics.is_self_review(principal, closeout.tutor_user_id):
        raise academics.CloseoutSelfReviewError()
    if closeout.version != version:
        raise StaleVersionError()
    return closeout, locked


async def _touch(session: AsyncSession, closeout: academics.SessionCloseout) -> None:
    """Flush an update of the row, so its version goes up by exactly one
    even when only the session's minutes change (``updated_at`` and
    ``version`` are left out of the audit diff)."""
    closeout.updated_at = func.now()
    await session.flush()
    await session.refresh(closeout, ["updated_at"])


async def _request_id(session: AsyncSession, closeout_id: uuid.UUID) -> uuid.UUID:
    request_id = await administration.approval_request_id_for_subject(
        session, request_type=SESSION_CLOSEOUT_TYPE, subject_id=closeout_id
    )
    if request_id is None:  # pragma: no cover - #77 creates it with the closeout
        raise RecordNotFoundError()
    return request_id


def _summary_field_errors(
    summary: dict[str, str | None], *, all_absent: bool
) -> list[FieldError]:
    return [
        {
            "path": ["body", "summary", field],
            "code": code,
            "message": _MESSAGES[code],
        }
        for field, code in academics.summary_errors(summary, all_absent=all_absent)
    ]


async def _new_revision(
    session: AsyncSession,
    closeout: academics.SessionCloseout,
    locked: scheduling.ClosingSession,
    *,
    source: Literal["review", "staff_correction"],
    approved_minutes: int,
    statuses: dict[uuid.UUID, str],
    principal: Principal,
    changes: dict[str, str | None] | None = None,
) -> academics.SessionCloseoutApproval:
    approval = await academics.add_approval(
        session,
        closeout,
        source=source,
        snapshot=academics.ApprovalSnapshot(
            class_session_id=locked.id,
            tutor_user_id=closeout.tutor_user_id,
            session_starts_at=locked.starts_at,
            session_kind=locked.kind,
            scheduled_minutes=locked.scheduled_minutes,
            approved_minutes=approved_minutes,
            attendance=statuses,
        ),
        approved_by_user_id=principal.user_id,
        changes=changes,
    )
    await on_closeout_approved(
        session,
        approval_id=approval.id,
        supersedes_approval_id=approval.supersedes_approval_id,
    )
    return approval


# --- C1: approve ------------------------------------------------------------------


async def approve_closeout(
    session: AsyncSession,
    *,
    principal: Principal,
    class_session_id: uuid.UUID,
    body: academics.CloseoutApprove,
) -> SessionCloseoutView:
    """``POST …/closeout/approve`` (C1): revision ``n + 1`` with the
    session's actual minutes and the live attendance."""
    closeout, locked = await _lock_for_review(
        session, principal, class_session_id, body.version, share=True
    )
    _raise_review_block(academics.review_block(closeout.status, "approve"))
    _, statuses = await scheduling.closeout_attendance(session, locked.id)
    approval = await _new_revision(
        session,
        closeout,
        locked,
        source="review",
        approved_minutes=0 if locked.actual_minutes is None else locked.actual_minutes,
        statuses=dict(statuses),
        principal=principal,
    )
    await administration.decide_approval_request(
        session,
        principal=principal,
        request_id=await _request_id(session, closeout.id),
        action="approve",
    )
    record_audit_event(
        session,
        action=APPROVED_EVENT,
        record_type=CLOSEOUT_RECORD_TYPE,
        record_id=closeout.id,
        details={
            "approval_id": approval.id,
            "revision": approval.revision,
            "approved_minutes": approval.approved_minutes,
            "supersedes_approval_id": approval.supersedes_approval_id,
            "source": approval.source,
        },
    )
    await _notify(
        session,
        closeout,
        locked,
        APPROVED_NOTIFICATION,
        approved_minutes=approval.approved_minutes,
    )
    return await get_session_closeout(session, principal, locked.id)


# --- C2: request changes ------------------------------------------------------------


async def request_closeout_changes(
    session: AsyncSession,
    *,
    principal: Principal,
    class_session_id: uuid.UUID,
    body: academics.CloseoutRequestChanges,
) -> SessionCloseoutView:
    """``POST …/closeout/request-changes`` (C2): back to the tutor with the
    reason; the session stays ``completed`` and the tutor may mark
    attendance again (E6)."""
    closeout, locked = await _lock_for_review(
        session, principal, class_session_id, body.version, share=True
    )
    _raise_review_block(academics.review_block(closeout.status, "request_changes"))
    closeout.status = academics.CHANGES_REQUESTED
    closeout.review_reason = body.reason
    await session.flush()
    await administration.decide_approval_request(
        session,
        principal=principal,
        request_id=await _request_id(session, closeout.id),
        action="request_changes",
        reason=body.reason,
    )
    record_audit_event(
        session,
        action=CHANGES_REQUESTED_EVENT,
        record_type=CLOSEOUT_RECORD_TYPE,
        record_id=closeout.id,
        details={"reason": body.reason},
    )
    await _notify(session, closeout, locked, CHANGES_REQUESTED_NOTIFICATION)
    return await get_session_closeout(session, principal, locked.id)


# --- C3: staff correction -----------------------------------------------------------


def _current_values(
    closeout: academics.SessionCloseout, locked: scheduling.ClosingSession
) -> dict[str, int | str | None]:
    return {
        "actual_minutes": locked.actual_minutes,
        "topics": closeout.topics,
        "performance": closeout.performance,
        "homework": closeout.homework,
        "next_steps": closeout.next_steps,
    }


def _summary_part(values: dict[str, int | str | None]) -> dict[str, str | None]:
    return {
        field: value if isinstance(value, str) else None
        for field, value in values.items()
        if field != "actual_minutes"
    }


async def correct_closeout(
    session: AsyncSession,
    *,
    principal: Principal,
    class_session_id: uuid.UUID,
    body: academics.CloseoutCorrection,
) -> SessionCloseoutView:
    """``POST …/closeout/corrections`` (C3; E5): only the given members
    change; on an approved closeout the correction is a new revision."""
    closeout, locked = await _lock_for_review(
        session, principal, class_session_id, body.version, share=False
    )
    _raise_review_block(academics.review_block(closeout.status, "correct"))
    proposed: dict[str, int | str | None] = {}
    if body.actual_minutes is not None:
        proposed["actual_minutes"] = body.actual_minutes
    if body.summary is not None:
        proposed |= body.summary.model_dump(exclude_unset=True)
    current = _current_values(closeout, locked)
    result = current | proposed
    participants, statuses = await scheduling.closeout_attendance(session, locked.id)
    errors = _summary_field_errors(
        _summary_part(result),
        all_absent=academics.all_marked_absent(statuses, len(participants)),
    )
    if errors:
        raise academics.CloseoutSummaryInvalidError(errors)
    fields = academics.corrected_fields(current, proposed)
    was_approved = closeout.status == academics.APPROVED
    reapproval = False
    if was_approved and not fields:
        approval = await academics.current_approval(session, closeout.id)
        reapproval = approval is not None and academics.attendance_changed(
            approval.attendance, statuses
        )
    if not fields and not reapproval:
        raise academics.CloseoutUnchangedError()

    changes = {
        field: _summary_part(result)[field]
        for field in fields
        if field != "actual_minutes"
    }
    minutes_changed = "actual_minutes" in fields
    new_minutes = result["actual_minutes"]
    assert new_minutes is None or isinstance(new_minutes, int)  # noqa: S101
    if minutes_changed and new_minutes is not None:
        await scheduling.set_actual_minutes(
            session, class_session_id=locked.id, actual_minutes=new_minutes
        )
    # One update of the closeout (its version goes up by exactly one).
    approval_row = None
    if was_approved:
        approval_row = await _new_revision(
            session,
            closeout,
            locked,
            source="staff_correction",
            approved_minutes=0 if new_minutes is None else new_minutes,
            statuses=dict(statuses),
            principal=principal,
            changes=changes,
        )
    else:
        for field, value in changes.items():
            setattr(closeout, field, value)
        await _touch(session, closeout)
    correction = await academics.add_correction(
        session,
        closeout,
        fields=fields,
        previous_minutes=locked.actual_minutes if minutes_changed else None,
        new_minutes=new_minutes if minutes_changed else None,
        reason=body.reason,
        closeout_status=academics.APPROVED if was_approved else academics.SUBMITTED,
        approval_id=None if approval_row is None else approval_row.id,
        corrected_by_user_id=principal.user_id,
    )
    record_audit_event(
        session,
        action=CORRECTED_EVENT,
        record_type=CLOSEOUT_RECORD_TYPE,
        record_id=closeout.id,
        details={
            "correction_id": correction.id,
            "fields": list(fields),
            "previous_minutes": correction.previous_minutes,
            "new_minutes": correction.new_minutes,
            "reason": body.reason,
            "approval_id": correction.approval_id,
        },
    )
    await _notify(
        session,
        closeout,
        locked,
        CORRECTED_NOTIFICATION,
        approved_minutes=None
        if approval_row is None
        else approval_row.approved_minutes,
    )
    return await get_session_closeout(session, principal, locked.id)


# --- D: the tutor's resubmission or correction request ------------------------------


def _ensure_submitter(
    principal: Principal,
    tutor_user_id: uuid.UUID | None,
    closeout: academics.SessionCloseout | None,
) -> None:
    """``404`` (the #77 record policy, or no closeout) or ``403
    closeout_tutor_only`` (visible staff, or a current tutor who didn't
    submit it)."""
    access = academics.closeout_access(principal, tutor_user_id)
    if access is academics.CloseoutAccess.HIDDEN or closeout is None:
        raise RecordNotFoundError()
    if (
        access is academics.CloseoutAccess.STAFF
        or closeout.tutor_user_id != principal.user_id
    ):
        raise academics.CloseoutTutorOnlyError()


async def resubmit_closeout(
    session: AsyncSession,
    *,
    principal: Principal,
    class_session_id: uuid.UUID,
    body: academics.CloseoutResubmit,
) -> SessionCloseoutView:
    """``PUT …/closeout`` (D2; the order is in the module docstring)."""
    context = await scheduling.closeout_session_context(session, class_session_id)
    if context is None:
        raise RecordNotFoundError()
    _ensure_submitter(
        principal,
        context.tutor_user_id,
        await academics.closeout_for_session(session, context.id),
    )
    closeout = await academics.lock_closeout(session, context.id)
    locked = await scheduling.lock_closeout_session(session, context.id)
    _ensure_submitter(principal, locked.tutor_user_id, closeout)
    assert closeout is not None  # noqa: S101 (checked above)
    if closeout.version != body.version:
        raise StaleVersionError()
    _raise_review_block(academics.review_block(closeout.status, "tutor_update"))
    from_status = closeout.status
    if from_status == academics.APPROVED and body.reason is None:
        raise academics.CloseoutSummaryInvalidError(
            [
                {
                    "path": ["body", "reason"],
                    "code": "reason_required",
                    "message": _MESSAGES["reason_required"],
                }
            ]
        )
    participants, statuses = await scheduling.closeout_attendance(session, locked.id)
    missing = [student for student in participants if student not in statuses]
    if missing:
        raise scheduling.AttendanceIncompleteError(missing)
    summary = body.summary.model_dump()
    errors = _summary_field_errors(
        summary, all_absent=academics.all_marked_absent(statuses, len(participants))
    )
    if errors:
        raise academics.CloseoutSummaryInvalidError(errors)
    proposed: dict[str, int | str | None] = {
        "actual_minutes": body.actual_minutes,
        **summary,
    }
    if from_status == academics.APPROVED and not academics.corrected_fields(
        _current_values(closeout, locked), proposed
    ):
        raise academics.CloseoutUnchangedError()

    for field, value in summary.items():
        setattr(closeout, field, value)
    closeout.tutor_note = body.reason
    closeout.last_submitted_at = clock.now()
    closeout.status = academics.SUBMITTED
    await session.flush()
    await scheduling.set_actual_minutes(
        session, class_session_id=locked.id, actual_minutes=body.actual_minutes
    )
    if from_status == academics.CHANGES_REQUESTED:
        await administration.resubmit_approval_request(
            session,
            request_id=await _request_id(session, closeout.id),
            by_user_id=principal.user_id,
        )
    else:
        await administration.open_approval_request(
            session,
            request_type=SESSION_CLOSEOUT_TYPE,
            subject_id=closeout.id,
            requester_user_id=principal.user_id,
        )
    record_audit_event(
        session,
        action=RESUBMITTED_EVENT,
        record_type=CLOSEOUT_RECORD_TYPE,
        record_id=closeout.id,
        details={"from_status": from_status, "reason": body.reason},
    )
    return await get_session_closeout(session, principal, locked.id)
