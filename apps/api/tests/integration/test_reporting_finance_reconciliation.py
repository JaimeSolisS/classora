"""Financial reports against rows written by their owning modules (#117 A3,
D1-D4 on the SQL side, E1, E3, F1, F2).

One scenario per module database, built once through the owners' service
functions and routes (charges by ``post_charge``, payments, reversals and
refunds by the billing service, expenses by ``insert_expense`` and the expense
payment and adjustment functions, payroll finalized and paid through its
routes). The clock is frozen at :data:`NOW` (the center's today is
2026-12-07, ``America/Mexico_City``). The full centavo oracle of the issue's D
fixture (packages, cohorts, sessions, payroll adjustments) is pinned on the
pure assembly in ``tests/unit/test_reporting_recognition.py``; this file
proves the queries feed it the owners' figures.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.models import BillingAccount
from app.modules.billing.schemas import AllocationLine, AllocationRequest, RefundDraft
from app.modules.finance import service as finance
from app.modules.finance.schemas import (
    ExpenseAdjustmentCreate,
    ExpensePaymentCreate,
    ExpensePaymentReversalCreate,
)
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll import service as payroll
from app.modules.reporting import finance as reports
from tests.conftest import FrozenClock, frozen_clock
from tests.factories import billing as factories
from tests.integration import receipt_support as rs
from tests.integration.auth_support import auth_client, create_user, signed_headers
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.corrections_support import Staff, staff
from tests.integration.expense_support import category_of, new_vendor
from tests.integration.lifecycle_support import in_uow
from tests.integration.payroll_payment_support import (
    NOW,
    body,
    finalized_period,
    pay,
    reverse,
    worked_period,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import prepare

PROFITABILITY = "/api/v1/reports/finance/profitability"
BALANCES = "/api/v1/reports/finance/balances"
TODAY = date(2026, 12, 7)


def d(month: int, day: int) -> date:
    return date(2026, month, day)


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance_staff(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


def get(client: TestClient, who: Staff, path: str, **params: str) -> dict[str, Any]:
    client.cookies.clear()
    response = client.get(path, params=params, headers=signed_headers(who.cookie))
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def accrual(client: TestClient, who: Staff, start: date, end: date) -> dict[str, Any]:
    return get(
        client, who, PROFITABILITY, basis="accrual", **{"from": str(start)}, to=str(end)
    )


def cash(client: TestClient, who: Staff, start: date, end: date) -> dict[str, Any]:
    return get(
        client, who, PROFITABILITY, basis="cash", **{"from": str(start)}, to=str(end)
    )


@dataclass
class Scenario:
    account: uuid.UUID
    period: uuid.UUID
    before: dict[str, Any] = field(default_factory=dict)


def _run(
    url: str, actor: uuid.UUID, call: Callable[[AsyncSession], Awaitable[Any]]
) -> Any:
    return in_uow(url, call, actor_id=actor)


def _expense(
    url: str,
    actor: uuid.UUID,
    category: Any,
    amount: str,
    incurred: date,
    due: date,
    vendor: Any = None,
) -> uuid.UUID:
    async def _insert(session: AsyncSession) -> uuid.UUID:
        made = await finance.insert_expense(
            session,
            category_id=uuid.UUID(str(category)),
            vendor_id=None if vendor is None else uuid.UUID(str(vendor)),
            description="Gasto de prueba",
            amount=Money(amount),
            incurred_on=incurred,
            due_on=due,
            receipt_reference=None,
            note=None,
            created_by_user_id=actor,
        )
        return made.id

    return _run(url, actor, _insert)


def _expense_paid(
    url: str, actor: uuid.UUID, expense: uuid.UUID, amount: str, on: date
) -> uuid.UUID:
    payment = ExpensePaymentCreate(
        paid_on=on, amount=Money(amount), method_code="cash", reference=None
    )
    recorded = _run(
        url, actor, lambda s: finance.record_payment(s, actor, expense, payment)
    )
    return uuid.UUID(str(recorded.id))


def _expense_adjusted(
    url: str, actor: uuid.UUID, expense: uuid.UUID, amount: str
) -> None:
    adjustment = ExpenseAdjustmentCreate(amount=Money(amount), reason="Corrección")
    _run(url, actor, lambda s: finance.post_adjustment(s, actor, expense, adjustment))


def _billing_payment(
    url: str,
    actor: uuid.UUID,
    account: uuid.UUID,
    amount: str,
    on: date,
    lines: list[tuple[uuid.UUID, str]] | None = None,
) -> uuid.UUID:
    allocation = (
        AllocationRequest(mode="none")
        if lines is None
        else AllocationRequest(
            mode="explicit",
            lines=tuple(AllocationLine(charge_id=c, amount=Money(a)) for c, a in lines),
        )
    )
    draft = replace(rs.draft(account, amount, allocation), received_on=on)
    return rs.record(url, actor, draft).payment_id


@pytest.fixture(scope="module")
def scenario(
    module_seeded_url: str,
    client: TestClient,
    owner: Staff,
    finance_staff: Staff,
) -> Scenario:
    url = module_seeded_url
    actor = owner.user.id
    # Billing: three manual charges (unallocated revenue) and the PY1-PY4
    # payments of the issue's fixture.
    _, account, [student] = rs.new_account(url)

    async def _charges(session: AsyncSession) -> None:
        for amount, service, due in (
            ("250.00", d(11, 12), d(11, 30)),
            ("100.00", d(10, 15), d(10, 31)),
            ("50.00", d(12, 5), d(12, 20)),
        ):
            await factories.charge(
                session, student, amount, service_date=service, due=due
            )

    _run(url, actor, _charges)
    _billing_payment(url, actor, account, "1000.00", d(11, 5))
    py2 = _billing_payment(url, actor, account, "500.00", d(11, 10))
    _run(url, actor, lambda s: billing.reverse_payment(s, py2, "Pago duplicado"))
    py3 = _billing_payment(url, actor, account, "2800.00", d(10, 28))
    refund = RefundDraft(
        payment_id=py3,
        amount=Money("300.00"),
        refunded_on=d(11, 20),
        payment_method_code="cash",
        reason="Devolución",
    )
    _run(url, actor, lambda s: billing.refund_payment(s, refund))
    _billing_payment(url, actor, account, "400.00", d(12, 1))

    # Expenses X1-X5.
    rent = category_of(url, "rent")
    utilities = category_of(url, "utilities")
    supplies = category_of(url, "supplies")
    maintenance = category_of(url, "maintenance")
    [furniture] = run_sql(
        url,
        "INSERT INTO expense_categories (name) VALUES ('Mobiliario') RETURNING id",
    )
    vendor = new_vendor(url, "Muebles del Centro")
    x1 = _expense(url, actor, rent, "1500.00", d(11, 1), d(11, 1))
    _expense_paid(url, actor, x1, "1500.00", d(11, 3))
    x2 = _expense(url, actor, utilities, "1200.00", d(11, 10), d(12, 20))
    first = _expense_paid(url, actor, x2, "600.00", d(11, 25))
    reversal = ExpensePaymentReversalCreate(reason="Monto equivocado")
    _run(url, actor, lambda s: finance.reverse_payment(s, actor, first, reversal))
    _expense_paid(url, actor, x2, "600.00", d(11, 26))
    x3 = _expense(url, actor, supplies, "300.00", d(11, 15), d(11, 15))
    _expense_adjusted(url, actor, x3, "-300.00")  # void
    x4 = _expense(url, actor, maintenance, "800.00", d(10, 28), d(10, 28))
    _expense_paid(url, actor, x4, "800.00", d(11, 5))
    _expense(url, actor, furniture.id, "500.00", d(11, 20), d(11, 30), vendor)
    run_sql(
        url,
        "UPDATE expense_categories SET is_active = false WHERE id = :id",
        {"id": furniture.id},
    )
    run_sql(url, "UPDATE vendors SET is_active = false WHERE id = :id", {"id": vendor})

    # Payroll: one finalized period of 110.00 (an individual lesson on Nov 3),
    # 30.00 paid on Nov 20, 20.00 paid and reversed.
    set_floor(url, "10-01")
    tutor = create_user(url, roles=(TUTOR,), display_name="Tutora Reporte")
    period = finalized_period(url, client, owner, finance_staff, tutor)
    assert (
        pay(client, finance_staff, period, body("30.00", "SPEI 1")).status_code == 201
    )
    paid = pay(
        client, finance_staff, period, body("20.00", "SPEI 2", paid_on="2026-11-21")
    )
    assert paid.status_code == 201, paid.text
    reversed_ = reverse(client, owner, paid.json()["id"])
    assert reversed_.status_code == 201, reversed_.text

    # T3: another tutor's prepared period (lines, not finalized): never cost.
    other = create_user(url, roles=(TUTOR,), display_name="Tutor Preparado")
    open_period = worked_period(url, client, owner, other)
    assert prepare(client, finance_staff, open_period).status_code == 200
    [lines] = run_sql(
        url,
        "SELECT count(*) AS n FROM payroll_lines WHERE payroll_period_id = :p",
        {"p": open_period},
    )
    assert lines.n > 0
    made = Scenario(account=account, period=period)
    made.before["accrual_nov"] = accrual(client, owner, d(11, 1), d(11, 30))
    _expense_adjusted(url, actor, x2, "150.00")  # posted after the snapshot (D3)
    return made


def class_row(report: dict[str, Any], class_type: str) -> tuple[str, str, str]:
    [row] = [r for r in report["class_rows"] if r["class_type"] == class_type]
    return row["revenue"], row["direct_tutor_cost"], row["gross_profit"]


def test_d1_accrual_november(client, owner, scenario):
    report = accrual(client, owner, d(11, 1), d(11, 30))
    assert [r["class_type"] for r in report["class_rows"]] == [
        "individual",
        "group",
        "course",
        "unallocated",
    ]
    assert class_row(report, "individual") == ("0.00", "110.00", "-110.00")
    assert class_row(report, "unallocated") == ("250.00", "0.00", "250.00")
    assert report["revenue_total"] == "250.00"
    assert report["direct_tutor_cost_total"] == "110.00"
    expenses = report["operating_expenses"]
    assert expenses["total"] == "3350.00"
    assert [
        (row["category"]["name"], row["category"]["is_active"], row["amount"])
        for row in expenses["by_category"]
    ] == [
        ("Renta", True, "1500.00"),
        ("Servicios", True, "1350.00"),
        ("Mobiliario", False, "500.00"),
    ]
    assert [
        (None if row["vendor"] is None else row["vendor"]["is_active"], row["amount"])
        for row in expenses["by_vendor"]
    ] == [(None, "2850.00"), (False, "500.00")]
    assert report["net_result"] == "-3210.00"
    assert (report["from"], report["to"], report["today"]) == (
        "2026-11-01",
        "2026-11-30",
        "2026-12-07",
    )


def test_d2_cash_november_and_the_october_refund_rule(client, owner, scenario):
    report = cash(client, owner, d(11, 1), d(11, 30))
    assert report["class_rows"] == []
    assert report["revenue_total"] == "700.00"
    assert report["direct_tutor_cost_total"] == "30.00"
    assert report["operating_expenses"]["total"] == "2900.00"
    assert report["net_result"] == "-2230.00"
    # D3: the refund on Nov 20 leaves October's cash untouched (E6).
    assert cash(client, owner, d(10, 1), d(10, 31))["revenue_total"] == "2800.00"


def test_d3_an_expense_adjustment_restates_its_month(client, owner, scenario):
    assert scenario.before["accrual_nov"]["operating_expenses"]["total"] == "3200.00"
    after = accrual(client, owner, d(11, 1), d(11, 30))
    assert after["operating_expenses"]["total"] == "3350.00"


def test_d4_reconciliation_with_the_owners_reads(
    module_seeded_url, client, owner, scenario
):
    url = module_seeded_url
    start, end = d(10, 1), TODAY
    report = accrual(client, owner, start, end)
    deferred = get(client, owner, BALANCES)["deferred_revenue"]

    async def _owner_reads(session: AsyncSession) -> tuple[Money, Money, Money, Money]:
        accounts = list(await session.scalars(select(BillingAccount.id)))
        nets = []
        for account in accounts:
            nets += [c.net for c in await billing.charge_balances(session, account)]
        figures = finance.expense_figures(TODAY)
        expense_rows = (await session.execute(select(figures))).all()
        total = await payroll.paid_total(session, scenario.period)
        return (
            Money.total(nets),
            Money.total(r.net for r in expense_rows),
            Money.total(r.paid for r in expense_rows),
            total,
        )

    charge_net, expense_net, expense_paid, payroll_paid = in_uow(url, _owner_reads)
    recognized = Money(report["revenue_total"])
    assert recognized + Money(deferred["total"]) == charge_net
    assert str(charge_net) == "400.00"
    [period] = run_sql(
        url,
        "SELECT total_amount FROM payroll_periods WHERE id = :id",
        {"id": scenario.period},
    )
    assert Money(report["direct_tutor_cost_total"]) == Money(period.total_amount)
    payroll_cash = Money(cash(client, owner, start, end)["direct_tutor_cost_total"])
    assert payroll_cash == payroll_paid == Money("30.00")
    payables = get(client, owner, BALANCES)["payables"]
    assert Money(period.total_amount) - payroll_cash == Money(
        payables["payroll_unpaid"]
    )
    assert Money(report["operating_expenses"]["total"]) == expense_net
    expense_cash = Money(cash(client, owner, start, end)["operating_expenses"]["total"])
    assert expense_cash == expense_paid == Money("2900.00")
    assert expense_net - expense_cash == Money(payables["expenses_open"])
    cash_revenue = Money(cash(client, owner, start, end)["revenue_total"])
    [sums] = run_sql(
        url,
        "SELECT (SELECT coalesce(sum(amount), 0) FROM payments p WHERE NOT EXISTS"
        " (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)) AS paid,"
        " (SELECT coalesce(sum(amount), 0) FROM refunds) AS refunded",
    )
    assert cash_revenue == Money(sums.paid) - Money(sums.refunded) == Money("3900.00")


def test_e1_payables(client, finance_staff, scenario):
    payables = get(client, finance_staff, BALANCES)["payables"]
    assert payables == {
        "expenses_open": "1250.00",
        "expenses_overdue": "500.00",
        "payroll_unpaid": "80.00",
        "payroll_periods_unpaid": 1,
        "total": "1330.00",
    }


def test_e3_receivables_standalone_oracle_and_the_seam(
    module_seeded_url, client, owner, scenario
):
    url = module_seeded_url
    actor = owner.user.id
    before = get(client, owner, BALANCES)["receivables"]
    _, account, [student] = rs.new_account(url)

    async def _charges(session: AsyncSession) -> list[uuid.UUID]:
        first = await factories.charge(
            session, student, "350.00", service_date=d(11, 1), due=d(11, 20)
        )
        second = await factories.charge(
            session, student, "200.00", service_date=d(12, 1), due=d(12, 20)
        )
        return [first.charge_id, second.charge_id]

    first, _second = _run(url, actor, _charges)
    _billing_payment(url, actor, account, "400.00", d(11, 15), [(first, "350.00")])
    after = get(client, owner, BALANCES)["receivables"]
    delta = {
        key: str(Money(after[key]) - Money(before[key]))
        for key in ("outstanding", "overdue", "account_credit")
    }
    assert delta == {
        "outstanding": "200.00",
        "overdue": "0.00",
        "account_credit": "50.00",
    }
    assert after["accounts_with_outstanding"] - before["accounts_with_outstanding"] == 1

    async def _sum_of_balances(session: AsyncSession) -> tuple[Money, Money, Money]:
        accounts = list(await session.scalars(select(BillingAccount.id)))
        balances = [
            await billing.account_balance(session, a, today=TODAY) for a in accounts
        ]
        return (
            Money.total(b.outstanding for b in balances),
            Money.total(b.overdue for b in balances),
            Money.total(b.account_credit for b in balances),
        )

    outstanding, overdue, credit = in_uow(url, _sum_of_balances)
    totals = in_uow(url, lambda s: billing.receivable_totals(s, TODAY))
    assert (totals.outstanding, totals.overdue, totals.account_credit) == (
        outstanding,
        overdue,
        credit,
    )
    assert (after["outstanding"], after["overdue"], after["account_credit"]) == (
        str(outstanding),
        str(overdue),
        str(credit),
    )


def test_a3_charge_figures_net_equals_charge_balances(module_seeded_url, scenario):
    async def _both(session: AsyncSession) -> tuple[dict[Any, Money], dict[Any, Money]]:
        figures = billing.charge_figures()
        mine = {
            row.id: Money(row.net)
            for row in await session.execute(select(figures.c.id, figures.c.net))
        }
        theirs: dict[Any, Money] = {}
        for account in await session.scalars(select(BillingAccount.id)):
            for charge in await billing.charge_balances(session, account):
                theirs[charge.charge_id] = charge.net
        return mine, theirs

    mine, theirs = in_uow(module_seeded_url, _both)
    assert mine == theirs
    assert mine


def test_a3_paid_sum_equals_paid_total(module_seeded_url, scenario):
    async def _both(session: AsyncSession) -> tuple[Any, Money]:
        value = await session.scalar(select(payroll.paid_sum(scenario.period)))
        return value, await payroll.paid_total(session, scenario.period)

    value, total = in_uow(module_seeded_url, _both)
    assert Money(value) == total == Money("30.00")


# --- F1, F2 ------------------------------------------------------------------------


def _statements(url: str, call: Callable[[AsyncSession], Awaitable[Any]]) -> list[str]:
    seen: list[str] = []

    async def _go() -> None:
        engine = short_lived_engine(url)

        def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                seen.clear()
                await call(uow.session)
        finally:
            await engine.dispose()

    asyncio.run(_go())
    return seen


def _all_reports(url: str) -> dict[str, list[str]]:
    return {
        "accrual": _statements(
            url,
            lambda s: reports.profitability_report(
                s, basis="accrual", start=d(10, 1), end=TODAY
            ),
        ),
        "cash": _statements(
            url,
            lambda s: reports.profitability_report(
                s, basis="cash", start=d(10, 1), end=TODAY
            ),
        ),
        "balances": _statements(url, reports.balances_report),
    }


def test_f1_f2_fixed_statements_no_lock_no_write(module_seeded_url, owner, scenario):
    url = module_seeded_url
    actor = owner.user.id
    small = _all_reports(url)
    _, account, [student] = rs.new_account(url)

    async def _many(session: AsyncSession) -> None:
        for i in range(20):
            await factories.charge(
                session, student, "10.00", service_date=d(11, 1 + i), due=d(11, 1 + i)
            )

    _run(url, actor, _many)
    rent = category_of(url, "rent")
    for i in range(20):
        _expense(url, actor, rent, "5.00", d(11, 1 + i), d(11, 1 + i))
    for i in range(5):
        _billing_payment(url, actor, account, "1.00", d(11, 1 + i))
    large = _all_reports(url)
    assert {k: len(v) for k, v in large.items()} == {
        k: len(v) for k, v in small.items()
    }
    for statements in large.values():
        for statement in statements:
            text = statement.upper()
            assert "FOR UPDATE" not in text
            assert "FOR SHARE" not in text
            assert not text.lstrip().startswith(("INSERT", "UPDATE", "DELETE"))
