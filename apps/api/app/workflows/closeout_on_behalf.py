"""Overdue closeouts and closeout on behalf of a tutor (#301 M5-10b; spec
§2 "the Coordinator verifies hours", §3 "admins reapprove", §7, §16;
architecture §4.1, §4.2, ISSUE-F2, ISSUE-F7; ADR-0005 D2).

Some classes end without a closeout: the tutor left, lost the role or never
submitted. Academic reviewers (``teaching_hours.approve``: Owner,
Administrator, Academic Coordinator) list them, close one out on the
tutor's behalf with a reason, and take over a closeout sent back to a tutor
who can't act. The closeout stays the **tutor's** (``tutor_user_id`` =
the session's current tutor, the approval request's requester too), so
payroll attribution (M7-04) and the #78 review don't change; the reviewer
and the reason are recorded on the closeout (``on_behalf_by_user_id``,
``on_behalf_reason``: the latest submission) and in a named audit event.
The routes are in :mod:`app.workflows.session_closeout_router` and
:mod:`app.workflows.overdue_closeouts_router`.

**Overdue list** (:func:`list_overdue_closeouts`): ``confirmed`` sessions
with ``ends_at <= now`` (a confirmed session never has a closeout:
submitting one completes it), oldest first; optional tutor and
center-local start dates. At most 5 statements whatever the page.

**Submit on behalf** (:func:`submit_closeout_on_behalf`; the route adds
``401``, ``403 permission_denied`` and the body's ``422`` first): the
session ``FOR UPDATE`` (``404`` unknown) → ``409 version_conflict``
(the session's version) → ``403 closeout_on_behalf_own_session``, ``409
closeout_already_submitted``, ``session_not_closable`` (``details.state``)
or ``session_not_ended`` → the #77 steps (``409
closeout_attendance_incomplete``; ``422 summary_required``; the closeout of
the tutor with the marker; the approval request) → the audit event (``mode
"submit"``) → the tutor's notification → ``201``.

**Take over** (:func:`take_over_closeout`, ``PUT``): ``404`` (unknown
session or no closeout) → the closeout ``FOR UPDATE`` → the session ``FOR
UPDATE`` → ``403 closeout_on_behalf_own_session`` → ``409
version_conflict`` (the closeout's version) → ``409
closeout_not_with_tutor`` (not ``changes_requested``) → ``409
closeout_attendance_incomplete`` → ``422 summary_required`` → the writes
(``submitted``, the marker, no tutor note) → the core
``resubmit_approval_request`` → the audit event (``mode "takeover"``) →
the tutor's notification → ``200``. An unchanged body is allowed (taking
ownership is the change). ``current_approval_id`` is never touched.

**Lock order:** closeout → session → approval request, as #78; the submit
locks only the session (no closeout exists before it).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from datetime import date
from typing import Any, Final, Literal, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import Principal, RecordNotFoundError
from app.core.errors import AppError
from app.core.models import StaleVersionError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.identity import service as identity
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.eligibility import local_day_bounds
from app.workflows.session_closeout import close_out, get_session_closeout
from app.workflows.session_closeout_review import (
    CLOSEOUT_RECORD_TYPE,
    notify_tutor,
    request_id_of,
    summary_field_errors,
)
from app.workflows.session_closeout_schemas import (
    OverdueCloseoutAttendance,
    OverdueCloseoutItem,
    OverdueCloseoutSubject,
    OverdueCloseoutTutor,
    SessionCloseoutView,
)

ON_BEHALF_EVENT: Final = "academics.closeout_submitted_on_behalf"
ON_BEHALF_NOTIFICATION: Final = "academics.closeout_submitted_on_behalf"
TUTOR_ROLE: Final = "tutor"
ACTIVE: Final = "active"


class RangeInvalidError(AppError):
    """``422 validation_error`` with ``range_invalid`` at ``query.to``: the
    last date is before the first (B1)."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["query", "to"],
                    "code": "range_invalid",
                    "message": "The last date must not be before the first.",
                }
            ]
        )


class CloseoutOnBehalfParams(BaseModel):
    """``academics.closeout_submitted_on_behalf`` (#301 E7): no reason,
    summary or student data."""

    class_session_id: str
    starts_at: str
    subject_name: str


def register_on_behalf_notifications() -> None:
    """Register the type (again: a no-op); ``app.wiring`` calls it."""
    administration.register_notification_type(
        ON_BEHALF_NOTIFICATION, CloseoutOnBehalfParams
    )


async def _record(
    session: AsyncSession,
    closeout: academics.SessionCloseout,
    locked: scheduling.ClosingSession,
    *,
    mode: Literal["submit", "takeover"],
    reason: str,
) -> None:
    """The named audit event (it carries the reason) and the tutor's
    notification, in the caller's Unit of Work."""
    record_audit_event(
        session,
        action=ON_BEHALF_EVENT,
        record_type=CLOSEOUT_RECORD_TYPE,
        record_id=closeout.id,
        details={"class_session_id": locked.id, "mode": mode, "reason": reason},
    )
    await notify_tutor(session, closeout, locked, ON_BEHALF_NOTIFICATION)


def _raise_on_behalf_block(block: academics.OnBehalfBlock, state: str) -> None:
    if block == academics.ON_BEHALF_OWN_SESSION:
        raise academics.CloseoutOnBehalfOwnSessionError()
    if block == academics.ALREADY_SUBMITTED:
        raise academics.CloseoutAlreadySubmittedError()
    if block == academics.NOT_CLOSABLE:
        raise academics.SessionNotClosableError(state)
    raise academics.SessionNotEndedError()


# --- C: submit on behalf ------------------------------------------------------------


async def submit_closeout_on_behalf(
    session: AsyncSession,
    *,
    principal: Principal,
    class_session_id: uuid.UUID,
    body: academics.CloseoutOnBehalfSubmit,
) -> SessionCloseoutView:
    """``POST …/closeout/on-behalf`` (C2; the order is in the module
    docstring)."""
    locked = await scheduling.lock_closeout_session(session, class_session_id)
    if locked.version != body.version:
        raise StaleVersionError()
    existing = await academics.closeout_for_session(session, locked.id)
    block = academics.on_behalf_block(
        locked.state,
        locked.ends_at,
        clock.now(),
        has_closeout=existing is not None,
        caller_is_tutor=principal.user_id == locked.tutor_user_id,
    )
    if block is not None:
        _raise_on_behalf_block(block, locked.state)
    assert locked.tutor_user_id is not None  # noqa: S101 (a confirmed row has one)
    closeout = await close_out(
        session,
        locked=locked,
        version=body.version,
        actual_minutes=body.actual_minutes,
        summary=body.summary,
        tutor_user_id=locked.tutor_user_id,
        on_behalf_by_user_id=principal.user_id,
        on_behalf_reason=body.reason,
    )
    await _record(session, closeout, locked, mode="submit", reason=body.reason)
    return await get_session_closeout(session, principal, locked.id)


# --- D: take over -------------------------------------------------------------------


async def take_over_closeout(
    session: AsyncSession,
    *,
    principal: Principal,
    class_session_id: uuid.UUID,
    body: academics.CloseoutTakeOver,
) -> SessionCloseoutView:
    """``PUT …/closeout/on-behalf`` (D2; the order is in the module
    docstring)."""
    closeout = await academics.lock_closeout(session, class_session_id)
    if closeout is None:
        raise RecordNotFoundError()
    locked = await scheduling.lock_closeout_session(session, class_session_id)
    if principal.user_id == closeout.tutor_user_id:
        raise academics.CloseoutOnBehalfOwnSessionError()
    if closeout.version != body.version:
        raise StaleVersionError()
    if closeout.status != academics.CHANGES_REQUESTED:
        raise academics.CloseoutNotWithTutorError()
    participants, statuses = await scheduling.closeout_attendance(session, locked.id)
    missing = [student for student in participants if student not in statuses]
    if missing:
        raise scheduling.AttendanceIncompleteError(missing)
    summary = body.summary.model_dump()
    errors = summary_field_errors(
        summary, all_absent=academics.all_marked_absent(statuses, len(participants))
    )
    if errors:
        raise academics.CloseoutSummaryInvalidError(errors)

    for field, value in summary.items():
        setattr(closeout, field, value)
    closeout.tutor_note = None
    closeout.last_submitted_at = clock.now()
    closeout.status = academics.SUBMITTED
    closeout.on_behalf_by_user_id = principal.user_id
    closeout.on_behalf_reason = body.reason
    await session.flush()
    await scheduling.set_actual_minutes(
        session, class_session_id=locked.id, actual_minutes=body.actual_minutes
    )
    await administration.resubmit_approval_request(
        session,
        request_id=await request_id_of(session, closeout.id),
        by_user_id=principal.user_id,
    )
    await _record(session, closeout, locked, mode="takeover", reason=body.reason)
    return await get_session_closeout(session, principal, locked.id)


# --- B: the overdue list ------------------------------------------------------------


async def list_overdue_closeouts(
    session: AsyncSession,
    *,
    page: OffsetParams,
    tutor_user_id: uuid.UUID | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> OffsetPage[OverdueCloseoutItem]:
    """``GET /api/v1/closeouts/overdue`` (B1–B4): ``from`` / ``to`` are
    center-local dates on ``starts_at`` (inclusive; ``to`` before ``from``
    is ``422 range_invalid``, no maximum span). At most 5 statements: the
    zone (only with a date), the total, the page, the subjects, the
    tutors."""
    if from_date is not None and to_date is not None and to_date < from_date:
        raise RangeInvalidError()
    starts_from = starts_before = None
    if from_date is not None or to_date is not None:
        zone = await administration.get_center_time_zone(session)
        if from_date is not None:
            starts_from = local_day_bounds(from_date, zone)[0]
        if to_date is not None:
            starts_before = local_day_bounds(to_date, zone)[1]
    rows, total = await scheduling.sessions_awaiting_closeout(
        session,
        now=clock.now(),
        tutor_user_id=tutor_user_id,
        starts_from=starts_from,
        starts_before=starts_before,
        limit=page.limit,
        offset=page.offset,
    )
    subjects = await catalog.subject_references(
        session, {row.subject_id for row in rows}
    )
    tutors = await identity.get_account_references(
        session, {row.tutor_user_id for row in rows if row.tutor_user_id is not None}
    )

    def tutor_of(user_id: uuid.UUID | None) -> OverdueCloseoutTutor:
        account = None if user_id is None else tutors.get(user_id)
        return OverdueCloseoutTutor(
            user_id=cast(uuid.UUID, user_id),
            display_name="" if account is None else account.display_name,
            can_submit=account is not None
            and account.state == ACTIVE
            and TUTOR_ROLE in account.roles,
        )

    items: list[OverdueCloseoutItem] = []
    for row in rows:
        subject = subjects.get(row.subject_id)
        items.append(
            OverdueCloseoutItem(
                class_session_id=row.id,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
                kind=cast(Any, row.kind),
                scheduled_minutes=row.scheduled_minutes,
                subject=OverdueCloseoutSubject(
                    name="" if subject is None else subject.name,
                    education_level_name=""
                    if subject is None
                    else subject.education_level_name,
                ),
                tutor=tutor_of(row.tutor_user_id),
                participant_count=row.participant_count,
                attendance=OverdueCloseoutAttendance(
                    marked=row.marked_count, total=row.participant_count
                ),
            )
        )
    return OffsetPage[OverdueCloseoutItem](
        items=items, total=total, limit=page.limit, offset=page.offset
    )
