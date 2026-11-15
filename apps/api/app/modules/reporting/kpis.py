"""Analytics KPIs: queries and assembly (#126; definitions, worked examples
and the budget in ``_docs/kpis.md``).

Read-only: no lock, no write, no audit event, no storage (E11). Money KPIs
come only from #117's code paths in :mod:`~app.modules.reporting.finance`
(its loaders and pure assembly); this module never re-implements a net, an
open amount, a paid sum or "unreversed".

- K1 :func:`revenue_trend`: each month of the range is #117
  ``profitability`` over that month's part; the totals are it over the whole
  range. One load, bucketed: the same statements as one profitability call.
- K2 :func:`course_profitability`: #117's accrual ``course`` entries per
  cohort, plus ``not_attributed``; their sum is #117's ``course`` row.
- K4 :func:`room_utilization`, K5 :func:`student_retention`, K6/K7
  :func:`tutor_performance`: session counts over the instant window
  ``[L(from), L(to + 1))`` of center-local midnights, ``starts_at ≤ now``
  (#84's in-scope set). The range filters compare ``starts_at`` with
  instants computed here, so they can use ``ix_class_sessions_starts_at``;
  the local date appears only in a select list.

Each function runs a fixed number of statements, whatever the row counts
(C1), the zone and today included.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import Date, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core import clock
from app.core.money import Money
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.catalog.models import Classroom, ClassroomAvailabilityWindow
from app.modules.courses import service as courses
from app.modules.identity import service as identity
from app.modules.reporting import finance, kpi_rules
from app.modules.reporting.finance import (
    CostEntry,
    ExpenseGroup,
    RevenueEntry,
    accrual_profitability,
    operating_expenses,
    profitability,
)
from app.modules.reporting.kpi_schemas import (
    ActiveMonth,
    ActivityFigures,
    AttendanceCounts,
    CohortProfitability,
    CohortRef,
    CourseFigures,
    CourseProfitability,
    RevenueTrend,
    RoomRef,
    RoomUtilization,
    RoomUtilizationRow,
    StudentRetention,
    TrendFigures,
    TrendMonth,
    TutorPerformance,
    TutorPerformanceRow,
    TutorRef,
    UtilizationFigures,
)
from app.modules.reporting.schemas import Profitability, ReportBasis
from app.modules.scheduling.models import (
    ClassSession,
    ClassSessionAttendance,
    ClassSessionParticipant,
)

type AnyRows = Select[*tuple[Any, ...]]
"""A statement of any columns (the C2 EXPLAIN test compiles them)."""

IN_SCOPE_STATES: Final = ("confirmed", "completed")
"""#84 E6 (``scheduling.attendance.in_scope_participations``): with
``starts_at ≤ now``."""
CANCELLED: Final = "cancelled"
COMPLETED: Final = "completed"
CONFIRMED: Final = "confirmed"
COURSE: Final = "course"


# --- Common -----------------------------------------------------------------------


def local_midnight(day: date, zone: ZoneInfo) -> datetime:
    """The instant of ``day``'s local midnight in ``zone`` (UTC)."""
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


def instant_window(start: date, end: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """``[L(start), L(end + 1))``: every instant whose local date is in
    ``[start, end]`` (23 or 25 real hours on a DST change day)."""
    return local_midnight(start, zone), local_midnight(end + timedelta(days=1), zone)


def in_scope(low: datetime, high: datetime, now: datetime) -> Any:
    """#84's in-scope sessions whose start is in ``[low, high)`` (SQL)."""
    return and_(
        ClassSession.starts_at >= low,
        ClassSession.starts_at < high,
        ClassSession.starts_at <= now,
        ClassSession.state.in_(IN_SCOPE_STATES),
    )


def _first_of(day: date) -> date:
    return day.replace(day=1)


# --- K1 revenue trend -------------------------------------------------------------


def _figures(report: Profitability) -> dict[str, Money]:
    return {
        "revenue_total": report.revenue_total,
        "direct_tutor_cost_total": report.direct_tutor_cost_total,
        "gross_profit": report.gross_profit,
        "operating_expenses_total": report.operating_expenses.total,
        "net_result": report.net_result,
    }


def _of_month(groups: Iterable[ExpenseGroup], month: kpi_rules.Month) -> list[Any]:
    first = _first_of(month.start)
    return [group for group in groups if group.month == first]


def trend(
    *,
    basis: ReportBasis,
    start: date,
    end: date,
    today: date,
    monthly: Sequence[tuple[kpi_rules.Month, Profitability]],
    whole: Profitability,
) -> RevenueTrend:
    return RevenueTrend.model_validate(
        {
            "basis": basis,
            "from": start,
            "to": end,
            "today": today,
            "months": [
                TrendMonth.model_validate(
                    {
                        "month": month.month,
                        "from": month.start,
                        "to": month.end,
                        **_figures(report),
                    }
                )
                for month, report in monthly
            ],
            "totals": TrendFigures(**_figures(whole)),
        }
    )


def accrual_trend(
    *,
    start: date,
    end: date,
    today: date,
    revenue: Sequence[RevenueEntry],
    costs: Sequence[CostEntry],
    expenses: Sequence[ExpenseGroup],
) -> RevenueTrend:
    """K1 accrual (pure): each month is :func:`accrual_profitability` over its
    part of the range, with the expenses grouped in that month."""
    monthly = [
        (
            month,
            accrual_profitability(
                start=month.start,
                end=month.end,
                today=today,
                revenue=revenue,
                costs=costs,
                expenses=_of_month(expenses, month),
            ),
        )
        for month in kpi_rules.months(start, end)
    ]
    whole = accrual_profitability(
        start=start,
        end=end,
        today=today,
        revenue=revenue,
        costs=costs,
        expenses=expenses,
    )
    return trend(
        basis="accrual",
        start=start,
        end=end,
        today=today,
        monthly=monthly,
        whole=whole,
    )


def cash_trend(
    *,
    start: date,
    end: date,
    today: date,
    sums: dict[date, finance.CashMonth],
    expenses: Sequence[ExpenseGroup],
) -> RevenueTrend:
    """K1 cash (pure): #117's cash :func:`profitability` per month from the
    month-grouped sums and expense payments."""
    zero = Money.zero()

    def report(
        lower: date, upper: date, found: Sequence[finance.CashMonth], groups: Any
    ) -> Profitability:
        return profitability(
            basis="cash",
            start=lower,
            end=upper,
            today=today,
            rows=[],
            revenue_total=Money.total(m.revenue for m in found) if found else zero,
            cost_total=Money.total(m.payroll_paid for m in found) if found else zero,
            expenses=operating_expenses(groups),
        )

    monthly = []
    for month in kpi_rules.months(start, end):
        found = sums.get(_first_of(month.start))
        monthly.append(
            (
                month,
                report(
                    month.start,
                    month.end,
                    [found] if found is not None else [],
                    _of_month(expenses, month),
                ),
            )
        )
    whole = report(start, end, list(sums.values()), expenses)
    return trend(
        basis="cash", start=start, end=end, today=today, monthly=monthly, whole=whole
    )


async def revenue_trend(
    session: AsyncSession, *, basis: ReportBasis, start: date, end: date
) -> RevenueTrend:
    """``GET …/kpis/revenue-trend`` once the range is valid (K1). The same
    statements as ``finance.profitability_report`` on the same basis."""
    zone, today = await finance._zone_and_today(session)
    if basis == "cash":
        sums = await finance._cash_sums_by_month(session, start, end)
        expenses = await finance._cash_expenses(session, start, end, by_month=True)
        return cash_trend(
            start=start, end=end, today=today, sums=sums, expenses=expenses
        )
    revenue = await finance._charge_revenue(session, zone)
    costs = await finance._cost_entries(session, start, end)
    groups = await finance._accrual_expenses(session, start, end, today, by_month=True)
    return accrual_trend(
        start=start,
        end=end,
        today=today,
        revenue=revenue,
        costs=costs,
        expenses=groups,
    )


# --- K2 course profitability ------------------------------------------------------


def _course_figures(revenue: Iterable[Money], cost: Iterable[Money]) -> CourseFigures:
    earned = Money.total(revenue)
    paid = Money.total(cost)
    return CourseFigures(
        revenue=earned, direct_tutor_cost=paid, gross_profit=earned - paid
    )


def course_rows(
    *,
    start: date,
    end: date,
    revenue: Iterable[RevenueEntry],
    costs: Iterable[CostEntry],
) -> tuple[dict[uuid.UUID, CourseFigures], CourseFigures, CourseFigures]:
    """K2 (pure): ``(per cohort, not_attributed, total)`` over the
    ``course``-class entries dated in ``[start, end]``, the entries #117's
    ``class_rows`` sums into its ``course`` row. Cohorts whose revenue and
    cost are both zero are left out."""
    income: defaultdict[uuid.UUID | None, list[Money]] = defaultdict(list)
    spent: defaultdict[uuid.UUID | None, list[Money]] = defaultdict(list)
    for item in revenue:
        if item.class_type == COURSE and finance._within(item.on, start, end):
            income[item.cohort_id].append(item.amount)
    for cost in costs:
        if cost.class_type == COURSE and finance._within(cost.on, start, end):
            spent[cost.cohort_id].append(cost.amount)
    cohorts: dict[uuid.UUID, CourseFigures] = {}
    for cohort in set(income) | set(spent):
        if cohort is None:
            continue
        figures = _course_figures(income[cohort], spent[cohort])
        if not (figures.revenue.is_zero() and figures.direct_tutor_cost.is_zero()):
            cohorts[cohort] = figures
    total = _course_figures(
        [amount for amounts in income.values() for amount in amounts],
        [amount for amounts in spent.values() for amount in amounts],
    )
    return cohorts, _course_figures(income[None], spent[None]), total


async def _cohort_names(
    session: AsyncSession, cohort_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """The cohorts' names. One statement, run even for no cohort (a fixed
    count)."""
    names = courses.cohort_name_columns().subquery("cohort_names")
    rows = await session.execute(
        select(names.c.id, names.c.name).where(names.c.id.in_(sorted(cohort_ids)))
    )
    return {row.id: row.name for row in rows}


async def course_profitability(
    session: AsyncSession, *, start: date, end: date
) -> CourseProfitability:
    """``GET …/kpis/course-profitability`` once the range is valid (K2):
    #117's accrual loaders (revenue and cost, no expenses) plus the cohort
    names."""
    zone, today = await finance._zone_and_today(session)
    revenue = await finance._charge_revenue(session, zone)
    costs = await finance._cost_entries(session, start, end)
    cohorts, unattributed, total = course_rows(
        start=start, end=end, revenue=revenue, costs=costs
    )
    names = await _cohort_names(session, cohorts)
    rows = [
        CohortProfitability(
            cohort=CohortRef(id=cohort, name=names.get(cohort, "")),
            revenue=figures.revenue,
            direct_tutor_cost=figures.direct_tutor_cost,
            gross_profit=figures.gross_profit,
        )
        for cohort, figures in cohorts.items()
    ]
    rows.sort(key=lambda row: (-row.gross_profit.amount, str(row.cohort.id)))
    return CourseProfitability.model_validate(
        {
            "from": start,
            "to": end,
            "today": today,
            "cohorts": rows,
            "not_attributed": unattributed,
            "total": total,
        }
    )


# --- K4 room utilization ----------------------------------------------------------


def _room_minutes_query(low: datetime, high: datetime, now: datetime) -> AnyRows:
    """Σ ``scheduled_minutes`` of the in-scope sessions per room (C2: a range
    on ``starts_at``)."""
    return (
        select(
            ClassSession.classroom_id,
            func.sum(ClassSession.scheduled_minutes).label("minutes"),
        )
        .where(in_scope(low, high, now), ClassSession.classroom_id.is_not(None))
        .group_by(ClassSession.classroom_id)
    )


def _hhmm(value: str) -> time:
    return time.fromisoformat(value)


def utilization(
    *,
    start: date,
    end: date,
    hours: dict[int, tuple[time, time]],
    closed: frozenset[date],
    rooms: Sequence[tuple[uuid.UUID, str, Sequence[tuple[int, time, time]]]],
    used: dict[uuid.UUID, int],
) -> RoomUtilization:
    """K4 (pure): each active room's available and used minutes, and the
    total row."""
    dates = kpi_rules.days(start, end)
    rows = []
    for room_id, name, windows in rooms:
        available = sum(
            kpi_rules.available_minutes(
                day, hours=hours, windows=windows, closed=day in closed
            )
            for day in dates
        )
        minutes = used.get(room_id, 0)
        rows.append(
            RoomUtilizationRow(
                room=RoomRef(id=room_id, name=name),
                available_minutes=available,
                used_minutes=minutes,
                utilization_percent=kpi_rules.percent(minutes, available),
            )
        )
    rows.sort(key=lambda row: (row.room.name.lower(), str(row.room.id)))
    available = sum(row.available_minutes for row in rows)
    minutes = sum(row.used_minutes for row in rows)
    return RoomUtilization.model_validate(
        {
            "from": start,
            "to": end,
            "rooms": rows,
            "total": UtilizationFigures(
                available_minutes=available,
                used_minutes=minutes,
                utilization_percent=kpi_rules.percent(minutes, available),
            ),
        }
    )


async def _active_rooms(
    session: AsyncSession,
) -> list[tuple[uuid.UUID, str, list[tuple[int, time, time]]]]:
    """The active classrooms with their windows. One statement."""
    rows = await session.execute(
        select(
            Classroom.id,
            Classroom.name,
            ClassroomAvailabilityWindow.weekday,
            ClassroomAvailabilityWindow.starts_at,
            ClassroomAvailabilityWindow.ends_at,
        )
        .outerjoin(
            ClassroomAvailabilityWindow,
            ClassroomAvailabilityWindow.classroom_id == Classroom.id,
        )
        .where(Classroom.is_active)
        .order_by(Classroom.id)
    )
    rooms: dict[uuid.UUID, tuple[uuid.UUID, str, list[tuple[int, time, time]]]] = {}
    for row in rows:
        room = rooms.setdefault(row.id, (row.id, row.name, []))
        if row.weekday is not None:
            room[2].append((row.weekday, row.starts_at, row.ends_at))
    return list(rooms.values())


async def room_utilization(
    session: AsyncSession, *, start: date, end: date
) -> RoomUtilization:
    """``GET …/kpis/room-utilization`` once the range is valid (K4).
    Statements: the zone, the business settings and hours (two), the
    closures, the rooms with their windows, the used minutes."""
    zone, _today = await finance._zone_and_today(session)
    settings = await administration.get_business_settings(session)
    hours = {
        item.weekday: (_hhmm(item.opens_at), _hhmm(item.closes_at))
        for item in settings.operating_hours
    }
    closures = await catalog.closures_between(session, start, end)
    closed = kpi_rules.closed_days(
        ((c.starts_on, c.ends_on) for c in closures), start, end
    )
    rooms = await _active_rooms(session)
    low, high = instant_window(start, end, zone)
    used = {
        row.classroom_id: int(row.minutes)
        for row in await session.execute(_room_minutes_query(low, high, clock.now()))
    }
    return utilization(
        start=start, end=end, hours=hours, closed=closed, rooms=rooms, used=used
    )


# --- K5 student retention ---------------------------------------------------------


def _local_month(zone: ZoneInfo) -> Any:
    """The first of the session's center-local month (select list only)."""
    local = func.timezone(str(zone), ClassSession.starts_at)
    return cast(func.date_trunc("month", local), Date)


def _retention_query(
    zone: ZoneInfo, low: datetime, middle: datetime, high: datetime, now: datetime
) -> AnyRows:
    """The distinct (student, local month, in the current window) of the
    in-scope participations in ``[low, high)``; ``middle`` is the current
    window's first instant (C2: a range on ``starts_at``)."""
    return (
        select(
            ClassSessionParticipant.student_id,
            _local_month(zone).label("month"),
            (ClassSession.starts_at >= middle).label("current"),
        )
        .select_from(ClassSessionParticipant)
        .join(ClassSession, ClassSession.id == ClassSessionParticipant.class_session_id)
        .where(in_scope(low, high, now))
        .distinct()
    )


def retention_report(
    *,
    start: date,
    end: date,
    previous: tuple[date, date],
    rows: Iterable[tuple[uuid.UUID, date, bool]],
) -> StudentRetention:
    """K5 (pure) from ``(student, first of local month, current)`` rows."""
    before: set[uuid.UUID] = set()
    now: set[uuid.UUID] = set()
    by_month: defaultdict[date, set[uuid.UUID]] = defaultdict(set)
    for student, month, current in rows:
        if current:
            now.add(student)
            by_month[month].add(student)
        else:
            before.add(student)
    counts = kpi_rules.retention(before, now)
    return StudentRetention.model_validate(
        {
            "from": start,
            "to": end,
            "previous_from": previous[0],
            "previous_to": previous[1],
            "active_previous": counts.active_previous,
            "active_current": counts.active_current,
            "retained": counts.retained,
            "new": counts.new,
            "lost": counts.lost,
            "retention_percent": counts.retention_percent,
            "months": [
                ActiveMonth.model_validate(
                    {
                        "month": month.month,
                        "from": month.start,
                        "to": month.end,
                        "active_students": len(by_month[_first_of(month.start)]),
                    }
                )
                for month in kpi_rules.months(start, end)
            ],
        }
    )


async def student_retention(
    session: AsyncSession, *, start: date, end: date
) -> StudentRetention:
    """``GET …/kpis/student-retention`` once the range is valid (K5).
    Statements: the zone and the participations."""
    zone, _today = await finance._zone_and_today(session)
    previous = kpi_rules.previous_window(start, end)
    low = local_midnight(previous[0], zone)
    middle, high = instant_window(start, end, zone)
    found = await session.execute(
        _retention_query(zone, low, middle, high, clock.now())
    )
    return retention_report(
        start=start,
        end=end,
        previous=previous,
        rows=((row.student_id, row.month, row.current) for row in found),
    )


# --- K6 tutor performance, K7 center activity -------------------------------------


def counted_cancellation(low: datetime, high: datetime) -> Any:
    """A session cancelled after a confirmation, starting in ``[low, high)``
    (SQL)."""
    return and_(
        ClassSession.starts_at >= low,
        ClassSession.starts_at < high,
        ClassSession.state == CANCELLED,
        ClassSession.first_confirmed_at.is_not(None),
    )


def _tutor_sessions_query(low: datetime, high: datetime, now: datetime) -> AnyRows:
    """Per current tutor: in-scope sessions, completed, pending closeout,
    counted cancellations and scheduled minutes (C2: a range on
    ``starts_at``)."""
    scoped = in_scope(low, high, now)
    return (
        select(
            ClassSession.tutor_user_id,
            func.count().filter(scoped).label("sessions"),
            func.count()
            .filter(scoped, ClassSession.state == COMPLETED)
            .label("completed"),
            func.count()
            .filter(
                scoped,
                ClassSession.state == CONFIRMED,
                ClassSession.ends_at <= now,
            )
            .label("pending_closeout"),
            func.count().filter(ClassSession.state == CANCELLED).label("cancelled"),
            func.coalesce(
                func.sum(ClassSession.scheduled_minutes).filter(scoped), 0
            ).label("scheduled_minutes"),
        )
        .where(or_(scoped, counted_cancellation(low, high)))
        .group_by(ClassSession.tutor_user_id)
    )


def _tutor_participations_query(
    low: datetime, high: datetime, now: datetime
) -> AnyRows:
    """Per current tutor and center-wide (``ROLLUP``; ``total`` is 1 on the
    center row): distinct students and the attendance marks of the in-scope
    participations (C2: a range on ``starts_at``)."""
    status = ClassSessionAttendance.status
    return (
        select(
            ClassSession.tutor_user_id,
            func.grouping(ClassSession.tutor_user_id).label("total"),
            func.count(ClassSessionParticipant.student_id.distinct()).label("students"),
            func.count().filter(status == "present").label("present"),
            func.count().filter(status == "late").label("late"),
            func.count().filter(status == "absent").label("absent"),
            func.count().filter(status.is_(None)).label("not_recorded"),
        )
        .select_from(ClassSessionParticipant)
        .join(ClassSession, ClassSession.id == ClassSessionParticipant.class_session_id)
        .outerjoin(
            ClassSessionAttendance,
            and_(
                ClassSessionAttendance.class_session_id
                == ClassSessionParticipant.class_session_id,
                ClassSessionAttendance.student_id == ClassSessionParticipant.student_id,
            ),
        )
        .where(in_scope(low, high, now))
        .group_by(func.rollup(ClassSession.tutor_user_id))
    )


ZERO_ACTIVITY: Final = {
    "sessions": 0,
    "completed": 0,
    "pending_closeout": 0,
    "cancelled": 0,
    "scheduled_minutes": 0,
}
ZERO_PARTICIPATION: Final = {
    "students": 0,
    "present": 0,
    "late": 0,
    "absent": 0,
    "not_recorded": 0,
}


def _activity(counts: dict[str, int], marks: dict[str, int]) -> dict[str, Any]:
    present, late, absent = marks["present"], marks["late"], marks["absent"]
    return {
        **counts,
        "students": marks["students"],
        "attendance": AttendanceCounts(
            present=present,
            late=late,
            absent=absent,
            not_recorded=marks["not_recorded"],
        ),
        "attendance_percent": kpi_rules.attendance_percent(present, late, absent),
    }


def performance(
    *,
    start: date,
    end: date,
    sessions: dict[uuid.UUID | None, dict[str, int]],
    participations: dict[uuid.UUID, dict[str, int]],
    center: dict[str, int],
    names: dict[uuid.UUID, str],
) -> TutorPerformance:
    """K6/K7 (pure). ``sessions`` is keyed by current tutor (``None``: a
    counted cancellation with no tutor, in the totals only);
    ``participations`` by tutor, ``center`` the center-wide row."""
    rows = [
        TutorPerformanceRow(
            tutor=TutorRef(id=tutor, display_name=names.get(tutor, "")),
            **_activity(counts, participations.get(tutor, ZERO_PARTICIPATION)),
        )
        for tutor, counts in sessions.items()
        if tutor is not None
    ]
    rows.sort(key=lambda row: (row.tutor.display_name.lower(), str(row.tutor.id)))
    totals = {
        key: sum(counts[key] for counts in sessions.values()) for key in ZERO_ACTIVITY
    }
    return TutorPerformance.model_validate(
        {
            "from": start,
            "to": end,
            "tutors": rows,
            "totals": ActivityFigures(**_activity(totals, center)),
        }
    )


async def _display_names(
    session: AsyncSession, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """The users' display names. One statement, run even for no user (a
    fixed count)."""
    names = identity.display_name_columns().subquery("display_names")
    rows = await session.execute(
        select(names.c.id, names.c.display_name).where(names.c.id.in_(sorted(user_ids)))
    )
    return {row.id: row.display_name for row in rows}


async def tutor_performance(
    session: AsyncSession, *, start: date, end: date
) -> TutorPerformance:
    """``GET …/kpis/tutor-performance`` once the range is valid (K6, K7).
    Statements: the zone, the sessions, the participations, the names."""
    zone, _today = await finance._zone_and_today(session)
    low, high = instant_window(start, end, zone)
    now = clock.now()
    sessions = {
        row.tutor_user_id: {key: int(getattr(row, key)) for key in ZERO_ACTIVITY}
        for row in await session.execute(_tutor_sessions_query(low, high, now))
    }
    participations: dict[uuid.UUID, dict[str, int]] = {}
    center = dict(ZERO_PARTICIPATION)
    for row in await session.execute(_tutor_participations_query(low, high, now)):
        marks = {key: int(getattr(row, key)) for key in ZERO_PARTICIPATION}
        if row.total:
            center = marks
        else:
            participations[row.tutor_user_id] = marks
    names = await _display_names(session, (t for t in sessions if t is not None))
    return performance(
        start=start,
        end=end,
        sessions=sessions,
        participations=participations,
        center=center,
        names=names,
    )


__all__ = [
    "IN_SCOPE_STATES",
    "accrual_trend",
    "cash_trend",
    "counted_cancellation",
    "course_profitability",
    "course_rows",
    "in_scope",
    "instant_window",
    "local_midnight",
    "performance",
    "retention_report",
    "revenue_trend",
    "room_utilization",
    "student_retention",
    "tutor_performance",
    "utilization",
]
