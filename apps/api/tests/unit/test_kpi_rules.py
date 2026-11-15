"""The pure KPI rules (#126 test plan; ``_docs/kpis.md``): month buckets,
#84's percent, available minutes per room-day, the previous window and the
retention sets, plus the pure assemblies of K1 (cash), K4, K5 and K6."""

import uuid
from datetime import date, time
from decimal import Decimal

import pytest

from app.core.money import Money
from app.modules.reporting import kpi_rules, kpis
from app.modules.reporting.finance import CashMonth
from app.modules.reporting.kpi_rules import Month


def d(text: str) -> date:
    return date.fromisoformat(text)


# --- months ---------------------------------------------------------------------------


def test_months_are_clipped_to_the_range():
    assert kpi_rules.months(d("2031-01-15"), d("2031-03-10")) == [
        Month("2031-01", d("2031-01-15"), d("2031-01-31")),
        Month("2031-02", d("2031-02-01"), d("2031-02-28")),
        Month("2031-03", d("2031-03-01"), d("2031-03-10")),
    ]


def test_a_single_day_is_one_month():
    assert kpi_rules.months(d("2031-03-31"), d("2031-03-31")) == [
        Month("2031-03", d("2031-03-31"), d("2031-03-31"))
    ]


def test_months_cross_a_year_boundary():
    assert [m.month for m in kpi_rules.months(d("2030-12-20"), d("2031-01-05"))] == [
        "2030-12",
        "2031-01",
    ]


def test_366_dates_from_mid_month_are_13_buckets():
    found = kpi_rules.months(d("2031-03-15"), d("2032-03-15"))
    assert len(found) == 13
    assert (found[0].start, found[-1].end) == (d("2031-03-15"), d("2032-03-15"))
    assert found[11] == Month("2032-02", d("2032-02-01"), d("2032-02-29"))


def test_months_reject_a_reversed_range():
    with pytest.raises(ValueError):
        kpi_rules.months(d("2031-03-02"), d("2031-03-01"))


# --- percent --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (2, 3, Decimal("66.67")),
        (1, 3, Decimal("33.33")),
        (1, 8, Decimal("12.50")),
        (1, 200, Decimal("0.50")),
        (1, 400, Decimal("0.25")),
        (1, 1600, Decimal("0.06")),  # 0.0625 half-up
        (330, 3480, Decimal("9.48")),
        (210, 3180, Decimal("6.60")),
        (5, 4, Decimal("125.00")),
        (0, 5, Decimal("0.00")),
        (0, 0, None),
        (3, 0, None),
    ],
)
def test_percent_is_84s_half_up_rule(numerator, denominator, expected):
    assert kpi_rules.percent(numerator, denominator) == expected


def test_attendance_percent_counts_late_and_leaves_out_unrecorded():
    assert kpi_rules.attendance_percent(2, 1, 1) == Decimal("75.00")
    assert kpi_rules.attendance_percent(0, 0, 0) is None


# --- available minutes ----------------------------------------------------------------

HOURS = {
    1: (time(9), time(21)),
    2: (time(9), time(21)),
    3: (time(9), time(21)),
    4: (time(9), time(21)),
    5: (time(9), time(21)),
    6: (time(9), time(14)),
}
MONDAY = d("2031-03-03")
SATURDAY = d("2031-03-08")
SUNDAY = d("2031-03-09")


def test_a_room_without_windows_has_the_open_hours():
    assert kpi_rules.available_minutes(
        MONDAY, hours=HOURS, windows=[], closed=False
    ) == (720)
    assert (
        kpi_rules.available_minutes(SATURDAY, hours=HOURS, windows=[], closed=False)
        == 300
    )


def test_windows_are_clipped_to_the_open_hours():
    windows = [(1, time(16), time(20)), (6, time(8), time(10)), (6, time(13), time(15))]
    assert (
        kpi_rules.available_minutes(MONDAY, hours=HOURS, windows=windows, closed=False)
        == 240
    )
    # Sat 08:00-10:00 ∩ 09:00-14:00 = 60; 13:00-15:00 ∩ = 60.
    assert (
        kpi_rules.available_minutes(
            SATURDAY, hours=HOURS, windows=windows, closed=False
        )
        == 120
    )
    # A weekday with windows elsewhere only: nothing that day.
    assert (
        kpi_rules.available_minutes(
            d("2031-03-04"), hours=HOURS, windows=windows, closed=False
        )
        == 0
    )


def test_no_hours_row_and_a_closure_give_zero():
    assert (
        kpi_rules.available_minutes(SUNDAY, hours=HOURS, windows=[], closed=False) == 0
    )
    assert (
        kpi_rules.available_minutes(MONDAY, hours=HOURS, windows=[], closed=True) == 0
    )


def test_closed_days_are_the_covered_dates_of_the_range():
    spans = [(d("2031-02-25"), d("2031-03-02")), (d("2031-03-05"), d("2031-03-05"))]
    assert kpi_rules.closed_days(spans, d("2031-03-01"), d("2031-03-09")) == {
        d("2031-03-01"),
        d("2031-03-02"),
        d("2031-03-05"),
    }


# --- previous window and retention ---------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2031-03-05", "2031-03-05", ("2031-03-04", "2031-03-04")),
        ("2031-03-01", "2031-03-31", ("2031-01-29", "2031-02-28")),
        ("2031-01-01", "2031-12-31", ("2030-01-01", "2030-12-31")),
        ("2032-01-01", "2032-12-31", ("2030-12-31", "2031-12-31")),
    ],
)
def test_the_previous_window_has_the_same_length(start, end, expected):
    previous = kpi_rules.previous_window(d(start), d(end))
    assert previous == (d(expected[0]), d(expected[1]))
    assert (previous[1] - previous[0]) == (d(end) - d(start))


def test_retention_set_arithmetic():
    counts = kpi_rules.retention(
        {"S1", "S2", "S4", "S6", "S8"}, {"S1", "S3", "S5", "S6", "S7"}
    )
    assert counts == kpi_rules.Retention(
        active_previous=5,
        active_current=5,
        retained=2,
        new=3,
        lost=3,
        retention_percent=Decimal("40.00"),
    )
    assert kpi_rules.retention(set(), {"S1"}).retention_percent is None


# --- pure assemblies -------------------------------------------------------------


def test_cash_trend_buckets_sum_to_the_totals():
    sums = {
        d("2031-01-01"): CashMonth(d("2031-01-01"), Money("400.00"), Money("0.00")),
        d("2031-03-01"): CashMonth(d("2031-03-01"), Money("300.00"), Money("250.00")),
    }
    report = kpis.cash_trend(
        start=d("2031-01-15"),
        end=d("2031-03-10"),
        today=d("2031-03-12"),
        sums=sums,
        expenses=[],
    )
    assert [
        (m.month, str(m.revenue_total), str(m.net_result)) for m in report.months
    ] == [
        ("2031-01", "400.00", "400.00"),
        ("2031-02", "0.00", "0.00"),
        ("2031-03", "300.00", "50.00"),
    ]
    assert (str(report.totals.revenue_total), str(report.totals.net_result)) == (
        "700.00",
        "450.00",
    )


def test_utilization_orders_rooms_and_gives_null_for_nothing_available():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    report = kpis.utilization(
        start=MONDAY,
        end=SUNDAY,
        hours=HOURS,
        closed=frozenset({d("2031-03-05")}),
        rooms=[
            (b, "aula B", [(1, time(16), time(20)), (6, time(8), time(10))]),
            (a, "Aula A", []),
            (c, "Aula C", [(7, time(9), time(12))]),
        ],
        used={a: 210, b: 120, c: 30},
    )
    assert [
        (r.room.name, r.available_minutes, r.used_minutes, r.utilization_percent)
        for r in report.rooms
    ] == [
        ("Aula A", 3180, 210, Decimal("6.60")),
        ("aula B", 300, 120, Decimal("40.00")),
        ("Aula C", 0, 30, None),
    ]
    assert (report.total.available_minutes, report.total.used_minutes) == (3480, 360)


def test_retention_report_months_and_windows():
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    report = kpis.retention_report(
        start=d("2031-03-15"),
        end=d("2031-04-10"),
        previous=(d("2031-02-16"), d("2031-03-14")),
        rows=[
            (s1, d("2031-03-01"), False),
            (s1, d("2031-03-01"), True),
            (s2, d("2031-04-01"), True),
        ],
    )
    assert (report.active_previous, report.retained, report.new, report.lost) == (
        1,
        1,
        1,
        0,
    )
    assert [(m.month, m.active_students) for m in report.months] == [
        ("2031-03", 1),
        ("2031-04", 1),
    ]


def test_performance_totals_include_a_cancellation_without_a_tutor():
    t1 = uuid.uuid4()
    zero = dict(kpis.ZERO_ACTIVITY)
    report = kpis.performance(
        start=MONDAY,
        end=SUNDAY,
        sessions={
            t1: {**zero, "sessions": 1, "completed": 1, "scheduled_minutes": 60},
            None: {**zero, "cancelled": 1},
        },
        participations={
            t1: {"students": 1, "present": 1, "late": 0, "absent": 0, "not_recorded": 0}
        },
        center={"students": 1, "present": 1, "late": 0, "absent": 0, "not_recorded": 0},
        names={t1: "Tutora"},
    )
    assert [r.tutor.display_name for r in report.tutors] == ["Tutora"]
    assert (report.totals.sessions, report.totals.cancelled) == (1, 1)
    assert report.totals.attendance_percent == Decimal("100.00")
