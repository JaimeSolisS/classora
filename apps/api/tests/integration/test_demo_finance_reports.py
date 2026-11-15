"""The financial reports on the whole demo database (#501 F9, E5-E7): the
accrual and cash profitability over ``[F, T]`` and the balances, as
``demo.finance`` and ``demo.owner`` see them; the #117 D4 identities over
``[F, T]`` and the widest range ``W``, with the right-hand sides read from the
owners' public seams (never the report code); and the 403s.

The payroll side (direct tutor cost, payroll cash and unpaid) is #499's,
imported, never retyped (E7)."""

import asyncio
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.db import create_sessionmaker
from app.core.money import Money, money_sum
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.billing.models import Payment, Refund
from app.modules.finance import service as finance
from app.modules.finance.models import ExpensePayment
from app.modules.identity.permissions import PAYROLL_FINALIZE
from tests.factories import demo
from tests.factories.demo_finance import FURNITURE, STATIONERY, VENDORS
from tests.integration.auth_support import auth_client, signed_headers
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    FINANCE_EXPENSES_GENERATOR,
    FROZEN_NOW,
    demo_env,
    generators_through,
    migrate,
)
from tests.integration.invitation_support import REAUTHENTICATE
from tests.integration.test_demo_payroll_views import (
    ACCRUAL_PAYROLL,
    CASH_PAYROLL,
    CLASS_ROWS,
    PAYROLL_UNPAID,
)
from tests.integration.test_demo_schedule import PASSWORDS, signed_in
from tests.integration.user_admin_support import put_delegations, version_of

F = date(2026, 10, 12)
T = date(2026, 11, 4)
W = (T - timedelta(days=365), T)
"""The widest range the report allows (E6)."""
PROFITABILITY = "/api/v1/reports/finance/profitability"
BALANCES = "/api/v1/reports/finance/balances"

REVENUE = {
    "individual": "5704.00",
    "group": "0.00",
    "course": "4455.00",
    "unallocated": "250.00",
}
"""Accrual revenue over ``[F, T]`` by class row. ``individual``: nine session
charges (2,799.00), the packages (1,305.00) and TP1's first month
(1,600.00); ``course``: #353's three fees over their six classes; ``group``:
nothing held yet (E5); ``unallocated``: MC."""
ACCRUAL = {
    "revenue_total": "10409.00",
    "direct_tutor_cost_total": ACCRUAL_PAYROLL,
    "gross_profit": "-1261.00",
    "net_result": "-28471.00",
}
ACCRUAL_EXPENSES = "27210.00"
ACCRUAL_BY_CATEGORY = [
    ("Equipo", "9200.00"),
    ("Renta", "6500.00"),
    ("Mantenimiento", "5600.00"),
    (STATIONERY, "2500.00"),
    (FURNITURE, "2400.00"),
    ("Servicios", "1010.00"),
]
ACCRUAL_BY_VENDOR = [
    (None, "10160.00"),
    (VENDORS[0].name, "6500.00"),
    (VENDORS[1].name, "6300.00"),
    (VENDORS[3].name, "2400.00"),
    (VENDORS[2].name, "1850.00"),
]
CASH = {
    "revenue_total": "2000.00",
    "direct_tutor_cost_total": CASH_PAYROLL,
    "gross_profit": "1730.00",
    "net_result": "-10030.00",
}
CASH_EXPENSES = "11760.00"
CASH_BY_CATEGORY = [
    ("Equipo", "6900.00"),
    (FURNITURE, "2400.00"),
    (STATIONERY, "1650.00"),
    ("Mantenimiento", "450.00"),
    ("Servicios", "360.00"),
]
CASH_BY_VENDOR = [
    (None, "5010.00"),
    (VENDORS[1].name, "3150.00"),
    (VENDORS[3].name, "2400.00"),
    (VENDORS[2].name, "1200.00"),
]
BALANCES_FIGURES = {
    "receivables": {
        "outstanding": "20595.50",
        "overdue": "11012.00",
        "account_credit": "500.00",
        "accounts_with_outstanding": 3,
    },
    "payables": {
        "expenses_open": "15450.00",
        "expenses_overdue": "1550.00",
        "payroll_unpaid": PAYROLL_UNPAID,
        "payroll_periods_unpaid": 5,
        "total": "26850.00",
    },
    "deferred_revenue": {
        "total": "11686.50",
        "packages": "2205.00",
        "courses": "3307.50",
        "scheduled_sessions": "6174.00",
        "other": "0.00",
    },
}
CHARGE_NET = "22095.50"
"""Σ every charge's net: accrual revenue 10,409.00 + deferred 11,686.50."""
NO_REPORTS = (
    "demo.admin",
    "demo.coordinator",
    "demo.tutor",
    "demo.tutor2",
    "demo.student",
    "demo.guardian",
)


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response.json()


def _report(
    client: Any, cookie: Any, basis: str, start: date, end: date
) -> dict[str, Any]:
    return _ok(
        get(
            client,
            cookie,
            PROFITABILITY,
            basis=basis,
            **{"from": start.isoformat()},
            to=end.isoformat(),
        )
    )


def _refs(url: str) -> dict[str, dict[str, Any]]:
    """Every category and vendor ref by name (current names, keys, state)."""
    categories = run_sql(
        url, "SELECT id, name, system_key, is_active FROM expense_categories"
    )
    vendors = run_sql(url, "SELECT id, name, is_active FROM vendors")
    return {
        "categories": {
            c.name: {
                "id": str(c.id),
                "name": c.name,
                "system_key": c.system_key,
                "is_active": c.is_active,
            }
            for c in categories
        },
        "vendors": {
            v.name: {"id": str(v.id), "name": v.name, "is_active": v.is_active}
            for v in vendors
        },
    }


def _expected_groups(
    refs: dict[str, dict[str, Any]],
    by_category: list[tuple[str, str]],
    by_vendor: list[tuple[str | None, str]],
) -> dict[str, Any]:
    return {
        "by_category": [
            {"category": refs["categories"][name], "amount": amount}
            for name, amount in by_category
        ],
        "by_vendor": [
            {
                "vendor": None if name is None else refs["vendors"][name],
                "amount": amount,
            }
            for name, amount in by_vendor
        ],
    }


def _check(client: Any, cookie: Any, refs: dict[str, dict[str, Any]]) -> None:
    """The figures of #501 "Expected figures" over ``[F, T]``."""
    accrual = _report(client, cookie, "accrual", F, T)
    assert [
        (
            r["class_type"],
            r["revenue"],
            r["direct_tutor_cost"],
            r["gross_profit"],
        )
        for r in accrual["class_rows"]
    ] == [
        (
            kind,
            REVENUE[kind],
            cost,
            str(Money(REVENUE[kind]) - Money(cost)),
        )
        for kind, cost in CLASS_ROWS
    ]
    assert {key: accrual[key] for key in ACCRUAL} == ACCRUAL
    expenses = accrual["operating_expenses"]
    assert expenses["total"] == ACCRUAL_EXPENSES
    assert {
        "by_category": expenses["by_category"],
        "by_vendor": expenses["by_vendor"],
    } == _expected_groups(refs, ACCRUAL_BY_CATEGORY, ACCRUAL_BY_VENDOR)

    cash = _report(client, cookie, "cash", F, T)
    assert {key: cash[key] for key in CASH} == CASH
    assert cash["class_rows"] == []
    expenses = cash["operating_expenses"]
    assert expenses["total"] == CASH_EXPENSES
    assert {
        "by_category": expenses["by_category"],
        "by_vendor": expenses["by_vendor"],
    } == _expected_groups(refs, CASH_BY_CATEGORY, CASH_BY_VENDOR)

    balances = _ok(get(client, cookie, BALANCES))
    assert {key: balances[key] for key in BALANCES_FIGURES} == BALANCES_FIGURES
    assert balances["today"] == T.isoformat()


async def _owner_reads(url: str, start: date, end: date) -> dict[str, Money]:
    """The right-hand sides of #117 D4, from the owners' public seams."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            session = uow.session
            charges = billing.charge_figures()
            charge_net = await session.scalar(select(money_sum(charges.c.net)))
            paid = await session.scalar(
                select(money_sum(Payment.amount)).where(
                    billing.unreversed_payment(),
                    Payment.received_on.between(start, end),
                )
            )
            refunded = await session.scalar(
                select(money_sum(Refund.amount)).where(
                    Refund.refunded_on.between(start, end)
                )
            )
            today = await administration.center_today(session)
            figures = finance.expense_figures(today)
            expense_net = await session.scalar(
                select(money_sum(figures.c.net)).where(
                    figures.c.incurred_on.between(start, end)
                )
            )
            expense_cash = await session.scalar(
                select(money_sum(ExpensePayment.amount)).where(
                    finance.unreversed(), ExpensePayment.paid_on.between(start, end)
                )
            )
            expense_open = await session.scalar(
                select(money_sum(figures.c.open)).where(
                    figures.c.status != "void", figures.c.open > Money.zero()
                )
            )
            payments = await session.scalar(select(func.count(Payment.id)))
            assert payments
            return {
                "charge_net": charge_net,
                "cash_revenue": paid - refunded,
                "expense_net": expense_net,
                "expense_cash": expense_cash,
                "expense_open": expense_open,
            }
    finally:
        await engine.dispose()


def test_the_reports_on_the_demo_data(migrated_url, env_for, clock):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    # M8-demo E4: through finance.demo_expenses (the opening balances would
    # move CHARGE_NET and the unallocated revenue).
    generators = generators_through(FINANCE_EXPENSES_GENERATOR)
    assert demo.main([], generators=generators) == 0
    refs = _refs(migrated_url)
    [admin] = run_sql(
        migrated_url, "SELECT id FROM users WHERE username = 'demo.admin'"
    )

    with auth_client(migrated_url) as client:
        cookie = signed_in(client, "demo.finance")
        _check(client, cookie, refs)

        # E6: the three identities over [F, T] and over W.
        balances = _ok(get(client, cookie, BALANCES))
        deferred = Money(balances["deferred_revenue"]["total"])
        expenses_open = Money(balances["payables"]["expenses_open"])
        for start, end in ((F, T), W):
            reads = asyncio.run(_owner_reads(migrated_url, start, end))
            accrual = _report(client, cookie, "accrual", start, end)
            cash = _report(client, cookie, "cash", start, end)
            assert Money(accrual["revenue_total"]) + deferred == reads["charge_net"]
            assert str(reads["charge_net"]) == CHARGE_NET
            assert Money(cash["revenue_total"]) == reads["cash_revenue"]
            expense_net = Money(accrual["operating_expenses"]["total"])
            expense_cash = Money(cash["operating_expenses"]["total"])
            assert expense_net == reads["expense_net"]
            assert expense_cash == reads["expense_cash"]
            assert expense_net - expense_cash == expenses_open
            assert expenses_open == reads["expense_open"]

        # demo.owner: the same figures.
        cookie = signed_in(client, "demo.owner")
        _check(client, cookie, refs)

        # demo.owner delegates payroll.finalize to demo.admin (#170): still no
        # reports for the Administrator.
        client.cookies.clear()
        reauthenticated = client.post(
            REAUTHENTICATE,
            json={"password": PASSWORDS["demo.owner"]},
            headers=signed_headers(cookie),
        )
        assert reauthenticated.status_code == 200, reauthenticated.text
        delegated = put_delegations(
            client,
            cookie,
            admin.id,
            [PAYROLL_FINALIZE],
            version_of(migrated_url, admin.id),
        )
        assert delegated.status_code == 200, delegated.text

        for username in NO_REPORTS:
            cookie = signed_in(client, username)
            for path in (
                BALANCES,
                f"{PROFITABILITY}?basis=accrual&from={F}&to={T}",
                f"{PROFITABILITY}?basis=cash&from={F}&to={T}",
            ):
                assert get(client, cookie, path).status_code == 403, (username, path)
