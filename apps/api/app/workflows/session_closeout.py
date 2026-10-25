"""Guided session closeout (#77 M5-10; spec §2, §3 "Attendance, session
completion and changes", §7, W3.1–2; architecture §4.1, §4.2, ISSUE-F2,
ISSUE-F7; ADR-0005 D2).

After a class starts its **current** tutor submits the closeout: every
participant's attendance (#76 marks), the actual teaching minutes and a
structured summary. Submission completes the session (``confirmed →
completed``, ``actual_minutes`` stored on ``class_sessions``), writes the
``academics`` closeout row (``submitted``) and creates a pending
``academics.session_closeout`` item in the approval inbox, in one Unit of
Work. Submitted hours are **not approved** (#78 reviews them in
:mod:`app.workflows.session_closeout_review`; payroll, M7-04, reads only
approved closeouts). No notification is sent.

The routes are in :mod:`app.workflows.session_closeout_router`.

**Read** (:func:`get_session_closeout`): the record policy
``academics.service.closeout_access`` (the current tutor, or a
``schedule.view`` holder; anyone else the ``404`` of an unknown id, in any
state). The summary text only for the tutor and ``teaching_hours.approve``
holders (E5), also the review reasons (#78 E1). At most 8 statements
(#78 E2: the closeout's revisions and corrections in two).

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
hold ``teaching_hours.approve``; ``inbox_decisions = False``; the generic
approve, reject and request changes answer ``409
closeout_review_route_required`` whatever the request's status
(``inbox_route_error``, #312); the actions keep the same check for internal
calls (see the handler). ``summaries``: the session facts in three
statements, no summary text, student names or attendance.
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
    CloseoutApprovalRevision,
    CloseoutAttendance,
    CloseoutBlockCode,
    CloseoutCorrectionItem,
    CloseoutPerson,
    CloseoutReview,
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
    """``GET /api/v1/class-sessions/{id}/closeout`` (#77 C1, C2; #78 E1, E2:
    at most 8 statements whatever the participants, revisions and
    corrections)."""
    context = await scheduling.closeout_session_context(session, class_session_id)
    access = _ensure_visible(
        principal,
        context is not None,
        None if context is None else context.tutor_user_id,
    )
    assert context is not None  # noqa: S101 (checked above)
    closeout = await academics.closeout_for_session(session, context.id)
    history = (
        None
        if closeout is None
        else await academics.closeout_history(session, closeout.id)
    )
    user_ids = {context.tutor_user_id}
    if closeout is not None and history is not None:
        user_ids.add(closeout.tutor_user_id)
        user_ids |= {item.approved_by_user_id for item in history.revisions}
        user_ids |= {item.corrected_by_user_id for item in history.corrections}
    names = await identity.get_account_references(
        session, {user_id for user_id in user_ids if user_id is not None}
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

    def named(user_id: uuid.UUID) -> CloseoutPerson:
        return person(user_id) or CloseoutPerson(display_name="")

    is_tutor = access is academics.CloseoutAccess.TUTOR
    state_block = academics.closeout_block(
        context.state,
        context.starts_at,
        clock.now(),
        has_closeout=closeout is not None,
    )
    block: CloseoutBlockCode | None = state_block if is_tutor else academics.TUTOR_ONLY
    shows_text = academics.sees_summary(principal, context.tutor_user_id)
    record = None
    can_review = can_correct = can_resubmit = can_request_correction = False
    if closeout is not None and history is not None:
        revisions = [
            CloseoutApprovalRevision(
                id=item.id,
                revision=item.revision,
                approved_minutes=item.approved_minutes,
                approved_by=named(item.approved_by_user_id),
                approved_at=item.approved_at,
                source=cast(Any, item.source),
            )
            for item in history.revisions
        ]
        current = next(
            (
                (view, item)
                for view, item in zip(revisions, history.revisions, strict=True)
                if item.id == closeout.current_approval_id
            ),
            None,
        )
        record = SessionCloseoutRecord(
            id=closeout.id,
            version=closeout.version,
            status=cast(Any, closeout.status),
            actual_minutes=context.actual_minutes,
            summary=academics.summary_of(closeout) if shows_text else None,
            summary_hidden=not shows_text,
            submitted_by=named(closeout.tutor_user_id),
            submitted_at=closeout.submitted_at,
            approval_request_id=approval_request_id,
            review=CloseoutReview(
                status=cast(Any, closeout.status),
                reason=closeout.review_reason if shows_text else None,
                last_submitted_at=closeout.last_submitted_at,
                tutor_note=closeout.tutor_note if shows_text else None,
            ),
            approval=None if current is None else current[0],
            revisions=revisions,
            corrections=[
                CloseoutCorrectionItem(
                    id=item.id,
                    fields=cast(Any, list(item.fields)),
                    previous_minutes=item.previous_minutes,
                    new_minutes=item.new_minutes,
                    reason=item.reason if shows_text else None,
                    corrected_by=named(item.corrected_by_user_id),
                    created_at=item.created_at,
                    approval_id=item.approval_id,
                )
                for item in history.corrections
            ],
            attendance_changed_since_approval=current is not None
            and academics.attendance_changed(current[1].attendance, context.statuses),
        )
        reviewer = principal.has(
            academics.TEACHING_HOURS_APPROVE
        ) and not academics.is_self_review(principal, closeout.tutor_user_id)
        submitter = is_tutor and closeout.tutor_user_id == principal.user_id
        can_review = reviewer and closeout.status == academics.SUBMITTED
        can_correct = reviewer and closeout.status in (
            academics.SUBMITTED,
            academics.APPROVED,
        )
        can_resubmit = submitter and closeout.status == academics.CHANGES_REQUESTED
        can_request_correction = submitter and closeout.status == academics.APPROVED
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
        can_review=can_review,
        can_correct=can_correct,
        can_resubmit=can_resubmit,
        can_request_correction=can_request_correction,
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


def review_path(class_session_id: uuid.UUID) -> str:
    """The staff session page where a closeout is reviewed (#78 E2)."""
    return f"/schedule/sessions/{class_session_id}"


class SessionCloseoutApprovalHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    session closeouts; the subject is the closeout (E1 of #77).

    **Decisions go through the closeout routes (#78 E2, E3):** reviewers
    approve or request changes on the staff session page
    (``app.workflows.session_closeout_review``), which moves the closeout
    first and then resolves this item through
    ``administration.service.decide_approval_request`` in the same Unit of
    Work. So ``approve`` accepts only a closeout already ``approved`` and
    ``request_changes`` only one already ``changes_requested``; otherwise,
    and always for ``reject`` (closeouts have no reject), they raise ``409
    closeout_review_route_required`` with ``details.review_path`` and the
    item stays as it was. The closeout is read without a lock (the review
    routes hold it; the generic routes fail before touching it).

    **Generic inbox routes (#312):** ``inbox_route_error`` returns that same
    error for every request status, so the core answers it right after its
    ``404`` check (``via_inbox``), before its own status checks. The action
    guards above stay as defence in depth for internal calls."""

    request_type: Final = SESSION_CLOSEOUT_TYPE
    review_permission: Final = academics.TEACHING_HOURS_APPROVE
    supports_request_changes: Final = True
    reason_required_on_reject: Final = True
    inbox_decisions: Final = False
    """E2: the inbox lists no decisions, only the "Review" link."""

    async def _route_error(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        status: str | None,
    ) -> academics.CloseoutReviewRouteRequiredError | None:
        closeout = await academics.closeouts_by_id(
            session, [request.subject_id], fresh=True
        )
        found = closeout.get(request.subject_id)
        if found is None or status is None or found.status != status:
            return academics.CloseoutReviewRouteRequiredError(
                review_path(found.class_session_id)
                if found is not None
                else "/approvals"
            )
        return None

    async def _require(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        status: str | None,
    ) -> None:
        error = await self._route_error(session, request, status)
        if error is not None:
            raise error

    async def inbox_route_error(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
    ) -> academics.CloseoutReviewRouteRequiredError:
        """#312: the generic routes' answer for any request status (the
        closeout read without a lock; ``/approvals`` when it's missing)."""
        error = await self._route_error(session, request, None)
        assert error is not None  # noqa: S101  (status None always refuses)
        return error

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        await self._require(session, request, academics.APPROVED)

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        await self._require(session, request, None)

    async def request_changes(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str,
    ) -> None:
        await self._require(session, request, academics.CHANGES_REQUESTED)

    async def summaries(
        self, session: AsyncSession, subject_ids: Iterable[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any]]:
        """``{class_session_id, starts_at, subject_name, tutor_display_name,
        kind, scheduled_minutes, actual_minutes, participant_count,
        kind_of_review, approved_minutes}`` by closeout id, in three
        statements (#78 C4: ``kind_of_review`` is ``first``,
        ``resubmission`` or ``correction``; ``approved_minutes`` the current
        approval revision's, or ``null``)."""
        ids = set(subject_ids)
        if not ids:
            return {}
        return await scheduling.closeout_session_summaries(
            session, academics.closeout_session_keys(ids)
        )


SESSION_CLOSEOUT_HANDLER: Final = SessionCloseoutApprovalHandler()
