"""K1 and K2 against their worked examples and #117 (#126 B1, B2, E5, E6;
``_docs/kpis.md``).

B1 and B2 each have a database of their own. The identities (each K1 month
equals a direct ``profitability_report`` call for that month's part of the
range, the totals equal the whole-range call, and the K2 total equals the
accrual ``course`` row) are also checked on #117's own reconciliation
scenario, whose module-scoped fixture is imported from
``test_reporting_finance_reconciliation.py`` (it builds on this module's
database)."""

from datetime import date
from typing import Any

import pytest

from app.core.money import Money
from app.modules.reporting import finance as reports
from app.modules.reporting import kpi_rules, kpis
from tests.conftest import frozen_clock
from tests.integration import kpi_support as ks
from tests.integration.lifecycle_support import in_uow
from tests.integration.test_reporting_finance_reconciliation import (  # noqa: F401
    TODAY,
    client,
    finance_staff,
    frozen,
    owner,
    scenario,
)

FIELDS = (
    "revenue_total",
    "direct_tutor_cost_total",
    "gross_profit",
    "operating_expenses_total",
    "net_result",
)


def trend(url: str, basis: str, start: date, end: date) -> Any:
    return in_uow(
        url, lambda s: kpis.revenue_trend(s, basis=basis, start=start, end=end)
    )


def course(url: str, start: date, end: date) -> Any:
    return in_uow(url, lambda s: kpis.course_profitability(s, start=start, end=end))


def profitability(url: str, basis: str, start: date, end: date) -> Any:
    return in_uow(
        url,
        lambda s: reports.profitability_report(s, basis=basis, start=start, end=end),
    )


def figures_of(item: Any) -> tuple[str, ...]:
    return tuple(str(getattr(item, name)) for name in FIELDS)


def report_figures(report: Any) -> tuple[str, ...]:
    return (
        str(report.revenue_total),
        str(report.direct_tutor_cost_total),
        str(report.gross_profit),
        str(report.operating_expenses.total),
        str(report.net_result),
    )


def assert_k1_identity(url: str, basis: str, start: date, end: date) -> Any:
    """Every month equals #117 over its part of the range; the totals equal
    #117 over the whole range."""
    found = trend(url, basis, start, end)
    buckets = kpi_rules.months(start, end)
    assert [(m.month, m.from_, m.to) for m in found.months] == [
        (b.month, b.start, b.end) for b in buckets
    ]
    for month in found.months:
        direct = profitability(url, basis, month.from_, month.to)
        assert figures_of(month) == report_figures(direct), month.month
    assert figures_of(found.totals) == report_figures(
        profitability(url, basis, start, end)
    )
    return found


def course_row(url: str, start: date, end: date) -> tuple[str, str, str]:
    report = profitability(url, "accrual", start, end)
    [row] = [r for r in report.class_rows if r.class_type == "course"]
    return str(row.revenue), str(row.direct_tutor_cost), str(row.gross_profit)


def course_figures(item: Any) -> tuple[str, str, str]:
    return str(item.revenue), str(item.direct_tutor_cost), str(item.gross_profit)


# --- B1 ---------------------------------------------------------------------------

B1_EXPECTED = {
    "accrual": [
        ("2031-01", ("400.00", "0.00", "400.00", "0.00", "400.00")),
        ("2031-02", ("400.00", "250.00", "150.00", "1000.00", "-850.00")),
        ("2031-03", ("0.00", "0.00", "0.00", "0.00", "0.00")),
        ("totals", ("800.00", "250.00", "550.00", "1000.00", "-450.00")),
    ],
    "cash": [
        ("2031-01", ("400.00", "0.00", "400.00", "0.00", "400.00")),
        ("2031-02", ("0.00", "0.00", "0.00", "1000.00", "-1000.00")),
        ("2031-03", ("300.00", "250.00", "50.00", "0.00", "50.00")),
        ("totals", ("700.00", "250.00", "450.00", "1000.00", "-550.00")),
    ],
}


def test_b1_revenue_trend_worked_example(seeded_url):
    url = seeded_url
    ks.build_b1(url)
    with frozen_clock(ks.B1_NOW):
        for basis, expected in B1_EXPECTED.items():
            found = assert_k1_identity(url, basis, *ks.B1_RANGE)
            assert [(m.month, figures_of(m)) for m in found.months] + [
                ("totals", figures_of(found.totals))
            ] == expected, basis
            assert [(m.from_, m.to) for m in found.months] == [
                (date(2031, 1, 15), date(2031, 1, 31)),
                (date(2031, 2, 1), date(2031, 2, 28)),
                (date(2031, 3, 1), date(2031, 3, 10)),
            ]
            assert found.today == date(2031, 3, 12)
            body = found.model_dump(mode="json", by_alias=True)
            assert body["months"][1]["net_result"] == expected[1][1][4]
            assert (body["from"], body["to"], body["basis"]) == (
                "2031-01-15",
                "2031-03-10",
                basis,
            )
        # A single day and a range inside one month.
        assert_k1_identity(url, "accrual", date(2031, 2, 3), date(2031, 2, 3))
        assert_k1_identity(url, "cash", date(2031, 3, 1), date(2031, 3, 10))


# --- B2 ---------------------------------------------------------------------------


def test_b2_course_profitability_worked_example(seeded_url):
    url = seeded_url
    made = ks.build_b2(url)
    with frozen_clock(ks.B2_NOW):
        found = course(url, *ks.B2_RANGE)
        assert [
            (row.cohort.id, row.cohort.name, course_figures(row))
            for row in found.cohorts
        ] == [
            (made.c2, made.c2_name, ("666.67", "0.00", "666.67")),
            (made.c1, made.c1_name, ("1000.00", "750.00", "250.00")),
        ]
        assert course_figures(found.not_attributed) == ("800.00", "0.00", "800.00")
        assert course_figures(found.total) == ("2466.67", "750.00", "1716.67")
        assert course_figures(found.total) == course_row(url, *ks.B2_RANGE)
        # February: C1's two occurrences (1,000.00), no cost in a February
        # period (the late approval is dated on March's end).
        february = course(url, date(2031, 2, 1), date(2031, 2, 28))
        assert [(r.cohort.id, course_figures(r)) for r in february.cohorts] == [
            (made.c1, ("1000.00", "0.00", "1000.00"))
        ]
        assert course_figures(february.total) == course_row(
            url, date(2031, 2, 1), date(2031, 2, 28)
        )
        # A range with nothing: no cohorts and a zero total.
        empty = course(url, date(2031, 1, 1), date(2031, 1, 31))
        assert empty.cohorts == []
        assert course_figures(empty.total) == ("0.00", "0.00", "0.00")
        # K1 on the same data still matches #117.
        assert_k1_identity(url, "accrual", date(2031, 2, 1), date(2031, 4, 2))


# --- Identities on #117's reconciliation scenario ----------------------------------


@pytest.mark.usefixtures("scenario")
def test_k1_identity_on_the_117_scenario_both_bases(module_seeded_url):
    url = module_seeded_url
    start = date(2026, 10, 1)
    for basis in ("accrual", "cash"):
        found = assert_k1_identity(url, basis, start, TODAY)
        assert [m.month for m in found.months] == ["2026-10", "2026-11", "2026-12"]
    # Mid-month bounds, as the restated November of #117 D3.
    assert_k1_identity(url, "accrual", date(2026, 10, 20), date(2026, 11, 25))
    assert_k1_identity(url, "cash", date(2026, 10, 20), date(2026, 11, 25))


@pytest.mark.usefixtures("scenario")
def test_k2_identity_on_the_117_scenario(module_seeded_url):
    url = module_seeded_url
    found = course(url, date(2026, 10, 1), TODAY)
    assert course_figures(found.total) == course_row(url, date(2026, 10, 1), TODAY)
    sums = [found.not_attributed, *found.cohorts]
    assert Money.total(x.revenue for x in sums) == found.total.revenue
    assert (
        Money.total(x.direct_tutor_cost for x in sums) == found.total.direct_tutor_cost
    )
