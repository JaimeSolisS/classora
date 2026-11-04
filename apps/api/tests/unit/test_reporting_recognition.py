"""Accrual recognition and report assembly, pure (#117 C1-C3, and the D1, D3,
D4, D5, E2 oracles on the pure assembly)."""

import random
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.money import Money
from app.modules.finance.schemas import ExpenseCategoryRef, VendorRef
from app.modules.reporting.finance import (
    CostEntry,
    ExpenseGroup,
    accrual_profitability,
    class_rows,
    deferred_revenue,
    revenue_entries,
)
from app.modules.reporting.recognition import (
    Consumption,
    Occurrence,
    Share,
    cost_class,
    course_shares,
    dated_shares,
    deferred_bucket,
    package_shares,
    revenue_class,
)

ZONE = ZoneInfo("America/Mexico_City")


def m(text: str) -> Money:
    return Money(text)


def d(month: int, day: int) -> date:
    return date(2026, month, day)


def at(month: int, day: int, hour: int = 16, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=ZONE)


def consumption(month: int, day: int, units: int = 1, key: str = "") -> Consumption:
    start = at(month, day)
    return Consumption(starts_at=start, on=start.date(), units=units, order_key=key)


def amounts(shares: list[Share]) -> list[tuple[date | None, str]]:
    return [(s.on, str(s.amount)) for s in shares]


# --- C1 packages -------------------------------------------------------------------


def test_pk1_three_sessions_use_the_prefix_property():
    shares = package_shares(
        amount=m("1000.00"),
        net=m("1000.00"),
        units_granted=3,
        consumptions=[consumption(11, 18), consumption(10, 28), consumption(11, 4)],
        expired_on=None,
        closed_on=None,
    )
    assert amounts(shares) == [
        (d(10, 28), "333.33"),
        (d(11, 4), "333.34"),
        (d(11, 18), "333.33"),
        (None, "0.00"),
    ]


def test_pk2_breakage_on_expires_on():
    shares = package_shares(
        amount=m("1100.00"),
        net=m("1100.00"),
        units_granted=4,
        consumptions=[consumption(10, 29)],
        expired_on=d(11, 2),
        closed_on=None,
    )
    assert amounts(shares) == [(d(10, 29), "275.00"), (d(11, 2), "825.00")]


@pytest.mark.parametrize(
    ("credit", "residual"), [("-500.00", "175.00"), ("-675.00", "0.00")]
)
def test_pk3_cancellation_recognizes_remainder_plus_credit(credit, residual):
    shares = package_shares(
        amount=m("900.00"),
        net=m("900.00") + m(credit),
        units_granted=4,
        consumptions=[consumption(11, 6)],
        expired_on=None,
        closed_on=d(11, 20),
    )
    assert amounts(shares) == [(d(11, 6), "225.00"), (d(11, 20), residual)]


def test_pk4_unused_package_is_deferred():
    shares = package_shares(
        amount=m("600.00"),
        net=m("600.00"),
        units_granted=4,
        consumptions=[],
        expired_on=None,
        closed_on=None,
    )
    assert amounts(shares) == [(None, "600.00")]


def test_default_cancellation_credit_after_one_of_three_leaves_zero():
    credit = m("1000.00").prorate(2, 3)
    assert str(credit) == "666.67"
    shares = package_shares(
        amount=m("1000.00"),
        net=m("1000.00") - credit,
        units_granted=3,
        consumptions=[consumption(11, 4)],
        expired_on=None,
        closed_on=d(11, 10),
    )
    assert amounts(shares) == [(d(11, 4), "333.33"), (d(11, 10), "0.00")]


def test_consumptions_order_by_session_start_then_transaction():
    same = at(11, 4)
    shares = package_shares(
        amount=m("1000.00"),
        net=m("1000.00"),
        units_granted=3,
        consumptions=[
            Consumption(same, same.date(), 1, "b"),
            Consumption(same - timedelta(days=7), d(10, 28), 1, "z"),
            Consumption(same, same.date(), 1, "a"),
        ],
        expired_on=None,
        closed_on=None,
    )
    assert [str(s.amount) for s in shares] == ["333.33", "333.34", "333.33", "0.00"]


# --- C2 courses --------------------------------------------------------------------

COHORT = [
    Occurrence(at(11, 6), d(11, 6)),
    Occurrence(at(11, 13), d(11, 13)),
    # Nov 20 is cancelled: owner_schedules never returns it.
    Occurrence(at(11, 27), d(11, 27)),
    Occurrence(at(12, 4), d(12, 4)),
]


def test_cf1_late_enrollment_window():
    shares = course_shares(
        net=m("1000.00"),
        occurrences=COHORT,
        enrolled_at=at(11, 10, 12),
        withdrawn_at=None,
        service_date=d(11, 10),
    )
    assert amounts(shares) == [
        (d(11, 13), "333.33"),
        (d(11, 27), "333.34"),
        (d(12, 4), "333.33"),
    ]


def test_cf2_withdrawal_window_and_net():
    shares = course_shares(
        net=m("600.00"),
        occurrences=COHORT,
        enrolled_at=at(11, 1, 9),
        withdrawn_at=at(11, 15, 10),
        service_date=d(11, 1),
    )
    assert amounts(shares) == [(d(11, 6), "300.00"), (d(11, 13), "300.00")]


def test_cf2_before_withdrawal_spreads_over_four():
    shares = course_shares(
        net=m("1200.00"),
        occurrences=COHORT,
        enrolled_at=at(11, 1, 9),
        withdrawn_at=None,
        service_date=d(11, 1),
    )
    assert [str(s.amount) for s in shares] == ["300.00"] * 4


def test_withdrawal_at_the_start_instant_counts_the_occurrence():
    shares = course_shares(
        net=m("100.00"),
        occurrences=COHORT,
        enrolled_at=at(11, 1),
        withdrawn_at=at(11, 6),
        service_date=d(11, 1),
    )
    assert amounts(shares) == [(d(11, 6), "100.00")]


def test_empty_window_recognizes_on_service_date():
    shares = course_shares(
        net=m("750.00"),
        occurrences=COHORT,
        enrolled_at=at(12, 5),
        withdrawn_at=None,
        service_date=d(12, 5),
    )
    assert amounts(shares) == [(d(12, 5), "750.00")]


# --- C3 properties -----------------------------------------------------------------


def test_package_properties_hold_for_random_sequences():
    rng = random.Random(117)  # noqa: S311 - a seeded property test
    for _ in range(500):
        granted = rng.randint(1, 12)
        amount = Money(f"{rng.randint(0, 500000) / 100:.2f}")
        used = rng.randint(0, granted)
        units = []
        while sum(units) < used:
            units.append(rng.randint(1, used - sum(units)))
        base = at(10, 1)
        items = [
            Consumption(base + timedelta(days=i), (base + timedelta(days=i)).date(), u)
            for i, u in enumerate(units)
        ]
        end = rng.choice(["open", "expired", "closed"])
        credit = Money.zero()
        if end == "closed":
            # The default credit, or an override from 0.00 to the net.
            credit = rng.choice(
                [amount.prorate(granted - used, granted), amount, Money.zero()]
            )
        net = amount - credit
        shares = package_shares(
            amount=amount,
            net=net,
            units_granted=granted,
            consumptions=items,
            expired_on=d(12, 1) if end == "expired" else None,
            closed_on=d(12, 2) if end == "closed" else None,
        )
        assert Money.total(s.amount for s in shares) == net
        cumulative = 0
        running = Money.zero()
        for item, share in zip(items, shares, strict=False):
            cumulative += item.units
            running = running + share.amount
            assert running == amount.prorate(cumulative, granted)
            assert not share.amount.is_negative()
        residual = shares[-1].amount
        if residual.is_negative():
            # Only an over-credited cancellation goes below zero.
            assert end == "closed"
            assert credit > amount - amount.prorate(used, granted)


def test_course_properties_hold_for_random_windows():
    rng = random.Random(1170)  # noqa: S311 - a seeded property test
    starts = [
        Occurrence(datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=7 * i), d(9, 1))
        for i in range(20)
    ]
    for _ in range(500):
        net = Money(f"{rng.randint(0, 900000) / 100:.2f}")
        enrolled = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(
            hours=rng.randint(0, 3000)
        )
        withdrawn = (
            None
            if rng.random() < 0.5
            else enrolled + timedelta(hours=rng.randint(0, 2000))
        )
        shares = course_shares(
            net=net,
            occurrences=rng.sample(starts, len(starts)),
            enrolled_at=enrolled,
            withdrawn_at=withdrawn,
            service_date=d(9, 1),
        )
        assert Money.total(s.amount for s in shares) == net
        assert all(not s.amount.is_negative() for s in shares)


# --- E7, E8 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "session_kind", "tuition", "expected"),
    [
        ("session", "individual", None, "individual"),
        ("session", "group", None, "group"),
        ("package_purchase", None, None, "individual"),
        ("group_enrollment", None, None, "group"),
        ("cohort_enrollment", None, None, "course"),
        ("tuition_period", None, "group", "group"),
        ("manual", None, None, "unallocated"),
    ],
)
def test_revenue_class(kind, session_kind, tuition, expected):
    assert (
        revenue_class(kind, session_kind=session_kind, tuition_class_type=tuition)
        == expected
    )


def test_a_session_charge_follows_its_current_session_date():
    assert dated_shares(
        kind="session", net=m("350.00"), service_date=d(10, 30), session_on=d(11, 2)
    ) == [Share(d(11, 2), m("350.00"))]
    assert dated_shares(
        kind="manual", net=m("250.00"), service_date=d(11, 12), session_on=None
    ) == [Share(d(11, 12), m("250.00"))]


def adjustment_class(**given: str | None) -> str:
    values: dict[str, str | None] = {
        "line_kind": "adjustment",
        "line_class_kind": None,
        "adjustment_kind": None,
        "adjustment_class_kind": None,
        "origin_line_kind": None,
        "origin_class_kind": None,
        "adjustment_session_kind": None,
    }
    values.update(given)
    return cost_class(**values)  # type: ignore[arg-type]


def test_cost_classes_follow_e8():
    assert adjustment_class(line_kind="no_show", line_class_kind="group") == "group"
    assert adjustment_class(line_kind="salary") == "unallocated"
    assert adjustment_class(line_kind="pay_item") == "unallocated"
    assert (
        adjustment_class(
            adjustment_kind="late_approval", adjustment_class_kind="course"
        )
        == "course"
    )
    assert (
        adjustment_class(
            adjustment_kind="hours_correction", adjustment_class_kind="individual"
        )
        == "individual"
    )
    assert (
        adjustment_class(
            adjustment_kind="manual",
            origin_line_kind="session",
            origin_class_kind="individual",
        )
        == "individual"
    )
    assert (
        adjustment_class(adjustment_kind="manual", origin_line_kind="salary")
        == "unallocated"
    )
    assert (
        adjustment_class(adjustment_kind="manual", adjustment_session_kind="group")
        == "group"
    )
    assert adjustment_class(adjustment_kind="manual") == "unallocated"
    assert adjustment_class(adjustment_kind="carry_forward") == "unallocated"


# --- The D fixture on the pure assembly ----------------------------------------------

RENT = ExpenseCategoryRef(
    id=uuid.UUID(int=1), name="Renta", system_key="rent", is_active=True
)
UTILITIES = ExpenseCategoryRef(
    id=uuid.UUID(int=2), name="Servicios", system_key="utilities", is_active=True
)
FURNITURE = ExpenseCategoryRef(
    id=uuid.UUID(int=3), name="Mobiliario", system_key=None, is_active=False
)
SUPPLIES = ExpenseCategoryRef(
    id=uuid.UUID(int=4), name="Papelería", system_key="supplies", is_active=True
)
OLD_VENDOR = VendorRef(id=uuid.UUID(int=9), name="Proveedor inactivo", is_active=False)


def fixture_revenue(*, cf2_withdrawn: bool = True):
    def charge(kind, shares, session_kind=None, tuition=None):
        return revenue_entries(
            revenue_class(kind, session_kind=session_kind, tuition_class_type=tuition),
            deferred_bucket(kind),
            shares,
        )

    def session(on, amount, kind="individual", service_date=None):
        return charge(
            "session",
            dated_shares(
                kind="session",
                net=m(amount),
                service_date=service_date or on,
                session_on=on,
            ),
            session_kind=kind,
        )

    entries = [
        *session(d(11, 3), "350.00"),  # R1
        *session(d(11, 2), "350.00", service_date=d(10, 30)),  # R2
        *session(d(11, 5), "0.00"),  # R3 credited
        *session(d(11, 10), "162.00", kind="group"),  # R4
        *charge("group_enrollment", [Share(d(11, 2), m("900.00"))]),  # R5
        *charge(
            "tuition_period", [Share(d(11, 15), m("1600.00"))], tuition="individual"
        ),
        *charge("manual", [Share(d(11, 12), m("250.00"))]),  # R7
        *session(d(12, 15), "350.00"),  # R8
        # R9: Nov 30 23:30 local is Dec 1 05:30 UTC; dated by the local date.
        *session(at(11, 30, 23, 30).astimezone(ZONE).date(), "300.00"),
    ]
    entries += charge(
        "package_purchase",
        package_shares(
            amount=m("1000.00"),
            net=m("1000.00"),
            units_granted=3,
            consumptions=[consumption(10, 28), consumption(11, 4), consumption(11, 18)],
            expired_on=None,
            closed_on=None,
        ),
    )
    entries += charge(
        "package_purchase",
        package_shares(
            amount=m("1100.00"),
            net=m("1100.00"),
            units_granted=4,
            consumptions=[consumption(10, 29)],
            expired_on=d(11, 2),
            closed_on=None,
        ),
    )
    entries += charge(
        "package_purchase",
        package_shares(
            amount=m("900.00"),
            net=m("400.00"),
            units_granted=4,
            consumptions=[consumption(11, 6)],
            expired_on=None,
            closed_on=d(11, 20),
        ),
    )
    entries += charge(
        "package_purchase",
        package_shares(
            amount=m("600.00"),
            net=m("600.00"),
            units_granted=4,
            consumptions=[],
            expired_on=None,
            closed_on=None,
        ),
    )
    entries += charge(
        "cohort_enrollment",
        course_shares(
            net=m("1000.00"),
            occurrences=COHORT,
            enrolled_at=at(11, 10, 12),
            withdrawn_at=None,
            service_date=d(11, 10),
        ),
    )
    entries += charge(
        "cohort_enrollment",
        course_shares(
            net=m("600.00") if cf2_withdrawn else m("1200.00"),
            occurrences=COHORT,
            enrolled_at=at(11, 1, 9),
            withdrawn_at=at(11, 15, 10) if cf2_withdrawn else None,
            service_date=d(11, 1),
        ),
    )
    return entries


def fixture_costs():
    nov_end = d(11, 30)

    def line(on, amount, **kind):
        return CostEntry(on, adjustment_class(**kind), m(amount))  # type: ignore[arg-type]

    return [
        line(d(10, 28), "300.00", line_kind="session", line_class_kind="individual"),
        line(d(11, 3), "300.00", line_kind="session", line_class_kind="individual"),
        line(d(11, 10), "250.00", line_kind="session", line_class_kind="group"),
        line(d(11, 12), "150.00", line_kind="no_show", line_class_kind="individual"),
        line(
            nov_end,
            "400.00",
            adjustment_kind="late_approval",
            adjustment_class_kind="course",
        ),
        line(nov_end, "-100.00", adjustment_kind="manual"),
        line(
            nov_end,
            "50.00",
            adjustment_kind="manual",
            origin_line_kind="session",
            origin_class_kind="individual",
        ),
        line(
            nov_end, "80.00", adjustment_kind="manual", adjustment_session_kind="group"
        ),
        line(nov_end, "3000.00", line_kind="salary"),
        line(nov_end, "500.00", line_kind="pay_item"),
        line(nov_end, "-200.00", line_kind="pay_item"),
    ]


def november_expenses(*, x2_adjusted: bool = True):
    return [
        ExpenseGroup(RENT, None, m("1500.00")),
        ExpenseGroup(UTILITIES, None, m("1350.00") if x2_adjusted else m("1200.00")),
        ExpenseGroup(SUPPLIES, None, m("0.00")),
        ExpenseGroup(FURNITURE, OLD_VENDOR, m("500.00")),
    ]


def rows_of(report):
    return {
        r.class_type: (str(r.revenue), str(r.direct_tutor_cost), str(r.gross_profit))
        for r in report.class_rows
    }


def test_d1_accrual_november():
    report = accrual_profitability(
        start=d(11, 1),
        end=d(11, 30),
        today=d(12, 10),
        revenue=fixture_revenue(),
        costs=fixture_costs(),
        expenses=november_expenses(),
    )
    assert [r.class_type for r in report.class_rows] == [
        "individual",
        "group",
        "course",
        "unallocated",
    ]
    assert rows_of(report) == {
        "individual": ("4491.67", "500.00", "3991.67"),
        "group": ("1062.00", "330.00", "732.00"),
        "course": ("1266.67", "400.00", "866.67"),
        "unallocated": ("250.00", "3200.00", "-2950.00"),
    }
    assert str(report.revenue_total) == "7070.34"
    assert str(report.direct_tutor_cost_total) == "4430.00"
    assert str(report.gross_profit) == "2640.34"
    expenses = report.operating_expenses
    assert str(expenses.total) == "3350.00"
    assert [(c.category.name, str(c.amount)) for c in expenses.by_category] == [
        ("Renta", "1500.00"),
        ("Servicios", "1350.00"),
        ("Mobiliario", "500.00"),
    ]
    assert [
        (None if v.vendor is None else v.vendor.name, str(v.amount))
        for v in expenses.by_vendor
    ] == [(None, "2850.00"), ("Proveedor inactivo", "500.00")]
    assert str(report.net_result) == "-709.66"


def test_d3_restatement_before_and_after():
    before = accrual_profitability(
        start=d(11, 1),
        end=d(11, 30),
        today=d(12, 10),
        revenue=fixture_revenue(cf2_withdrawn=False),
        costs=fixture_costs(),
        expenses=november_expenses(x2_adjusted=False),
    )
    assert rows_of(before)["course"][0] == "1566.67"
    assert str(before.operating_expenses.total) == "3200.00"
    october = class_rows(fixture_revenue(), fixture_costs(), d(10, 1), d(10, 31))
    assert Money.total(r.direct_tutor_cost for r in october) == m("300.00")


def test_d4_reconciliation_revenue_and_deferred():
    revenue = fixture_revenue()
    total_net = Money.total(e.amount for e in revenue)
    assert str(total_net) == "8962.00"
    by_month = [
        Money.total(r.revenue for r in class_rows(revenue, [], start, end))
        for start, end in [
            (d(10, 1), d(10, 31)),
            (d(11, 1), d(11, 30)),
            (d(12, 1), d(12, 10)),
        ]
    ]
    assert [str(x) for x in by_month] == ["608.33", "7070.34", "333.33"]
    all_time = Money.total(
        r.revenue for r in class_rows(revenue, [], d(10, 1), d(12, 10))
    )
    assert str(all_time) == "8012.00"
    deferred = deferred_revenue(revenue, d(12, 10))
    assert str(deferred.total) == "950.00"
    assert all_time + deferred.total == total_net
    payroll = Money.total(
        r.direct_tutor_cost
        for r in class_rows([], fixture_costs(), d(10, 1), d(12, 10))
    )
    assert str(payroll) == "4730.00"


def test_d5_boundaries():
    revenue = fixture_revenue()
    one_day = {
        r.class_type: str(r.revenue)
        for r in class_rows(revenue, [], d(11, 2), d(11, 2))
    }
    assert one_day == {
        "individual": "1175.00",
        "group": "900.00",
        "course": "0.00",
        "unallocated": "0.00",
    }
    december = class_rows(revenue, [], d(12, 1), d(12, 10))
    assert {r.class_type: str(r.revenue) for r in december}["individual"] == "0.00"
    empty = class_rows(revenue, fixture_costs(), d(11, 25), d(11, 25))
    assert [
        (r.class_type, str(r.revenue), str(r.direct_tutor_cost)) for r in empty
    ] == [
        ("individual", "0.00", "0.00"),
        ("group", "0.00", "0.00"),
        ("course", "0.00", "0.00"),
        ("unallocated", "0.00", "0.00"),
    ]
    # A session on Oct 31 23:30 local (Nov 1 05:30 UTC) is in October.
    assert at(10, 31, 23, 30).astimezone(UTC).date() == d(11, 1)
    assert at(10, 31, 23, 30).astimezone(ZONE).date() == d(10, 31)


def test_e2_deferred_revenue_split():
    deferred = deferred_revenue(fixture_revenue(), d(12, 10))
    assert (
        str(deferred.packages),
        str(deferred.scheduled_sessions),
        str(deferred.courses),
        str(deferred.other),
        str(deferred.total),
    ) == ("600.00", "350.00", "0.00", "0.00", "950.00")
