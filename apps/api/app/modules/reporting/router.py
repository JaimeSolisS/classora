"""Financial report routes (#117; ADR-0017 E12, E18; ADR-0006 E7).

Owners and Finance Staff (``finance.report.view``; not delegable) read them.
Administrators (also with ``payroll.finalize`` delegated), academic
coordinators, tutors, students and guardians get ``403 permission_denied``.

- ``GET /api/v1/reports/finance/profitability?basis=&from=&to=``: range
  totals on the accrual or the cash basis. ``from`` and ``to`` are center-local
  dates, both inclusive: ``from ≤ to ≤`` the center's today and ``to − from <
  366``, else ``422 report_range_invalid`` at the offending parameter.
- ``GET /api/v1/reports/finance/balances``: receivables, payables and deferred
  revenue as of the center's today.

Read-only: no lock, no write, no audit event, no ``Idempotency-Key``.
"""

from datetime import date
from typing import Annotated, Final

from fastapi import APIRouter, Query

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.reporting import finance
from app.modules.reporting.errors import ReportRangeInvalidError
from app.modules.reporting.schemas import Balances, Profitability, ReportBasis

REPORT_VIEW: Final = "finance.report.view"
MAX_SPAN_DAYS: Final = 366

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def check_range(start: date, end: date, today: date) -> None:
    """#117 E2/B2, in this order: ``from > to`` (at ``to``), ``to`` after
    today (at ``to``), a span of 366 days or more (at ``from``)."""
    if start > end:
        raise ReportRangeInvalidError(
            "to", "The last date must not be before the first."
        )
    if end > today:
        raise ReportRangeInvalidError("to", "The last date must not be after today.")
    if (end - start).days >= MAX_SPAN_DAYS:
        raise ReportRangeInvalidError("from", "The range must not exceed 366 days.")


@router.get(
    "/finance/profitability",
    response_model=Profitability,
    response_model_by_alias=True,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The query is not valid: basis, from or to missing or malformed"
            " (validation_error); from after to, to after the center's today,"
            " or more than 366 days (report_range_invalid)."
        ),
    },
)
async def get_profitability(
    principal: Annotated[Principal, require(REPORT_VIEW)],
    uow: UnitOfWorkDep,
    basis: Annotated[ReportBasis, Query(description="accrual or cash.")],
    from_: Annotated[
        date, Query(alias="from", description="First local date, inclusive.")
    ],
    to: Annotated[date, Query(description="Last local date, inclusive.")],
) -> Profitability:
    """Revenue, direct tutor cost, gross profit, operating expenses and the
    net result over the range. **Accrual** (ADR-0006 E7): computed live from
    the current nets, so a credit, withdrawal or expense adjustment posted
    later changes the figures of the period it belongs to; payroll adjustments
    count on their target period's end date and never restate. `class_rows`
    gives individual, group, course and unallocated (manual charges, salary,
    pay items, free payroll adjustments). **Cash**: payments received minus
    refunds paid out, payroll paid and expenses paid; a reversed payment
    disappears from its date; `class_rows` is empty."""
    today = await finance.center_today(uow.session)
    check_range(from_, to, today)
    return await finance.profitability_report(
        uow.session, basis=basis, start=from_, end=to
    )


@router.get(
    "/finance/balances",
    response_model=Balances,
    responses={**PERMISSION_RESPONSES},
)
async def get_balances(
    principal: Annotated[Principal, require(REPORT_VIEW)],
    uow: UnitOfWorkDep,
) -> Balances:
    """Receivables (open charges, of which overdue, and the account credit,
    shown and never netted), payables (open expenses, of which overdue, and
    unpaid finalized payroll) and deferred revenue (what the accrual rules
    recognize after today), as of the center's today."""
    return await finance.balances_report(uow.session)
