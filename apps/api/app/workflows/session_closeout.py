"""Guided session closeout (#77 M5-10; spec §2, §3 "Attendance, session
completion and changes", §7, W3.1–2; architecture §4.1, §4.2, ISSUE-F2,
ISSUE-F7; ADR-0005 D2).

After a class starts its **current** tutor submits the closeout: every
participant's attendance (#76 marks), the actual teaching minutes and a
structured summary. Submission completes the session (``confirmed →
completed``, ``actual_minutes`` stored on ``class_sessions``), writes the
``academics`` closeout row (``submitted``) and creates a pending
``academics.session_closeout`` item in the approval inbox, in one Unit of
Work. Submitted hours are **not approved** (#78 reviews them; payroll,
M7-04, reads only approved closeouts). No notification is sent.

The routes are in :mod:`app.workflows.session_closeout_router`.

**Read** (:func:`get_session_closeout`): the record policy
``academics.service.closeout_access`` (the current tutor, or a
``schedule.view`` holder; anyone else the ``404`` of an unknown id, in any
state). The summary text only for the tutor and ``teaching_hours.approve``
holders (E5). At most 6 statements.

**Submit** (:func:`submit_session_closeout`; the route adds ``401`` and the
body's ``422`` first), in this order:

1. the session (unlocked) → ``404`` unknown or hidden;
2. ``403 closeout_tutor_only`` for a visible caller who isn't the current
   tutor;
3. the session ``FOR UPDATE`` (the only lock); the policy again on the
   locked row;
4. ``409 version_conflict``;
5. ``409 closeout_already_submitted``, ``session_not_closable``
   (``details.state``), ``session_not_started``;
6. ``scheduling.service.complete_class_session``: ``409
   closeout_attendance_incomplete`` (``details.missing_student_ids``), else
   the session ``completed`` with ``actual_minutes`` (version + 1);
7. ``422 validation_error`` with ``summary_required`` at
   ``body.summary.<field>`` unless every participant is ``absent``: the
   Unit of Work rolls the transition back, nothing changes;
8. the closeout row, then the approval request (requester = the tutor).

**Lock order:** closeout → session → approval request for the closeout
paths (#78). The submit locks only the session; the closeout and the
request don't exist before it.

The ``academics.session_closeout`` approval handler
(:data:`SESSION_CLOSEOUT_HANDLER`, registered by ``app.wiring``): reviewers
hold ``teaching_hours.approve``; ``inbox_decisions = False``, and approve,
reject and request changes answer ``409 closeout_review_unavailable`` (the
item stays ``pending``) until #78 replaces them. ``summaries``: the session
facts in three statements, no summary text, student names or attendance.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal, RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import StaleVersionError
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.workflows.session_closeout_schemas import (
    CloseoutAttendance,
    CloseoutBlockCode,
    CloseoutPerson,
    CloseoutSessionSubject,
    SessionCloseoutRecord,
    SessionCloseoutSubmit,
    SessionCloseoutView,
)

SESSION_CLOSEOUT_TYPE: Final = "academics.session_closeout"
"""The approval type (subject = the closeout id, requester = the tutor)."""

_MESSAGES: Final = {
    "summary_required": "Fill in this part of the summary.",
    "too_long": "At most 2000 characters.",
}


def _ensure_visible(
    principal: Principal, found: bool, tutor_user_id: uuid.UUID | None
) -> academics.CloseoutAccess:
    access = academics.closeout_access(principal, tutor_user_id)
    if not found or access is academics.CloseoutAccess.HIDDEN:
        raise RecordNotFoundError()
    return access


def _ensure_tutor(
    principal: Principal, found: bool, tutor_user_id: uuid.UUID | None
) -> None:
    """``404`` (unknown or hidden) or ``403 closeout_tutor_only``."""
    if _ensure_visible(principal, found, tutor_user_id) is (
        academics.CloseoutAccess.STAFF
    ):
        raise academics.CloseoutTutorOnlyError()


def _raise_block(block: academics.CloseoutBlock, state: str) -> None:
    if block == academics.ALREADY_SUBMITTED:
        raise academics.CloseoutAlreadySubmittedError()
    if block == academics.NOT_CLOSABLE:
        raise academics.SessionNotClosableError(state)
    if block == academics.NOT_STARTED:
        raise academics.SessionNotStartedError()


async def get_session_closeout(
    session: AsyncSession, principal: Principal, class_session_id: uuid.UUID
) -> SessionCloseoutView:
    """``GET /api/v1/class-sessions/{id}/closeout`` (C1, C2)."""
    context = await scheduling.closeout_session_context(session, class_session_id)
    access = _ensure_visible(
        principal,
        context is not None,
        None if context is None else context.tutor_user_id,
    )
    assert context is not None  # noqa: S101 (checked above)
    closeout = await academics.closeout_for_session(session, context.id)
    names = await identity.get_account_references(
        session,
        {
            user_id
            for user_id in (
                context.tutor_user_id,
                None if closeout is None else closeout.tutor_user_id,
            )
            if user_id is not None
        },
    )
    approval_request_id = (
        None
        if closeout is None
        else await administration.approval_request_id_for_subject(
            session, request_type=SESSION_CLOSEOUT_TYPE, subject_id=closeout.id
        )
    )

    def person(user_id: uuid.UUID | None) -> CloseoutPerson | None:
        account = None if user_id is None else names.get(user_id)
        return (
            None
            if account is None
            else CloseoutPerson(display_name=account.display_name)
        )

    is_tutor = access is academics.CloseoutAccess.TUTOR
    state_block = academics.closeout_block(
        context.state,
        context.starts_at,
        clock.now(),
        has_closeout=closeout is not None,
    )
    block: CloseoutBlockCode | None = state_block if is_tutor else academics.TUTOR_ONLY
    shows_summary = academics.sees_summary(principal, context.tutor_user_id)
    record = None
    if closeout is not None:
        record = SessionCloseoutRecord(
            id=closeout.id,
            status=cast(Any, closeout.status),
            actual_minutes=context.actual_minutes,
            summary=academics.summary_of(closeout) if shows_summary else None,
            summary_hidden=not shows_summary,
            submitted_by=person(closeout.tutor_user_id)
            or CloseoutPerson(display_name=""),
            submitted_at=closeout.submitted_at,
            approval_request_id=approval_request_id,
        )
    missing = context.missing_student_ids
    return SessionCloseoutView(
        class_session_id=context.id,
        version=context.version,
        kind=cast(Any, context.kind),
        state=cast(Any, context.state),
        starts_at=context.starts_at,
        ends_at=context.ends_at,
        scheduled_minutes=context.scheduled_minutes,
        subject=CloseoutSessionSubject(
            id=context.subject.id,
            name=context.subject.name,
            education_level_name=context.subject.education_level_name,
        ),
        tutor=person(context.tutor_user_id),
        can_submit=is_tutor and state_block is None,
        block=block,
        attendance=CloseoutAttendance(
            total=len(context.participant_ids),
            marked=len(context.statuses),
            all_absent=academics.all_marked_absent(
                context.statuses, len(context.participant_ids)
            ),
            missing_student_ids=missing,
        ),
        closeout=record,
    )


async def submit_session_closeout(
    session: AsyncSession,
    *,
    principal: Principal,
    class_session_id: uuid.UUID,
    body: SessionCloseoutSubmit,
) -> SessionCloseoutView:
    """``POST /api/v1/class-sessions/{id}/closeout`` (D2, D3; the order is
    in the module docstring)."""
    context = await scheduling.closeout_session_context(session, class_session_id)
    _ensure_tutor(
        principal,
        context is not None,
        None if context is None else context.tutor_user_id,
    )
    locked = await scheduling.lock_closeout_session(session, class_session_id)
    _ensure_tutor(principal, True, locked.tutor_user_id)
    if locked.version != body.version:
        raise StaleVersionError()
    existing = await academics.closeout_for_session(session, locked.id)
    block = academics.closeout_block(
        locked.state, locked.starts_at, clock.now(), has_closeout=existing is not None
    )
    if block is not None:
        _raise_block(block, locked.state)

    try:
        completed = await scheduling.complete_class_session(
            session,
            class_session_id=locked.id,
            expected_version=body.version,
            actual_minutes=body.actual_minutes,
        )
    except scheduling.SessionNotCompletableError as exc:  # pragma: no cover
        # The block above has checked the state under the same lock.
        raise academics.SessionNotClosableError(exc.state) from exc

    all_absent = academics.all_marked_absent(
        completed.statuses, len(completed.participant_ids)
    )
    errors: list[FieldError] = [
        {
            "path": ["body", "summary", field],
            "code": code,
            "message": _MESSAGES[code],
        }
        for field, code in academics.summary_errors(
            body.summary.model_dump(), all_absent=all_absent
        )
    ]
    if errors:
        raise academics.CloseoutSummaryInvalidError(errors)

    closeout = await academics.create_session_closeout(
        session,
        class_session_id=completed.id,
        tutor_user_id=principal.user_id,
        summary=body.summary,
        submitted_at=clock.now(),
    )
    await administration.create_approval_request(
        session,
        request_type=SESSION_CLOSEOUT_TYPE,
        subject_id=closeout.id,
        requester_user_id=principal.user_id,
    )
    return await get_session_closeout(session, principal, completed.id)


class SessionCloseoutApprovalHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    session closeouts; the subject is the closeout (E1 of #77)."""

    request_type: Final = SESSION_CLOSEOUT_TYPE
    review_permission: Final = academics.TEACHING_HOURS_APPROVE
    supports_request_changes: Final = True
    """#78 implements it."""
    reason_required_on_reject: Final = True
    inbox_decisions: Final = False
    """No decisions until #78: the inbox lists no actions, and the generic
    routes answer ``409 closeout_review_unavailable``."""

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        raise academics.CloseoutReviewUnavailableError()

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        raise academics.CloseoutReviewUnavailableError()

    async def request_changes(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str,
    ) -> None:
        raise academics.CloseoutReviewUnavailableError()

    async def summaries(
        self, session: AsyncSession, subject_ids: Iterable[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any]]:
        """``{class_session_id, starts_at, subject_name, tutor_display_name,
        kind, scheduled_minutes, actual_minutes, participant_count}`` by
        closeout id, in three statements."""
        ids = set(subject_ids)
        if not ids:
            return {}
        return await scheduling.closeout_session_summaries(
            session, academics.closeout_session_keys(ids)
        )


SESSION_CLOSEOUT_HANDLER: Final = SessionCloseoutApprovalHandler()
