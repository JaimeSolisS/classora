"""The M7 demo data, part 2 (#501; A-E, F2-F7): ``billing.demo_past_cohort_fee``,
``finance.demo_setup``, ``finance.demo_templates`` and
``finance.demo_expenses``, the whole run with them, worker parity and reruns,
weekends, ``dev`` and the flag, the stale and hand-configured skips,
prerequisites and rollback. What each account sees is in
``test_demo_finance_views``; the reports in ``test_demo_finance_reports``."""

import asyncio
import dataclasses
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from app.core import clock as app_clock
from app.core.audit import ACTOR_INFO_KEY
from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.finance import service as finance
from app.modules.finance.errors import ExpensePaymentExceedsOpenError
from app.modules.finance.schemas import (
    ExpenseCategoryUpdate,
    ExpenseCreate,
    VendorCreate,
)
from app.workflows.charge_reminders import run_send_charge_reminders
from app.workflows.enrollment_charges import run_post_enrollment_charges
from app.workflows.recurring_expenses import run_generate_recurring_expenses
from tests.factories import demo
from tests.factories.demo_academics import COHORT_NAME
from tests.factories.demo_billing import BILLING_CONFIGURED, WITHOUT_BILLING
from tests.factories.demo_changes import STALE_SCHEDULE
from tests.factories.demo_finance import (
    FEE_AMOUNT,
    FINANCE_CONFIGURED,
    FURNITURE,
    STATIONERY,
    TL_VOID_REASON,
    VENDORS,
    X5_REVERSAL_REASON,
    X6_ADJUSTMENT_REASON,
    X7_ADJUSTMENT_REASON,
    DemoFinanceExpensesGenerator,
    DemoFinancePlan,
    DemoFinanceSetupGenerator,
    DemoFinanceTemplatesGenerator,
    DemoPastCohortFeeGenerator,
    Pay,
    PlannedPayment,
    plan_demo_finance,
)
from tests.factories.identity import (
    DEMO_OWNER_USERNAME,
    DemoPrerequisiteMissingError,
    demo_user_id,
)
from tests.integration.billing_invariants.checks import Catalogue
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.demo_support import (
    ADMIN_GENERATORS,
    ALL_TABLE_COUNTS,
    DEMO_FINANCE_EXPENSES_ROWS,
    DEMO_FINANCE_SETUP_ROWS,
    DEMO_FINANCE_TABLE_COUNTS,
    DEMO_FINANCE_TEMPLATES_ROWS,
    DEMO_PAST_COHORT_FEE_ROWS,
    DEV_LINES,
    FINANCE_GENERATORS,
    FIRST_RUN_LINES,
    FLAGGED_GENERATORS,
    FROZEN_NOW,
    GENERATOR_NAMES,
    PAYROLL_CORRECTIONS_GENERATOR,
    RERUN_LINES,
    RERUN_REFERENCE_LINES,
    SEEDED_CATEGORIES,
    TABLE_COUNTS_THROUGH_354,
    TABLE_COUNTS_THROUGH_499,
    TABLE_COUNTS_THROUGH_501,
    count,
    demo_env,
    demo_table_counts,
    first_run_lines,
    generators_through,
    migrate,
)

D = Decimal
# T = Wednesday 2026-11-04: F = 10-12, M1 = 11-01, A31 = 10-31, T0 = 10-14,
# the billing floor B = 10-16.
F = date(2026, 10, 12)
T = date(2026, 11, 4)
FINANCE_TABLES = (
    "vendors",
    "expense_templates",
    "expenses",
    "expense_payments",
    "expense_payment_reversals",
    "expense_adjustments",
)
FEES = [
    # (amount, due date, list amount, discount lines): S and B 1,800.00 less
    # the sibling 10 %; A less the scholarship 25 % then the sibling 10 %
    # (#95 H4). S's family F1 has no due-date offset, F2 (A, B) 5 days.
    (D("1215.00"), date(2026, 10, 21), D("1800.00"), 2),
    (D("1620.00"), date(2026, 10, 16), D("1800.00"), 1),
    (D("1620.00"), date(2026, 10, 21), D("1800.00"), 1),
]
TEMPLATES = {
    # name: (status, is_active, next_period_start, periods)
    "Renta del local": ("active", True, date(2026, 12, 1), 1),
    "Internet y telefonía": ("active", True, date(2026, 11, 30), 1),
    "Limpieza semanal": ("paused", False, date(2026, 11, 9), 4),
    "Impresión de volantes de inscripción": ("ended", True, date(2026, 10, 26), 2),
    "Mantenimiento anual de climas": ("active", True, date(2027, 10, 12), 1),
}
"""D1 at ``FROZEN_NOW``: TI's next period is clamped to 11-30."""
STATUSES = Counter(
    {"unpaid": 4, "overdue": 3, "partially_paid": 1, "paid": 8, "void": 2}
)
"""The 18 expenses' statuses at ``FROZEN_NOW`` (#501 "Expected figures")."""
ENROLLMENT_STEPS = Counter({"posted": 3})
REMINDER_STEPS = Counter(
    {"sent": 1, "sent_charges": 1, "notifications": 1, "no_recipient": 2}
)
"""E3, B3: the reminder step after the fees (F1's account; F2 and F3 have
no recipient)."""
WEEKEND_TEMPLATES_ROWS = 17
"""``finance.demo_templates`` on a weekend (F = 10-19): TL has three weeks, so
one generated expense fewer."""


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _fresh_finance(
    plan: Any = plan_demo_finance,
) -> list[Any]:
    """The registered generators with new #501 instances (their step counts
    and ids are read after the run)."""
    return [
        *generators_through(PAYROLL_CORRECTIONS_GENERATOR),
        DemoPastCohortFeeGenerator(),
        DemoFinanceSetupGenerator(),
        DemoFinanceTemplatesGenerator(),
        DemoFinanceExpensesGenerator(plan=plan),
    ]


def _finance_lines(lines: list[str]) -> list[str]:
    """The four #501 lines of a run's output, by generator name (the M8-demo
    generators come after them)."""
    by_name = {line.split(":")[0]: line for line in lines}
    return [by_name[name] for name in FINANCE_GENERATORS]


def _line(name: str, rows: int | str) -> str:
    return f"{name}: {rows} rows" if isinstance(rows, int) else f"{name}: {rows}"


def _no_finance_data(url: str) -> None:
    for table in FINANCE_TABLES:
        assert count(url, table) == 0, table
    categories = run_sql(
        url, "SELECT name, system_key, is_active FROM expense_categories"
    )
    assert len(categories) == SEEDED_CATEGORIES
    assert all(c.system_key is not None and c.is_active for c in categories)


def _cohort_prices(url: str) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM service_prices p JOIN course_cohorts c"
        " ON c.id = p.cohort_id WHERE c.name = :n",
        {"n": COHORT_NAME},
    )
    return int(row.n)


def _actors(url: str, record_types: tuple[str, ...]) -> dict[tuple[str, str], set[Any]]:
    rows = run_sql(
        url,
        "SELECT e.record_type, e.action, u.username FROM audit_events e"
        " LEFT JOIN users u ON u.id = e.actor_user_id"
        " WHERE e.record_type = ANY(:types) GROUP BY 1, 2, 3",
        {"types": list(record_types)},
    )
    out: dict[tuple[str, str], set[Any]] = {}
    for r in rows:
        out.setdefault((r.record_type, r.action), set()).add(r.username)
    return out


async def _statuses(url: str) -> dict[Any, Any]:
    """Every expense's figures, by finance's own read (``expense_figures``)."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            today = await administration.center_today(uow.session)
            figures = finance.expense_figures(today)
            rows = (await uow.session.execute(select(figures))).all()
            return {r.id: r for r in rows}
    finally:
        await engine.dispose()


async def _violations_on(url: str) -> list[Any]:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            today = await administration.center_today(uow.session)
            return await Catalogue().run(uow.session, today=today)
    finally:
        await engine.dispose()


def test_a_first_run_writes_the_finance_data(migrated_url, env_for, clock, capsys):
    # F2 on Wednesday 2026-11-04, 11:00 local.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = _fresh_finance()

    assert demo.main([], generators=generators) == 0

    assert capsys.readouterr().out.splitlines() == first_run_lines(generators)
    assert app_clock.now() == FROZEN_NOW
    fee, _, templates, expenses = generators[-4:]

    # B1: PR7, by demo.finance.
    [price] = run_sql(
        migrated_url,
        "SELECT p.id, p.class_type, p.pricing_model, s.name AS subject,"
        " v.effective_from, v.amount FROM service_prices p"
        " JOIN course_cohorts c ON c.id = p.cohort_id"
        " JOIN subjects s ON s.id = p.subject_id"
        " JOIN price_versions v ON v.service_price_id = p.id WHERE c.name = :n",
        {"n": COHORT_NAME},
    )
    assert tuple(price)[1:] == (
        "course",
        "full_course",
        "Matemáticas",
        date(2026, 10, 14),
        D(FEE_AMOUNT),
    )
    assert count(migrated_url, "service_prices") == 9

    # B2: one cohort_enrollment charge per enrollment, by the job step.
    charges = run_sql(
        migrated_url,
        "SELECT ch.id, ch.amount, ch.due_date, ch.list_amount, ch.posted_by_user_id,"
        " ch.proration_remaining, ch.proration_total,"
        " (SELECT count(*) FROM charge_discounts d WHERE d.charge_id = ch.id)"
        " AS discounts FROM charges ch"
        " JOIN cohort_enrollments e ON e.id = ch.cohort_enrollment_id"
        " JOIN course_cohorts c ON c.id = e.cohort_id WHERE c.name = :n"
        " ORDER BY ch.amount, ch.due_date",
        {"n": COHORT_NAME},
    )
    assert [(c.amount, c.due_date, c.list_amount, c.discounts) for c in charges] == FEES
    assert {c.posted_by_user_id for c in charges} == {None}
    assert {(c.proration_remaining, c.proration_total) for c in charges} == {(6, 6)}
    assert fee.steps.enrollment == ENROLLMENT_STEPS
    # B3: the reminder step's counts; demo.guardian's new reminder.
    assert fee.steps.reminders == REMINDER_STEPS
    # B4: the billing invariants hold on the whole database.
    assert asyncio.run(_violations_on(migrated_url)) == []

    # C1, C2: the rename, "Mobiliario" and V1-V4; then the deactivations.
    categories = {
        r.name: (r.system_key, r.is_active)
        for r in run_sql(
            migrated_url, "SELECT name, system_key, is_active FROM expense_categories"
        )
    }
    assert categories == {
        "Renta": ("rent", True),
        "Servicios": ("utilities", True),
        STATIONERY: (None, True),
        "Equipo": ("equipment", True),
        "Mantenimiento": ("maintenance", True),
        FURNITURE: (None, False),
    }
    vendors = run_sql(
        migrated_url, "SELECT name, contact, is_active FROM vendors ORDER BY name"
    )
    assert sorted((v.name, v.contact, v.is_active) for v in vendors) == sorted(
        (v.name, v.contact, v.key != "V4") for v in VENDORS
    )

    # D1: the five templates, created by demo.finance.
    rows = run_sql(
        migrated_url,
        "SELECT t.id, t.name, t.is_active, t.next_period_start, t.ends_on,"
        " (SELECT count(*) FROM expenses x WHERE x.expense_template_id = t.id)"
        " AS periods FROM expense_templates t",
    )
    status = {
        "Impresión de volantes de inscripción": "ended",
        "Limpieza semanal": "paused",
    }
    assert {
        r.name: (
            status.get(r.name, "active"),
            r.is_active,
            r.next_period_start,
            r.periods,
        )
        for r in rows
    } == TEMPLATES
    assert set(templates.created.values()) == {r.id for r in rows}
    assert dataclasses.astuple(templates.steps) == (5, 9, 0)

    # D2: the generated expenses: no creator, the template's name.
    generated = run_sql(
        migrated_url,
        "SELECT x.id, x.description, t.name, x.period_start, x.incurred_on,"
        " x.created_by_user_id FROM expenses x"
        " JOIN expense_templates t ON t.id = x.expense_template_id"
        " ORDER BY t.name, x.period_start",
    )
    assert len(generated) == 9
    assert all(g.description == g.name for g in generated)
    assert all(g.incurred_on == g.period_start for g in generated)
    assert {g.created_by_user_id for g in generated} == {None}
    figures = asyncio.run(_statuses(migrated_url))
    tl = [g for g in generated if g.name == "Limpieza semanal"]
    assert [(g.period_start, figures[g.id].status) for g in tl] == [
        (date(2026, 10, 12), "paid"),
        (date(2026, 10, 19), "void"),
        (date(2026, 10, 26), "overdue"),
        (date(2026, 11, 2), "overdue"),
    ]
    tv = [g for g in generated if g.name == "Impresión de volantes de inscripción"]
    assert [figures[g.id].status for g in tv] == ["paid", "paid"]
    [void] = run_sql(
        migrated_url,
        "SELECT a.amount, a.reason FROM expense_adjustments a WHERE a.expense_id = :id",
        {"id": tl[1].id},
    )
    assert (void.amount, void.reason) == (D("-450.00"), TL_VOID_REASON)

    # E1: X1-X9 by demo.finance, with their statuses.
    recorded = expenses.recorded
    assert recorded is not None
    plan = plan_demo_finance(T)
    for planned in plan.expenses:
        row = figures[recorded.expenses[planned.key]]
        assert (row.status, row.incurred_on, row.due_on, str(row.amount)) == (
            planned.status,
            planned.incurred_on,
            planned.due_on,
            planned.amount,
        ), planned.key
        assert row.description == planned.description
        assert row.receipt_reference == planned.receipt_reference
    assert Counter(r.status for r in figures.values()) == STATUSES
    assert str(figures[recorded.expenses["X6"]].net) == "1050.00"
    assert str(figures[recorded.expenses["X7"]].net) == "0.00"
    x5 = run_sql(
        migrated_url,
        "SELECT p.id, p.paid_on, p.amount, p.method_code, p.reference,"
        " u.username AS recorded_by, r.reason, ru.username AS reversed_by"
        " FROM expense_payments p JOIN users u ON u.id = p.recorded_by_user_id"
        " LEFT JOIN expense_payment_reversals r ON r.expense_payment_id = p.id"
        " LEFT JOIN users ru ON ru.id = r.reversed_by_user_id"
        " WHERE p.expense_id = :id ORDER BY p.paid_on",
        {"id": recorded.expenses["X5"]},
    )
    assert [tuple(p)[1:] for p in x5] == [
        (date(2026, 10, 20), D("1200.00"), "transfer", "COL-DEMO-0001",
         "demo.finance", X5_REVERSAL_REASON, "demo.owner"),
        (date(2026, 10, 22), D("1200.00"), "transfer", "COL-DEMO-0001",
         "demo.finance", None, None),
    ]  # fmt: skip
    assert [p.id for p in x5] == recorded.payments["X5"]
    adjustments = run_sql(
        migrated_url,
        "SELECT a.expense_id, a.amount, a.reason, u.username"
        " FROM expense_adjustments a JOIN users u ON u.id = a.created_by_user_id",
    )
    assert sorted((a.amount, a.reason, a.username) for a in adjustments) == [
        (D("-450.00"), TL_VOID_REASON, "demo.finance"),
        (D("-360.00"), X7_ADJUSTMENT_REASON, "demo.finance"),
        (D("150.00"), X6_ADJUSTMENT_REASON, "demo.finance"),
    ]
    [x9] = run_sql(
        migrated_url,
        "SELECT c.name, c.is_active AS category_active, v.name AS vendor,"
        " v.is_active AS vendor_active FROM expenses x"
        " JOIN expense_categories c ON c.id = x.category_id"
        " JOIN vendors v ON v.id = x.vendor_id WHERE x.id = :id",
        {"id": recorded.expenses["X9"]},
    )
    assert tuple(x9) == (FURNITURE, False, VENDORS[3].name, False)
    creators = run_sql(
        migrated_url,
        "SELECT DISTINCT u.username FROM expenses x"
        " JOIN users u ON u.id = x.created_by_user_id",
    )
    assert [c.username for c in creators] == ["demo.finance"]

    # The audit actors: demo.finance for every write but X5's reversal
    # (demo.owner) and the job steps (none).
    actors = _actors(
        migrated_url,
        (
            "service_prices",
            "expense_categories",
            "vendors",
            "expense_templates",
            "expenses",
            "expense_payments",
            "expense_payment_reversals",
            "expense_adjustments",
        ),
    )
    assert actors[("expense_categories", "create")] == {"demo.finance"}
    assert actors[("expense_categories", "update")] == {"demo.finance"}
    assert actors[("vendors", "create")] == {"demo.finance"}
    assert actors[("vendors", "update")] == {"demo.finance"}
    assert actors[("expense_templates", "create")] == {"demo.finance"}
    # The job's advance has no actor; the pause is demo.finance's.
    assert actors[("expense_templates", "update")] == {None, "demo.finance"}
    assert actors[("expenses", "create")] == {None, "demo.finance"}
    assert actors[("expense_payments", "create")] == {"demo.finance"}
    assert actors[("expense_payment_reversals", "create")] == {"demo.owner"}
    assert actors[("expense_adjustments", "create")] == {"demo.finance"}
    assert actors[("service_prices", "create")] == {"demo.finance"}
    assert (
        demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_501)
        == TABLE_COUNTS_THROUGH_501
    )


def test_the_jobs_do_nothing_after_the_seed_and_a_rerun_writes_nothing(
    migrated_url, env_for, clock, capsys
):
    # F7: worker parity on the same frozen day; then F4: a rerun the same day
    # and 10 days later prints 0 rows (the markers come first).
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()
    settings = settings_for(migrated_url)
    before = demo_table_counts(migrated_url, ALL_TABLE_COUNTS)

    generated = asyncio.run(run_generate_recurring_expenses(settings))
    enrollments = asyncio.run(run_post_enrollment_charges(settings))
    reminders = asyncio.run(run_send_charge_reminders(settings))

    assert dataclasses.astuple(generated) == (0, 0, 0, 0)
    assert (enrollments.posted, enrollments.no_price, enrollments.failed) == (0, 0, 0)
    assert reminders.sent_charges == 0
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == before
    capsys.readouterr()  # the jobs' log lines

    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS
    assert app_clock.now() == FROZEN_NOW + timedelta(days=10)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 11, 8, 5, 30, tzinfo=UTC),  # Saturday 23:30 local
        datetime(2026, 11, 8, 18, 0, tzinfo=UTC),  # Sunday 12:00 local
    ],
    ids=["saturday-night", "sunday"],
)
def test_a_weekend_run(migrated_url, env_for, clock, capsys, now):
    # F3: F = 10-19, so TL has three weeks (8 generated expenses).
    clock.set(now)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert _finance_lines(lines) == [
        _line(FINANCE_GENERATORS[0], DEMO_PAST_COHORT_FEE_ROWS),
        _line(FINANCE_GENERATORS[1], DEMO_FINANCE_SETUP_ROWS),
        _line(FINANCE_GENERATORS[2], WEEKEND_TEMPLATES_ROWS),
        _line(FINANCE_GENERATORS[3], DEMO_FINANCE_EXPENSES_ROWS),
    ]
    tl = run_sql(
        migrated_url,
        "SELECT x.period_start FROM expenses x JOIN expense_templates t"
        " ON t.id = x.expense_template_id WHERE t.name = 'Limpieza semanal'"
        " ORDER BY 1",
    )
    assert [r.period_start for r in tl] == [
        date(2026, 10, 19) + timedelta(weeks=k) for k in range(3)
    ]
    assert count(migrated_url, "expenses") == 8 + 9
    assert app_clock.now() == now


def test_dev_writes_no_finance_data(migrated_url, env_for, capsys):
    # F5.
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    _no_finance_data(migrated_url)


def test_without_billing_leaves_the_finance_data_out(
    migrated_url, env_for, clock, capsys
):
    # F5: the E2E seed. The four print the flag's skip; #353's cohort stays
    # unpriced; the categories are untouched; the other lines are unchanged.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main(["--without-billing"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"{line.split(':')[0]}: skipped ({WITHOUT_BILLING})"
        if line.split(":")[0] in FLAGGED_GENERATORS
        else line
        for line in FIRST_RUN_LINES
    ]
    assert _finance_lines(lines) == [
        f"{name}: skipped ({WITHOUT_BILLING})" for name in FINANCE_GENERATORS
    ]
    _no_finance_data(migrated_url)
    assert _cohort_prices(migrated_url) == 0
    assert (
        demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_354)
        == TABLE_COUNTS_THROUGH_354
    )


def test_the_finance_generators_come_last():
    # A1.
    assert len(GENERATOR_NAMES) == 40
    assert [g.name for g in demo.DEMO_GENERATORS] == GENERATOR_NAMES
    # M8-demo E7: the four M8 generators come after them.
    after = GENERATOR_NAMES.index(PAYROLL_CORRECTIONS_GENERATOR) + 1
    assert GENERATOR_NAMES[after : after + 4] == list(FINANCE_GENERATORS)
    assert GENERATOR_NAMES[after + 4 :] == list(ADMIN_GENERATORS[1:])
    after = FLAGGED_GENERATORS.index(PAYROLL_CORRECTIONS_GENERATOR) + 1
    assert FLAGGED_GENERATORS[after : after + 4] == FINANCE_GENERATORS
    assert (
        sum(DEMO_FINANCE_TABLE_COUNTS.values()) - SEEDED_CATEGORIES
        == DEMO_PAST_COHORT_FEE_ROWS
        + DEMO_FINANCE_SETUP_ROWS
        + DEMO_FINANCE_TEMPLATES_ROWS
        + DEMO_FINANCE_EXPENSES_ROWS
    )


# --- F6: stale data, hand-configured data, prerequisites, rollback --------------------


def test_data_from_an_earlier_day_is_skipped(migrated_url, env_for, clock, capsys):
    # The generators through payroll.demo_corrections on 2026-11-04, then
    # every generator 10 days later.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = generators_through(PAYROLL_CORRECTIONS_GENERATOR)
    assert demo.main([], generators=generators) == 0
    capsys.readouterr()

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *RERUN_REFERENCE_LINES,
        *(
            f"{name}: skipped ({STALE_SCHEDULE})"
            # The receipt message ran with the prefix (0 rows on the rerun).
            if name in {*FINANCE_GENERATORS, *ADMIN_GENERATORS[1:]}
            else f"{name}: 0 rows"
            for name in GENERATOR_NAMES
        ),
    ]
    _no_finance_data(migrated_url)
    assert _cohort_prices(migrated_url) == 0
    assert (
        demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_499)
        == TABLE_COUNTS_THROUGH_499
    )


async def _by_hand(url: str, what: str) -> None:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            session = uow.session
            if what == "vendor":
                await finance.add_vendor(
                    session, VendorCreate(name="Ferretería del Centro")
                )
                return
            [rent] = [
                c
                for c in await finance.list_categories(session)
                if c.system_key == "rent"
            ]
            if what == "rename":
                await finance.change_category(
                    session,
                    rent.id,
                    ExpenseCategoryUpdate(version=rent.version, name="Arrendamiento"),
                )
                return
            owner_id = await demo_user_id(session, DEMO_OWNER_USERNAME, "test")
            await finance.create_expense(
                session,
                owner_id,
                ExpenseCreate(
                    category_id=rent.id,
                    description="Renta de octubre",
                    amount=Money("5000.00"),
                    incurred_on=date(2026, 11, 1),
                    due_on=date(2026, 11, 5),
                ),
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("what", ["vendor", "expense", "rename"])
def test_expenses_set_up_by_hand_are_skipped(
    migrated_url, env_for, clock, capsys, what
):
    # G9.4: a vendor, an expense or a category rename made by hand first:
    # the three finance generators skip; the cohort fee still posts.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = generators_through(PAYROLL_CORRECTIONS_GENERATOR)
    assert demo.main([], generators=generators) == 0
    capsys.readouterr()
    asyncio.run(_by_hand(migrated_url, what))
    vendors, expenses = count(migrated_url, "vendors"), count(migrated_url, "expenses")

    assert demo.main([]) == 0

    assert _finance_lines(capsys.readouterr().out.splitlines()) == [
        _line(FINANCE_GENERATORS[0], DEMO_PAST_COHORT_FEE_ROWS),
        *(f"{name}: skipped ({FINANCE_CONFIGURED})" for name in FINANCE_GENERATORS[1:]),
    ]
    assert (count(migrated_url, "vendors"), count(migrated_url, "expenses")) == (
        vendors,
        expenses,
    )
    for table in ("expense_templates", "expense_payments", "expense_adjustments"):
        assert count(migrated_url, table) == 0, table
    assert _cohort_prices(migrated_url) == 1


def test_billing_set_up_by_hand_skips_the_cohort_fee(
    migrated_url, env_for, clock, capsys
):
    # G9.4: M6-demo's pricing marker (the sibling discount) is missing because
    # billing was set up by hand; the finance data still seeds.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    through = generators_through("academics.demo_progress_reports")
    assert demo.main([], generators=through) == 0
    capsys.readouterr()
    run_sql(
        migrated_url,
        "UPDATE billing_settings SET billing_starts_on = :d",
        {"d": date(2026, 11, 2)},
    )

    assert demo.main([]) == 0

    assert _finance_lines(capsys.readouterr().out.splitlines()) == [
        f"{FINANCE_GENERATORS[0]}: skipped ({BILLING_CONFIGURED})",
        _line(FINANCE_GENERATORS[1], DEMO_FINANCE_SETUP_ROWS),
        _line(FINANCE_GENERATORS[2], DEMO_FINANCE_TEMPLATES_ROWS),
        _line(FINANCE_GENERATORS[3], DEMO_FINANCE_EXPENSES_ROWS),
    ]
    assert _cohort_prices(migrated_url) == 0


@pytest.mark.parametrize("missing", ["cohort", "owner"])
def test_a_missing_prerequisite_fails_the_run(migrated_url, env_for, clock, missing):
    # G9.5: #353's cohort or demo.owner missing (renamed); nothing of the four
    # is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = generators_through(PAYROLL_CORRECTIONS_GENERATOR)
    assert demo.main([], generators=generators) == 0
    if missing == "cohort":
        run_sql(
            migrated_url,
            "UPDATE course_cohorts SET name = 'Otro grupo' WHERE name = :n",
            {"n": COHORT_NAME},
        )
    else:
        run_sql(
            migrated_url,
            "UPDATE users SET username = 'otro.owner' WHERE username = 'demo.owner'",
        )
    charges = count(migrated_url, "charges")

    with pytest.raises(DemoPrerequisiteMissingError):
        demo.main([], generators=_fresh_finance()[-4:])

    _no_finance_data(migrated_url)
    assert count(migrated_url, "charges") == charges
    assert count(migrated_url, "service_prices") == 8


def _overpaid_x4(today: date) -> DemoFinancePlan:
    """X4's second payment above the open amount (1,700.00 → 1,800.00)."""
    plan = plan_demo_finance(today)
    expenses = []
    for planned in plan.expenses:
        if planned.key == "X4":
            first, second = planned.steps
            assert isinstance(second, Pay)
            planned = dataclasses.replace(
                planned,
                steps=(
                    first,
                    Pay(dataclasses.replace(second.payment, amount="1800.00")),
                ),
            )
        expenses.append(planned)
    return dataclasses.replace(plan, expenses=tuple(expenses))


def test_a_refusal_rolls_the_run_back(migrated_url, env_for, clock):
    # F6: X4's second payment above its open amount is refused; everything
    # rolls back (PR7, the fees, the vendors, the templates); the clock and
    # the session actor are restored.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = generators_through(PAYROLL_CORRECTIONS_GENERATOR)
    assert demo.main([], generators=generators) == 0
    charges = count(migrated_url, "charges")

    with pytest.raises(ExpensePaymentExceedsOpenError):
        demo.main([], generators=_fresh_finance(_overpaid_x4)[-4:])

    assert app_clock.now() == FROZEN_NOW
    _no_finance_data(migrated_url)
    assert _cohort_prices(migrated_url) == 0
    assert count(migrated_url, "charges") == charges

    # The actor and the clock: the generator on a session of a seeded
    # database (setup and templates committed first).
    assert demo.main([], generators=_fresh_finance()[-4:-1]) == 0
    asyncio.run(_refused_in_a_session(migrated_url))
    assert count(migrated_url, "expense_payments") == 3


async def _refused_in_a_session(url: str) -> None:
    engine = short_lived_engine(url)
    try:
        with pytest.raises(ExpensePaymentExceedsOpenError):
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                saved = uow.session.info[ACTOR_INFO_KEY]
                try:
                    await DemoFinanceExpensesGenerator(plan=_overpaid_x4).generate(
                        uow.session
                    )
                finally:
                    assert uow.session.info[ACTOR_INFO_KEY] is saved
                    assert app_clock.now() == FROZEN_NOW
    finally:
        await engine.dispose()


def test_the_x4_double_only_changes_the_second_payment():
    # The rollback test's plan double is the plan but for that one amount.
    plan, double = plan_demo_finance(T), _overpaid_x4(T)
    assert [x.key for x in double.expenses] == [x.key for x in plan.expenses]
    assert double.expense("X4").payments == (
        plan.expense("X4").payments[0],
        PlannedPayment("1800.00", date(2026, 10, 29), "transfer", "PRY-DEMO-0002"),
    )
