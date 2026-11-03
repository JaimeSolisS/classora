"""The payroll workflow and its money lock (#110; spec W3.3-W3.5; ADR-0017 E9,
E10, E12, its #110 notes; data-model §13, §15).

``open → prepared → finalized``, and ``prepared → open`` by a ``return``. This is
a workflow because it composes ``payroll``, ``administration``, ``academics``,
``scheduling`` and ``identity`` (ADR-0002). A finalized period never changes:
the guards of migrations ``0073`` and ``0076`` refuse with ``42501``.

**The lock order** (data-model §15): the idempotency key → the period ``FOR
UPDATE``, re-read → the versions and items ``FOR KEY SHARE`` → the
``approval_requests`` row. Scheduling and academics are read with no lock.

- :func:`prepare`, one transaction: ``404`` → ``409 payroll_period_not_open`` →
  ``409 payroll_period_not_ended`` → ``recalculate``
  (``409 payroll_amount_out_of_range``) → ``blocking_work`` (``409
  payroll_unresolved_work``) → ``409 payroll_negative_total`` → the write. The
  order matters: a line a recalculation would drop keeps its work off the list
  until the run has gone. A refusal rolls the recalculation back (#110 E3), so the
  page then runs ``recalculate``, which commits, and the list agrees with the
  ``409``. ``prepare`` is free of any period order (#110 E2).
- :func:`return_period`: ``404`` → ``409 payroll_period_not_prepared`` → the write.
  Either permission. No reauthentication and no key (#110 E7): it changes no
  money, and a second click gets the ``409``.
- :func:`finalize`: ``404`` → ``409 payroll_period_not_prepared`` → ``403
  payroll_self_finalize`` → ``409 payroll_review_outdated`` (#110 E4) → ``409
  payroll_period_out_of_order`` (#110 E2) → drift, ``409 payroll_period_stale``
  → ``blocking_work`` again, ``409 payroll_unresolved_work`` → the total → the
  write. ``blocking_work`` runs again because work approved after ``prepare``
  with no basis has no draft and no line, so the drift comparison can't see it.

**Oldest first (#110 E2).** An out-of-order finalize pays wrongly when a cohort's
``N`` changes afterwards (833.33 or 1166.67 on 1000.00). Every earlier period
counts, an empty one too: it is finalized at ``0.00``.

**Drift (#110 D3)** compares drafts, never ids, for the kinds a run rebuilds. What
survives a run is outside the comparison and inside the total (#110 D4).

**The review (#110 E4).** Drift can't see a reviewer's page outliving a return and
a new ``prepare``: ``finalize`` takes the ``prepared_at`` that page read. Lines
can't change while a period is ``prepared``, so that one value identifies them.

**No pay in side channels (#110 E6, E10):** no amount and no reason in logs, audit
events, notifications, inbox summaries, error bodies or the stored idempotent
response. The figure stays on the period, and the page refetches it.

**Who reads (#110 E8):** a ``payroll.view`` holder every period, a delegated
Administrator ``prepared`` and ``finalized`` ones. Any other status is the same
``404`` as a missing id on the reads. Neither read takes a lock (#443 D3).

**Statements:** ``finalize`` is fixed whatever the number of lines (#110 M8).
"""

import uuid
from datetime import datetime, timedelta
from typing import Final

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import (
    PermissionDeniedError,
    Principal,
    RecordNotFoundError,
)
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.administration.schemas import AccountName
from app.modules.identity import service as identity
from app.modules.identity.schemas import AccountReference
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import (
    PayrollPeriodFacts,
    PayrollPeriodSummary,
    PayrollPeriodTransition,
    PeriodStatus,
)
from app.modules.scheduling import service as scheduling
from app.workflows import payroll_calculation
from app.workflows.charge_pricing import floor_instant
from app.workflows.payroll_calculation import PERIOD_RECORD_TYPE
from app.workflows.payroll_period_approval import PAYROLL_PERIOD_TYPE
from app.workflows.payroll_workflow_schemas import (
    PayrollPeriodDetail,
    PayrollPeriodItem,
    PayrollPeriodReview,
)

PREPARED_EVENT: Final = "payroll.period_prepared"
RETURNED_EVENT: Final = "payroll.period_returned"
FINALIZED_EVENT: Final = "payroll.period_finalized"
FINALIZED_NOTIFICATION: Final = "payroll.period_finalized"
OPEN: Final = "open"
PREPARED: Final = "prepared"


class PeriodFinalizedParams(BaseModel):
    """``payroll.period_finalized``: no amount."""

    payroll_period_id: str
    starts_on: str
    ends_on: str


def register_payroll_notifications() -> None:
    """Register the type (again: a no-op); ``app.wiring`` calls it."""
    administration.register_notification_type(
        FINALIZED_NOTIFICATION, PeriodFinalizedParams
    )


def ensure_can_list(principal: Principal) -> None:
    """``403`` unless the caller reaches the period reads (#110 F1)."""
    if not payroll.can_list_periods(principal):
        raise PermissionDeniedError()


def ensure_can_return(principal: Principal) -> None:
    """``403`` unless the caller holds either permission (#110 C1)."""
    if not payroll.can_return_period(principal):
        raise PermissionDeniedError()


def _transition(period: PayrollPeriodFacts) -> PayrollPeriodTransition:
    return PayrollPeriodTransition(
        id=period.id,
        status=period.status,
        prepared_at=period.prepared_at,
        finalized_at=period.finalized_at,
    )


def _record(
    session: AsyncSession, period: PayrollPeriodFacts, action: str, line_count: int
) -> None:
    """The named event: ids, the status and a count, no amount and no reason
    (#110 E6)."""
    record_audit_event(
        session,
        action=action,
        record_type=PERIOD_RECORD_TYPE,
        record_id=period.id,
        details={
            "payroll_period_id": period.id,
            "tutor_user_id": period.tutor_user_id,
            "status": period.status,
            "line_count": line_count,
        },
    )


async def _refuse_blocking_work(
    session: AsyncSession, period: PayrollPeriodSummary
) -> None:
    blocking = await payroll_calculation.blocking_work(session, period)
    if blocking:
        raise payroll.PayrollUnresolvedWorkError(blocking)


async def prepare(
    session: AsyncSession, principal: Principal, period_id: uuid.UUID
) -> PayrollPeriodTransition:
    """``POST …/prepare``, in the order of the module docstring."""
    period = await payroll.lock_period(session, period_id)
    if period.status != OPEN:
        raise payroll.PayrollPeriodNotOpenError(period.status)
    if period.ends_on >= await administration.center_today(session):
        raise payroll.PayrollPeriodNotEndedError(period.ends_on)
    calculated = await payroll_calculation.recalculate(session, period_id)
    await _refuse_blocking_work(session, period)
    if calculated.total.is_negative():
        raise payroll.PayrollNegativeTotalError()
    prepared = await payroll.mark_prepared(
        session, period_id, by=principal.user_id, at=clock.now()
    )
    _record(session, prepared, PREPARED_EVENT, calculated.line_count)
    await administration.open_approval_request(
        session,
        request_type=PAYROLL_PERIOD_TYPE,
        subject_id=period_id,
        requester_user_id=principal.user_id,
    )
    return _transition(prepared)


async def _approval_request_id(
    session: AsyncSession, period_id: uuid.UUID
) -> uuid.UUID:
    """The period's request: a ``prepared`` period always has one."""
    request_id = await administration.approval_request_id_for_subject(
        session, request_type=PAYROLL_PERIOD_TYPE, subject_id=period_id
    )
    if request_id is None:  # pragma: no cover - prepare opens it
        raise RuntimeError("a prepared period has no approval request")
    return request_id


async def return_period(
    session: AsyncSession, principal: Principal, period_id: uuid.UUID, reason: str
) -> PayrollPeriodTransition:
    """``POST …/return``. The lines stay, and the next ``prepare`` rebuilds them
    and reopens the same request."""
    period = await payroll.lock_period(session, period_id)
    if period.status != PREPARED:
        raise payroll.PayrollPeriodNotPreparedError(period.status)
    returned = await payroll.mark_returned(
        session, period_id, by=principal.user_id, at=clock.now(), reason=reason
    )
    lines = await payroll.stored_lines(session, period_id)
    _record(session, returned, RETURNED_EVENT, len(lines))
    await administration.cancel_approval_request(
        session,
        request_id=await _approval_request_id(session, period_id),
        by_user_id=principal.user_id,
    )
    return _transition(returned)


async def finalize(
    session: AsyncSession,
    principal: Principal,
    period_id: uuid.UUID,
    prepared_at: datetime,
) -> PayrollPeriodTransition:
    """``POST …/finalize``, in the order of the module docstring. #113 adds the
    payslip after these writes."""
    period = await payroll.lock_period(session, period_id)
    if period.status != PREPARED:
        raise payroll.PayrollPeriodNotPreparedError(period.status)
    refusal = payroll.finalize_refusal(
        principal,
        tutor_user_id=period.tutor_user_id,
        prepared_by_user_id=period.prepared_by_user_id,
    )
    if refusal is not None:
        raise payroll.PayrollSelfFinalizeError(refusal)
    if period.prepared_at != prepared_at:
        raise payroll.PayrollReviewOutdatedError()
    earlier = await payroll.earliest_unfinalized_before(session, period)
    if earlier is not None:
        raise payroll.PayrollPeriodOutOfOrderError(earlier.id, earlier.status)
    stored = await payroll.stored_lines(session, period_id)
    drift = payroll.line_drift(
        payroll.rebuilt_only(stored),
        await payroll_calculation.compute_lines(session, period),
    )
    if not drift.is_clean:
        raise payroll.PayrollPeriodStaleError(
            added=drift.added, removed=drift.removed, changed=drift.changed
        )
    await _refuse_blocking_work(session, period)
    total = payroll.lines_total(stored)
    finalized = await payroll.mark_finalized(
        session, period_id, by=principal.user_id, at=clock.now(), total=total
    )
    _record(session, finalized, FINALIZED_EVENT, len(stored))
    await administration.decide_approval_request(
        session,
        principal=principal,
        request_id=await _approval_request_id(session, period_id),
        action="approve",
    )
    await administration.create_notification(
        session,
        recipient_user_id=period.tutor_user_id,
        notification_type=FINALIZED_NOTIFICATION,
        params=PeriodFinalizedParams(
            payroll_period_id=str(period_id),
            starts_on=period.starts_on.isoformat(),
            ends_on=period.ends_on.isoformat(),
        ),
    )
    return _transition(finalized)


async def list_periods(
    session: AsyncSession,
    principal: Principal,
    *,
    page: OffsetParams,
    status: PeriodStatus | None,
    tutor_user_id: uuid.UUID | None,
) -> OffsetPage[PayrollPeriodItem]:
    """``GET /api/v1/payroll/periods``. Three statements, whatever the page
    size."""
    rows = await payroll.list_periods(
        session,
        page=page,
        visible=(
            None
            if payroll.can_read_period(principal, OPEN)
            else payroll.REVIEWABLE_STATUSES
        ),
        status=status,
        tutor_user_id=tutor_user_id,
    )
    accounts = await identity.get_account_references(
        session, {row.tutor_user_id for row in rows.items}
    )
    return OffsetPage[PayrollPeriodItem](
        items=[
            PayrollPeriodItem(
                id=row.id,
                tutor_user_id=row.tutor_user_id,
                tutor_display_name=accounts[row.tutor_user_id].display_name,
                tutor_state=accounts[row.tutor_user_id].state,
                starts_on=row.starts_on,
                ends_on=row.ends_on,
                pay_frequency=row.pay_frequency,
                status=row.status,
                line_count=row.line_count,
                total=row.total,
                prepared_at=row.prepared_at,
                finalized_at=row.finalized_at,
            )
            for row in rows.items
        ],
        total=rows.total,
        limit=rows.limit,
        offset=rows.offset,
    )


async def _pending_closeouts(
    session: AsyncSession, period: PayrollPeriodSummary
) -> int:
    """#110 F4. One statement, with the zone read, no lock."""
    zone = await administration.get_center_time_zone(session)
    held = scheduling.held_session_columns(
        tutor_user_id=period.tutor_user_id,
        starts_from=floor_instant(period.starts_on, zone),
        starts_before=floor_instant(period.ends_on + timedelta(days=1), zone),
    ).subquery()
    count = await session.scalar(
        select(func.count())
        .select_from(held)
        .where(academics.has_no_current_approval(held.c.class_session_id))
    )
    return count or 0


def _name(
    accounts: dict[uuid.UUID, AccountReference], user_id: uuid.UUID | None
) -> AccountName | None:
    if user_id is None:
        return None
    return AccountName(id=user_id, display_name=accounts[user_id].display_name)


async def read_period(
    session: AsyncSession, principal: Principal, period_id: uuid.UUID
) -> PayrollPeriodReview:
    """``GET /api/v1/payroll/periods/{id}/lines``: the record policy comes first,
    so a hidden period can't answer the lines' ``409``. No lock."""
    period = await payroll.period_row(session, period_id)
    if not payroll.can_read_period(principal, period.status):
        raise RecordNotFoundError()
    read = await payroll.period_lines(session, period_id)
    accounts = await identity.get_account_references(
        session,
        {
            user_id
            for user_id in (
                period.tutor_user_id,
                period.prepared_by_user_id,
                period.finalized_by_user_id,
                period.returned_by_user_id,
            )
            if user_id is not None
        },
    )
    earlier = await payroll.earliest_unfinalized_before(session, period)
    tutor = accounts[period.tutor_user_id]
    return PayrollPeriodReview(
        period=PayrollPeriodDetail(
            id=period.id,
            tutor_user_id=period.tutor_user_id,
            starts_on=period.starts_on,
            ends_on=period.ends_on,
            pay_frequency=period.pay_frequency,
            status=period.status,
            tutor_display_name=tutor.display_name,
            tutor_state=tutor.state,
            prepared_at=period.prepared_at,
            prepared_by=_name(accounts, period.prepared_by_user_id),
            finalized_at=period.finalized_at,
            finalized_by=_name(accounts, period.finalized_by_user_id),
            total_amount=period.total_amount,
            returned_at=period.returned_at,
            returned_by=_name(accounts, period.returned_by_user_id),
            return_reason=period.return_reason,
            earlier_unfinalized_period_id=None if earlier is None else earlier.id,
            pending_closeouts=await _pending_closeouts(session, period),
        ),
        lines=read.lines,
        total=read.total,
    )
