"""Analytics KPI shapes (#126; definitions in ``_docs/kpis.md``).

Closed shapes (``extra="forbid"``). Money values are strings (#87), as in
#117; percentages are decimal strings with two places (``"66.67"``) or
``null`` for a zero denominator (#84 E6). K4, K5 and K6 have no money
field; K5 names no student; K6 names tutors only as ``{id, display_name}``
(#126 A3).
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, WithJsonSchema

from app.core.money import Money
from app.modules.reporting.schemas import ReportBasis

type KpiPercent = Annotated[
    Decimal,
    PlainSerializer(lambda value: f"{value:.2f}", return_type=str),
    WithJsonSchema({"type": "string", "format": "decimal", "examples": ["66.67"]}),
]

_CLOSED = ConfigDict(extra="forbid", populate_by_name=True)


class _Closed(BaseModel):
    model_config = _CLOSED


class _Ranged(_Closed):
    from_: date = Field(alias="from", serialization_alias="from")
    to: date


# --- K1 ---------------------------------------------------------------------------


class TrendFigures(_Closed):
    """#117 ``profitability`` figures of a range (K1)."""

    revenue_total: Money
    direct_tutor_cost_total: Money = Field(
        description="The payroll KPI: finalized payroll (accrual) or unreversed"
        " payroll payments (cash)."
    )
    gross_profit: Money
    operating_expenses_total: Money
    net_result: Money


class TrendMonth(TrendFigures):
    """One center-local month clipped to the range."""

    month: str = Field(description="YYYY-MM.", examples=["2031-01"])
    from_: date = Field(alias="from", serialization_alias="from")
    to: date


class RevenueTrend(_Ranged):
    """``GET /api/v1/reports/kpis/revenue-trend`` (K1): each month equals
    #117 ``profitability`` over that month's part of the range; ``totals``
    equals it over the whole range."""

    basis: ReportBasis
    today: date = Field(description="The center's today when it was computed.")
    months: list[TrendMonth] = Field(description="Oldest first, zeros included.")
    totals: TrendFigures


# --- K2 ---------------------------------------------------------------------------


class CohortRef(_Closed):
    id: uuid.UUID
    name: str


class CourseFigures(_Closed):
    revenue: Money
    direct_tutor_cost: Money
    gross_profit: Money = Field(description="revenue - direct_tutor_cost.")


class CohortProfitability(CourseFigures):
    cohort: CohortRef


class CourseProfitability(_Ranged):
    """``GET /api/v1/reports/kpis/course-profitability`` (K2, accrual, no
    overhead): ``Σ cohorts + not_attributed = total`` = #117's ``course``
    row."""

    today: date = Field(description="The center's today when it was computed.")
    cohorts: list[CohortProfitability] = Field(
        description="Cohorts with a non-zero revenue or cost in the range, by"
        " gross_profit descending, then cohort id."
    )
    not_attributed: CourseFigures = Field(
        description="Course-class revenue and cost tied to no cohort."
    )
    total: CourseFigures


# --- K4 ---------------------------------------------------------------------------


class RoomRef(_Closed):
    id: uuid.UUID
    name: str


class UtilizationFigures(_Closed):
    available_minutes: int
    used_minutes: int
    utilization_percent: KpiPercent | None = Field(
        description="used / available × 100 (may exceed 100.00); null when"
        " nothing is available."
    )


class RoomUtilizationRow(UtilizationFigures):
    room: RoomRef


class RoomUtilization(_Ranged):
    """``GET /api/v1/reports/kpis/room-utilization`` (K4): active rooms, by
    name (case-insensitive), then id."""

    rooms: list[RoomUtilizationRow]
    total: UtilizationFigures


# --- K5 ---------------------------------------------------------------------------


class ActiveMonth(_Closed):
    month: str = Field(description="YYYY-MM.", examples=["2031-03"])
    from_: date = Field(alias="from", serialization_alias="from")
    to: date
    active_students: int


class StudentRetention(_Ranged):
    """``GET /api/v1/reports/kpis/student-retention`` (K5): counts only."""

    previous_from: date
    previous_to: date
    active_previous: int
    active_current: int
    retained: int
    new: int
    lost: int
    retention_percent: KpiPercent | None = Field(
        description="retained / active_previous × 100; null with no previous activity."
    )
    months: list[ActiveMonth] = Field(description="The range's months, oldest first.")


# --- K6 / K7 ----------------------------------------------------------------------


class TutorRef(_Closed):
    id: uuid.UUID
    display_name: str


class AttendanceCounts(_Closed):
    present: int
    late: int
    absent: int
    not_recorded: int


class ActivityFigures(_Closed):
    sessions: int = Field(description="In-scope sessions.")
    completed: int
    pending_closeout: int = Field(description="Confirmed sessions that have ended.")
    cancelled: int = Field(description="Cancelled after a confirmation.")
    scheduled_minutes: int
    students: int = Field(description="Distinct participants of in-scope sessions.")
    attendance: AttendanceCounts
    attendance_percent: KpiPercent | None = Field(
        description="(present + late) / (present + late + absent) × 100 (#84)."
    )


class TutorPerformanceRow(ActivityFigures):
    tutor: TutorRef


class TutorPerformance(_Ranged):
    """``GET /api/v1/reports/kpis/tutor-performance`` (K6; ``totals`` is K7,
    center activity): per current tutor, by display name
    (case-insensitive), then id."""

    tutors: list[TutorPerformanceRow]
    totals: ActivityFigures


__all__ = [
    "ActiveMonth",
    "ActivityFigures",
    "AttendanceCounts",
    "CohortProfitability",
    "CohortRef",
    "CourseFigures",
    "CourseProfitability",
    "RevenueTrend",
    "RoomRef",
    "RoomUtilization",
    "RoomUtilizationRow",
    "StudentRetention",
    "TrendFigures",
    "TrendMonth",
    "TutorPerformance",
    "TutorPerformanceRow",
    "TutorRef",
    "UtilizationFigures",
]
