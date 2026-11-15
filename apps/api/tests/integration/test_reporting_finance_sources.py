"""Accrual dating on rows written by the billing and package workflows (#117
D1 R2/R9/PK1, D5, F1, and the handoff's mutation targets): a session charge
follows its session's **current** local date, a session at 23:30 local on the
last of the month stays in that month, and a package consumption approved in
November for an October session is recognized in October."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.schemas import ChargeDraft
from app.modules.reporting import finance as reports
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import package_support as ps
from tests.integration import session_charge_support as scs
from tests.integration.billing_ledger_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

NOW = datetime(2026, 12, 10, 18, 0, tzinfo=UTC)


def d(month: int, day: int) -> date:
    return date(2026, month, day)


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


def individual(url: str, start: date, end: date) -> str:
    report = in_uow(
        url,
        lambda s: reports.profitability_report(
            s, basis="accrual", start=start, end=end
        ),
    )
    [row] = [r for r in report.class_rows if r.class_type == "individual"]
    return str(row.revenue)


def _package(url: str, sessions: list[tuple[date, date]]) -> uuid.UUID:
    """A 3-session package of 1000.00 sold on Oct 20; each (session day,
    consumed on) is consumed with the clock on its second date."""
    w = ps.world(url)
    package = ps.sell(
        url, w.student, ps.product(url, quantity=3, amount="1000.00"), d(10, 20)
    )
    for day, consumed_on in sessions:
        session = ps.session_for(url, w, day)
        with frozen_clock(ps.local(consumed_on, 12)):
            assert ps.consume(url, session, w.student).outcome == "consumed"
    return package


def _session_charge(url: str, starts_at: datetime, amount: str) -> Any:
    """A session with one participant and its ``session`` charge, posted by
    ``post_charge`` for the session's local date."""
    item = scs.item(url, starts_at=starts_at, price=None)
    draft = ChargeDraft(
        student_id=item.student,
        kind="session",
        class_session_id=item.session,
        service_date=starts_at.astimezone(scs.ZONE).date(),
        list_amount=Money(amount),
        due_date_base=starts_at.astimezone(scs.ZONE).date(),
        scheduled_minutes=60,
    )
    in_uow(url, lambda s: billing.post_charge(s, draft))
    [charge] = scs.charges_of(url, item.session)
    return item, charge


@pytest.fixture(scope="module")
def scenario(module_seeded_url: str, frozen: FrozenClock) -> dict[str, Any]:
    url = module_seeded_url
    scs.set_floor(url, d(10, 1))
    # PK1: Oct 28 consumed on Nov 2, then Nov 4 and Nov 18.
    package = _package(
        url, [(d(10, 28), d(11, 2)), (d(11, 4), d(11, 5)), (d(11, 18), d(11, 19))]
    )
    # R2: charged for Oct 30, then rescheduled to Nov 2.
    r2, r2_charge = _session_charge(url, scs.local(d(10, 30)), "350.00")
    assert r2_charge.service_date == d(10, 30)
    run_sql(
        url,
        "UPDATE class_sessions SET starts_at = starts_at + interval '3 days',"
        " ends_at = ends_at + interval '3 days' WHERE id = :id",
        {"id": r2.session},
    )
    # R9: Nov 30 23:30 local (Dec 1 05:30 UTC).
    r9, r9_charge = _session_charge(url, scs.local(d(11, 30), 23, 30), "300.00")
    assert r9_charge.service_date == d(11, 30)
    return {"package": package, "r2": r2, "r9": r9}


def test_accrual_dates_follow_the_current_session_and_the_local_day(
    module_seeded_url, scenario
):
    url = module_seeded_url
    assert individual(url, d(10, 1), d(10, 31)) == "333.33"
    assert individual(url, d(11, 1), d(11, 30)) == "1316.67"
    assert individual(url, d(12, 1), d(12, 10)) == "0.00"
    # R2 is on Nov 2 now; the Oct 28 consumption was approved that day.
    assert individual(url, d(11, 2), d(11, 2)) == "350.00"
    assert individual(url, d(10, 28), d(10, 28)) == "333.33"
    assert individual(url, d(11, 30), d(11, 30)) == "300.00"


def test_revenue_and_deferred_reconcile_with_the_charge_nets(
    module_seeded_url, scenario
):
    url = module_seeded_url
    balances = in_uow(url, reports.balances_report)
    assert str(balances.deferred_revenue.total) == "0.00"
    [total] = run_sql(
        url,
        "SELECT sum(c.amount + coalesce((SELECT sum(a.amount) FROM charge_adjustments a"
        " WHERE a.charge_id = c.id), 0)) AS net FROM charges c",
    )
    recognized = individual(url, d(10, 1), d(12, 10))
    assert recognized == str(total.net) == "1650.00"


def test_an_unused_package_and_a_future_session_are_deferred(
    module_seeded_url, scenario
):
    url = module_seeded_url
    before = in_uow(url, reports.balances_report).deferred_revenue
    counts_before = {
        "accrual": count_statements(
            url,
            lambda s: reports.profitability_report(
                s, basis="accrual", start=d(10, 1), end=d(12, 10)
            ),
        )[0],
        "balances": count_statements(url, reports.balances_report)[0],
    }
    w = ps.world(url)
    ps.sell(url, w.student, ps.product(url, quantity=4, amount="600.00"), d(11, 1))
    _session_charge(url, scs.local(d(12, 15)), "350.00")
    after = in_uow(url, reports.balances_report).deferred_revenue
    assert str(after.packages - before.packages) == "600.00"
    assert str(after.scheduled_sessions - before.scheduled_sessions) == "350.00"
    assert str(after.total - before.total) == "950.00"
    # F1: more packages, sessions and charges, the same statements.
    for _ in range(3):
        _package(url, [(d(11, 9), d(11, 10))])
    counts_after = {
        "accrual": count_statements(
            url,
            lambda s: reports.profitability_report(
                s, basis="accrual", start=d(10, 1), end=d(12, 10)
            ),
        )[0],
        "balances": count_statements(url, reports.balances_report)[0],
    }
    assert counts_after == counts_before
