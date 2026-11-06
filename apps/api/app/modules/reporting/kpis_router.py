"""Analytics KPI routes (#126; definitions in ``_docs/kpis.md``).

Each KPI is gated like the page its rows come from (E3), with no new
permission:

- K1 ``revenue-trend`` and K2 ``course-profitability``: ``finance.report.view``
  (Owner, Finance Staff; an Administrator gets ``403`` even with
  ``payroll.finalize`` delegated, as on #117's routes);
- K4 ``room-utilization`` and K5 ``student-retention``: ``schedule.view``;
- K6 ``tutor-performance`` (totals = K7): ``schedule.view`` **and**
  ``tutor.view``, so Finance Staff get ``403``.

K3 (unpaid balances and payroll) is #117's ``GET /reports/finance/balances``.
Every range is #117's: ``from``/``to`` center-local dates, both inclusive,
``from ≤ to ≤`` the center's today and ``to − from < 366``, else ``422
report_range_invalid`` (:func:`~app.modules.reporting.router.check_range`).
The order is ``401`` → ``403 permission_denied`` → ``422 validation_error``
→ ``422 report_range_invalid`` → ``200``.

Read-only: no lock, no write, no audit event, no ``Idempotency-Key``.
"""

from datetime import date
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    ensure_permission,
    require,
)
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWork, UnitOfWorkDep
from app.modules.reporting import finance, kpis
from app.modules.reporting.kpi_schemas import (
    CourseProfitability,
    RevenueTrend,
    RoomUtilization,
    StudentRetention,
    TutorPerformance,
)
from app.modules.reporting.router import REPORT_VIEW, check_range
from app.modules.reporting.schemas import ReportBasis

SCHEDULE_VIEW: Final = "schedule.view"
TUTOR_VIEW: Final = "tutor.view"

router = APIRouter(prefix="/api/v1/reports/kpis", tags=["reports"])

RANGE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **PERMISSION_RESPONSES,
    422: problem_response_doc(
        "The query is not valid: from or to (or basis) missing or malformed"
        " (validation_error); from after to, to after the center's today, or"
        " more than 366 days (report_range_invalid)."
    ),
}

From = Annotated[date, Query(alias="from", description="First local date, inclusive.")]
To = Annotated[date, Query(description="Last local date, inclusive.")]


def _schedule_and_tutor_view(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
) -> Principal:
    """``schedule.view`` (the route's access rule), then ``tutor.view``:
    both are needed, so the tutor names stay as visible as the tutor
    directory (E3). Runs before the query is validated (``403`` before
    ``422``)."""
    ensure_permission(principal, TUTOR_VIEW)
    return principal


async def _checked(uow: UnitOfWork, start: date, end: date) -> None:
    check_range(start, end, await finance.center_today(uow.session))


@router.get(
    "/revenue-trend",
    response_model=RevenueTrend,
    response_model_by_alias=True,
    responses=RANGE_RESPONSES,
)
async def get_revenue_trend(
    principal: Annotated[Principal, require(REPORT_VIEW)],
    uow: UnitOfWorkDep,
    basis: Annotated[ReportBasis, Query(description="accrual or cash.")],
    from_: From,
    to: To,
) -> RevenueTrend:
    """K1: revenue, direct tutor cost (the payroll KPI), gross profit,
    operating expenses and the net result per center-local month of the
    range (clipped to it, zeros included) and in total. Each month equals
    `GET /reports/finance/profitability` over that month's part of the range
    on the same basis, and the totals equal it over the whole range, with
    every #117 rule (accrual restatement included)."""
    await _checked(uow, from_, to)
    return await kpis.revenue_trend(uow.session, basis=basis, start=from_, end=to)


@router.get(
    "/course-profitability",
    response_model=CourseProfitability,
    response_model_by_alias=True,
    responses=RANGE_RESPONSES,
)
async def get_course_profitability(
    principal: Annotated[Principal, require(REPORT_VIEW)],
    uow: UnitOfWorkDep,
    from_: From,
    to: To,
) -> CourseProfitability:
    """K2 (accrual, no overhead): the course-class revenue and direct tutor
    cost of the range per cohort, plus what can't be tied to a cohort
    (`not_attributed`). The sum is the accrual report's `course` row."""
    await _checked(uow, from_, to)
    return await kpis.course_profitability(uow.session, start=from_, end=to)


@router.get(
    "/room-utilization",
    response_model=RoomUtilization,
    response_model_by_alias=True,
    responses=RANGE_RESPONSES,
)
async def get_room_utilization(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    uow: UnitOfWorkDep,
    from_: From,
    to: To,
) -> RoomUtilization:
    """K4: each active room's available minutes (operating hours ∩ the
    room's windows, closure days excluded, wall-clock) and used minutes (the
    scheduled minutes of its confirmed or completed sessions that have
    started), with the percentage (it may exceed 100.00)."""
    await _checked(uow, from_, to)
    return await kpis.room_utilization(uow.session, start=from_, end=to)


@router.get(
    "/student-retention",
    response_model=StudentRetention,
    response_model_by_alias=True,
    responses=RANGE_RESPONSES,
)
async def get_student_retention(
    principal: Annotated[Principal, require(SCHEDULE_VIEW)],
    uow: UnitOfWorkDep,
    from_: From,
    to: To,
) -> StudentRetention:
    """K5: students active in the range (at least one confirmed or completed
    session that has started) against the window of the same length just
    before it: retained, new, lost and the retention percentage, plus the
    active students per month. Counts only."""
    await _checked(uow, from_, to)
    return await kpis.student_retention(uow.session, start=from_, end=to)


@router.get(
    "/tutor-performance",
    response_model=TutorPerformance,
    response_model_by_alias=True,
    responses=RANGE_RESPONSES,
)
async def get_tutor_performance(
    principal: Annotated[Principal, Depends(_schedule_and_tutor_view)],
    uow: UnitOfWorkDep,
    from_: From,
    to: To,
) -> TutorPerformance:
    """K6 per current tutor (a substitute is credited) and K7 center-wide
    (`totals`): sessions, completed, pending closeout, cancelled after
    confirmation, scheduled minutes, distinct students and attendance with
    the #84 percentage. No money, grade or ranking."""
    await _checked(uow, from_, to)
    return await kpis.tutor_performance(uow.session, start=from_, end=to)
