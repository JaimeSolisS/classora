"""The payroll calculation (#107 D, F; spec W3, W7; ADR-0017 E2, E9; data-model
§15).

Pay is derived, never posted. Nothing pays at a closeout approval, a
cancellation or a reschedule: :func:`recalculate` reads the committed state of
``academics`` and ``scheduling`` and rebuilds the lines of one ``open`` period.
It takes no lock on either module and no advisory lock, and no writer of those
modules takes a period lock, so a payroll failure can't roll back a closeout,
and neither side waits for the other (§15).

- :func:`compute_lines` only reads and returns frozen drafts, by service date,
  then session. #110's drift check compares them, with no write. It reads the
  period's work in center-local dates: ``floor_instant(starts_on)`` to
  ``floor_instant(ends_on + 1)``, half-open, and a service date is the local
  date of the start. It takes the versions of the tutor ``FOR KEY SHARE``, so a
  concurrent delete waits for the run.
- :func:`recalculate`: the period ``FOR UPDATE``, re-read → ``404`` → ``409
  payroll_period_not_open`` → :func:`compute_lines` → replace the rebuilt kinds
  → the event. A second run leaves the same lines but for ``id`` and
  ``created_at``. Two runs at once take turns on the period row.

A revision or an approval that arrives later shows on the next run of an open
period. A prepared period waits for ``return`` or #110's drift check, and a
finalized one for #111.

**Statements:** eleven for a run, whatever the number of sessions, with the
zone not yet read, the event included.

Log lines and the event carry ids and counts, never an amount.
"""

import uuid
from datetime import timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit_event
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import (
    PayrollPeriodLines,
    PayrollPeriodSummary,
    PayrollRecalculation,
)
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import floor_instant, local_date

RECALCULATED_EVENT: Final = "payroll.period_recalculated"
PERIOD_RECORD_TYPE: Final = "payroll_period"


async def compute_lines(
    session: AsyncSession, period: PayrollPeriodSummary
) -> list[payroll.LineDraft]:
    """The lines the period's facts give now. Reads only."""
    zone = await administration.get_center_time_zone(session)
    starts_from = floor_instant(period.starts_on, zone)
    starts_before = floor_instant(period.ends_on + timedelta(days=1), zone)
    tutor = period.tutor_user_id
    basis = payroll.PayBasis(
        terms=await payroll.terms_basis_versions(session, tutor),
        rates=await payroll.rate_versions(session, tutor),
        rules=await payroll.session_pay_rule_versions(session),
    )
    approvals = await academics.current_approvals(
        session, tutor, starts_from, starts_before
    )
    makeups = await scheduling.makeup_session_ids(
        session, [approval.class_session_id for approval in approvals]
    )
    cancellations = await scheduling.payable_cancellations(
        session, tutor, starts_from, starts_before
    )
    return payroll.draft_lines(
        starts_on=period.starts_on,
        ends_on=period.ends_on,
        basis=basis,
        approved=[
            payroll.ApprovedSession(
                approval_id=approval.approval_id,
                class_session_id=approval.class_session_id,
                class_kind=approval.session_kind,
                service_date=local_date(approval.session_starts_at, zone),
                approved_minutes=approval.approved_minutes,
                all_absent=academics.all_marked_absent(
                    approval.attendance, len(approval.attendance)
                ),
                is_makeup=approval.class_session_id in makeups,
            )
            for approval in approvals
        ],
        cancellations=[
            payroll.PayableCancellation(
                class_session_id=cancellation.class_session_id,
                class_kind=cancellation.kind,
                service_date=local_date(cancellation.starts_at, zone),
                scheduled_minutes=cancellation.scheduled_minutes,
                classification=cancellation.classification,
            )
            for cancellation in cancellations
        ],
    )


async def recalculate(
    session: AsyncSession, period_id: uuid.UUID
) -> PayrollRecalculation:
    """Rebuild the session-sourced lines of an ``open`` period."""
    period = await payroll.lock_open_period(session, period_id)
    drafts = await compute_lines(session, period)
    result = await payroll.replace_lines(session, period, drafts)
    record_audit_event(
        session,
        action=RECALCULATED_EVENT,
        record_type=PERIOD_RECORD_TYPE,
        record_id=period.id,
        details={
            "payroll_period_id": period.id,
            "tutor_user_id": period.tutor_user_id,
            "line_count": result.line_count,
        },
    )
    return result


async def read_lines(session: AsyncSession, period_id: uuid.UUID) -> PayrollPeriodLines:
    """``GET /api/v1/payroll/periods/{id}/lines``."""
    return await payroll.period_lines(session, period_id)
