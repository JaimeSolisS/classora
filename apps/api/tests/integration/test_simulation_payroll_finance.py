"""The simulated payroll and operating expenses against PostgreSQL (#553
HF-01e: B, C). One ``small`` Spanish run (shared, read only) with the real
clock frozen at :data:`~tests.integration.simulation_support.FROZEN_NOW`;
the HF-01d-only rerun (B6) uses its own database. B6's run-date
independence is #543 C4's test (``test_simulation_seed.py``, whose
canonical dump holds the payroll and finance tables), the English text
check is #543 D4's test there, and the full profile's pinned total is
checked by both full runs. The job parity test (B5) runs last on the shared
database. Payslip PDFs are rendered only in CI or the API image (no native
Pango); this file reads the stored payslips."""

import asyncio
import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.reporting import finance as reports
from app.modules.reporting.schemas import Profitability
from app.workflows import payroll_adjustments, payroll_periods, recurring_expenses
from tests.factories.simulation import SIMULATION_MODULES
from tests.factories.simulation.anchor import SIM_ANCHOR, SIM_NOW, sim_clock
from tests.factories.simulation.plan import plan_simulation
from tests.factories.simulation.timeline import SEEDED_EARLIER
from tests.factories.simulation.timetable import fortnight_end
from tests.integration.auth_support import auth_client, login
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.simulation_support import (
    MODULE_ROWS,
    migrate,
    payroll_free_text,
    payroll_texts,
    run_command,
    simulation_env,
    table_counts,
)

PLAN = plan_simulation("es", "small", 1)
PAYROLL = PLAN.payroll
REPORT_FROM = date(2026, 9, 1)
REPORT_TO = date(2026, 11, 18)


def _fresh(database_url: str) -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    url = make_url(database_url).set(database=name).render_as_string(False)
    try:
        migrate(url)
        yield url
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


SMALL_RUN: dict[str, list[str]] = {}


@pytest.fixture(scope="module")
def es_url(database_url: str) -> Iterator[str]:
    for url in _fresh(database_url):
        code, out, err = run_command(url, "es")
        assert code == 0, err
        SMALL_RUN["out"] = out
        yield url


def rows(url: str, sql: str, **params: Any) -> list[Any]:
    return run_sql(url, sql, params or None)


def scalar(url: str, sql: str, **params: Any) -> Any:
    return rows(url, sql, **params)[0][0]


def user_id(url: str, username: str) -> Any:
    return scalar(url, "SELECT id FROM users WHERE username = :u", u=username)


UNREVERSED = (
    "NOT EXISTS (SELECT 1 FROM payroll_payment_reversals r"
    " WHERE r.payroll_payment_id = pp.id)"
)
PERIODS = (
    "SELECT p.id, u.username, p.starts_on, p.ends_on, p.pay_frequency,"  # noqa: S608
    " p.status,"
    " p.total_amount,"
    " (SELECT COALESCE(sum(l.amount), 0) FROM payroll_lines l"
    "  WHERE l.payroll_period_id = p.id) AS lines,"
    " (SELECT COALESCE(sum(pp.amount), 0) FROM payroll_payments pp"
    f"  WHERE pp.payroll_period_id = p.id AND {UNREVERSED}) AS paid"
    " FROM payroll_periods p JOIN users u ON u.id = p.tutor_user_id"
)
EXPENSES = (
    "SELECT e.id, e.description, e.incurred_on, e.amount"
    " + COALESCE((SELECT sum(a.amount) FROM expense_adjustments a"
    "   WHERE a.expense_id = e.id), 0) AS net,"
    " COALESCE((SELECT sum(ep.amount) FROM expense_payments ep"
    "   WHERE ep.expense_id = e.id AND NOT EXISTS (SELECT 1"
    "   FROM expense_payment_reversals r WHERE r.expense_payment_id = ep.id)), 0)"
    " AS paid FROM expenses e"
)


# --- B1, B3: every table has rows and every kind is present ---------------------------


def test_the_run_prints_the_payroll_and_finance_totals(es_url):
    assert SMALL_RUN["out"][-2:] == [
        f"payroll: {MODULE_ROWS['small']['payroll']} rows",
        f"finance: {MODULE_ROWS['small']['finance']} rows",
    ]


def test_every_payroll_and_finance_table_has_rows(es_url):
    for table in (
        "tutor_pay_terms",
        "compensation_rates",
        "session_pay_rules",
        "payroll_settings",
        "payroll_periods",
        "pay_item_types",
        "pay_items",
        "payroll_lines",
        "payroll_adjustments",
        "payroll_payments",
        "payroll_payment_reversals",
        "payslips",
        "expense_categories",
        "vendors",
        "expenses",
        "expense_payments",
        "expense_payment_reversals",
        "expense_adjustments",
        "expense_templates",
    ):
        assert scalar(es_url, f"SELECT count(*) FROM {table}") > 0, table  # noqa: S608
    kinds = {r.kind for r in rows(es_url, "SELECT kind FROM payroll_adjustments")}
    assert kinds == {"late_approval", "hours_correction", "manual", "carry_forward"}
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM session_pay_rules"
            " WHERE created_by_user_id IS NOT NULL AND mode = 'percent'"
            " AND percent = 50",
        )
        == 2
    )
    frequencies = dict(
        rows(es_url, "SELECT pay_frequency, count(*) FROM tutor_pay_terms GROUP BY 1")
    )
    assert frequencies == {"biweekly": 3, "monthly": 1}
    assert scalar(es_url, "SELECT payroll_starts_on FROM payroll_settings") == date(
        2026, 8, 31
    )


def test_work_after_lock_lands_in_the_next_open_period(es_url):
    """B1: the two late group reviews and the correction after lock are
    adjustments in the tutor's period after the one the work is dated in;
    the carry-forward pair nets to zero."""
    found = rows(
        es_url,
        "SELECT a.kind, a.amount, t.starts_on AS target,"
        " (s.starts_at AT TIME ZONE 'America/Mexico_City')::date AS class_day"
        " FROM payroll_adjustments a JOIN payroll_periods t"
        " ON t.id = a.target_period_id"
        " LEFT JOIN class_sessions s ON s.id = a.class_session_id"
        " WHERE a.kind IN ('late_approval', 'hours_correction')",
    )
    assert Counter(r.kind for r in found) == {"late_approval": 2, "hours_correction": 1}
    for row in found:
        # Posted the evening of the work, into the next fortnight (not at the
        # anchor, which would land in 9-22 Nov).
        assert row.target == fortnight_end(row.class_day) + timedelta(days=1)
        assert (row.amount > 0) == (row.kind == "late_approval")
    carried = rows(
        es_url, "SELECT amount FROM payroll_adjustments WHERE kind = 'carry_forward'"
    )
    assert sorted(r.amount for r in carried)[0] == -sorted(r.amount for r in carried)[1]


# --- B2, B4: period states and identities ---------------------------------------------


def test_ended_periods_are_finalized_and_paid_and_the_anchors_is_open(es_url):
    periods = rows(es_url, PERIODS)
    assert periods
    for p in periods:
        if p.ends_on < SIM_ANCHOR:
            assert p.status == "finalized", p
            # B4: the total is the sum of its lines; B2: fully paid.
            assert p.total_amount == p.lines == p.paid, p
        else:
            assert p.status == "open", p
            assert p.starts_on <= SIM_ANCHOR, p
    tutors = {p.username for p in periods}
    assert len(tutors) == len(PLAN.tutors)
    for username in tutors:
        own = [p for p in periods if p.username == username]
        assert sum(1 for p in own if p.status == "open") == 1
    assert {(p.pay_frequency, p.ends_on) for p in periods if p.status == "open"} == {
        ("biweekly", date(2026, 11, 22)),
        ("monthly", date(2026, 11, 30)),
    }
    assert (
        scalar(es_url, "SELECT count(*) FROM payroll_periods WHERE status = 'prepared'")
        == 0
    )


def test_payslips_in_the_run_language_one_per_finalized_period_with_lines(es_url):
    expected = scalar(
        es_url,
        "SELECT count(*) FROM payroll_periods p WHERE p.status = 'finalized'"
        " AND EXISTS (SELECT 1 FROM payroll_lines l WHERE l.payroll_period_id = p.id)",
    )
    assert dict(rows(es_url, "SELECT language, count(*) FROM payslips GROUP BY 1")) == {
        "es": expected
    }


def test_nothing_is_dated_on_or_after_the_anchor(es_url):
    assert scalar(es_url, "SELECT max(paid_on) FROM payroll_payments") < SIM_ANCHOR
    assert scalar(es_url, "SELECT max(paid_on) FROM expense_payments") < SIM_ANCHOR
    assert scalar(es_url, "SELECT max(incurred_on) FROM expenses") < SIM_ANCHOR
    assert scalar(es_url, "SELECT max(finalized_at) FROM payroll_periods") < SIM_NOW
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM payroll_periods WHERE starts_on > :a",
            a=SIM_ANCHOR,
        )
        == 0
    )


def test_every_expense_is_paid_or_voided(es_url):
    expenses = rows(es_url, EXPENSES)
    assert len(expenses) == len(PLAN.expenses.expenses)
    voided = [e for e in expenses if e.net == 0]
    assert voided
    for e in expenses:
        assert e.incurred_on < SIM_ANCHOR
        assert e.paid == e.net, e
    assert scalar(es_url, "SELECT count(*) FROM vendors WHERE NOT is_active") == 1
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM expense_templates WHERE ends_on > '2026-12-05'",
        )
        == 0
    )
    cleaning = {
        r.period_start
        for r in rows(
            es_url,
            "SELECT e.period_start FROM expenses e JOIN expense_templates t"
            " ON t.id = e.expense_template_id WHERE t.name = :n",
            n=PLAN.expenses.template("cleaning").name,
        )
    }
    assert date(2026, 10, 12) in cleaning and date(2026, 11, 2) in cleaning
    assert not {date(2026, 10, 19), date(2026, 10, 26)} & cleaning


def _profitability(url: str, basis: str) -> Profitability:
    async def read() -> Profitability:
        engine = short_lived_engine(url)
        try:
            async with AsyncSession(engine) as session:
                with sim_clock(SIM_NOW):
                    return await reports.profitability_report(
                        session,
                        basis=basis,  # type: ignore[arg-type]
                        start=REPORT_FROM,
                        end=REPORT_TO,
                    )
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_the_financial_report_matches_the_payroll_and_finance_sums(es_url):
    """B4: the #117 profitability report for 1 Sep - 18 Nov, accrual and
    cash, against the sums read from the payroll and finance tables."""
    period = {"a": REPORT_FROM, "b": REPORT_TO}
    finalized = scalar(
        es_url,
        "SELECT sum(total_amount) FROM payroll_periods WHERE status = 'finalized'"
        " AND ends_on BETWEEN :a AND :b",
        **period,
    )
    payroll_paid = scalar(
        es_url,
        f"SELECT sum(pp.amount) FROM payroll_payments pp WHERE {UNREVERSED}"  # noqa: S608
        " AND pp.paid_on BETWEEN :a AND :b",
        **period,
    )
    net = sum((e.net for e in rows(es_url, EXPENSES)), Decimal(0))
    expense_paid = sum((e.paid for e in rows(es_url, EXPENSES)), Decimal(0))

    accrual = _profitability(es_url, "accrual")
    cash = _profitability(es_url, "cash")

    assert accrual.direct_tutor_cost_total.amount == finalized
    assert accrual.operating_expenses.total.amount == net
    assert cash.direct_tutor_cost_total.amount == payroll_paid
    assert cash.operating_expenses.total.amount == expense_paid
    assert finalized > 0 and net > 0


# --- B6, B7: rerun, skip, language ----------------------------------------------------


def test_a_rerun_writes_nothing(es_url):
    before = table_counts(es_url)

    code, out, _ = run_command(es_url, "es")

    assert code == 0
    assert out[-2:] == ["payroll: 0 rows", "finance: 0 rows"]
    assert table_counts(es_url) == before


def test_a_database_seeded_by_hf01d_only_skips_payroll_and_finance(database_url):
    for url in _fresh(database_url):
        assert run_command(url, "es", modules=SIMULATION_MODULES[:8])[0] == 0
        before = table_counts(url)

        code, out, _ = run_command(url, "es")

        assert code == 0
        assert out[-3:] == [
            "billing: 0 rows",
            f"payroll: skipped ({SEEDED_EARLIER})",
            f"finance: skipped ({SEEDED_EARLIER})",
        ]
        assert table_counts(url) == before


def test_payroll_and_finance_text_comes_from_the_spanish_pool(es_url):
    found = [value for _, value in payroll_free_text(es_url)]
    assert found and set(found) <= payroll_texts("es")


# --- C1: per account, through the API -------------------------------------------------


def test_who_sees_which_payslip_and_expense(es_url):
    tutor = next(
        t
        for t in PLAN.tutors
        if t.key not in PAYROLL.salaried and t.key != PAYROLL.joiner
    )
    other = next(t for t in PLAN.tutors if t.key != tutor.key)
    own_id = user_id(es_url, tutor.account.username)
    other_id = user_id(es_url, other.account.username)
    finalized = rows(
        es_url,
        "SELECT id FROM payroll_periods WHERE tutor_user_id = :t"
        " AND status = 'finalized'",
        t=own_id,
    )
    open_period = scalar(
        es_url,
        "SELECT id FROM payroll_periods WHERE tutor_user_id = :t AND status = 'open'",
        t=own_id,
    )
    payment = scalar(es_url, "SELECT id FROM payroll_payments LIMIT 1")
    with auth_client(es_url) as client:
        account = tutor.account
        assert login(client, account.username, account.password).status_code == 200
        mine = client.get(f"/api/v1/payroll/tutors/{own_id}/earnings")
        assert mine.status_code == 200, mine.text
        assert {i["id"] for i in mine.json()["items"]} == {str(r.id) for r in finalized}
        assert (
            client.get(f"/api/v1/payroll/tutors/{own_id}/earnings/{open_period}")
        ).status_code == 404
        assert (
            client.get(f"/api/v1/payroll/tutors/{other_id}/earnings")
        ).status_code == 404
        assert client.get("/api/v1/payroll/periods").status_code == 403
    for staff in (PLAN.finance, PLAN.owner):
        with auth_client(es_url) as client:
            assert login(client, staff.username, staff.password).status_code == 200
            for tutor_id in (own_id, other_id):
                response = client.get(f"/api/v1/payroll/tutors/{tutor_id}/earnings")
                assert response.status_code == 200, response.text
            assert client.get("/api/v1/payroll/periods").status_code == 200
            assert client.get("/api/v1/finance/expenses").status_code == 200
    for staff in (PLAN.administrator, PLAN.coordinator):
        with auth_client(es_url) as client:
            assert login(client, staff.username, staff.password).status_code == 200
            assert client.get("/api/v1/payroll/periods").status_code == 403
            assert client.get("/api/v1/finance/expenses").status_code == 403
    with auth_client(es_url) as client:
        owner = PLAN.owner
        assert login(client, owner.username, owner.password).status_code == 200
        events = client.get(
            "/api/v1/audit-events",
            params={"record_type": "payroll_payments", "record_id": str(payment)},
        )
        assert events.status_code == 200, events.text
        [created] = [e for e in events.json()["items"] if e["action"] == "create"]
        for field in ("amount", "reference"):
            assert created["changes"][field]["new"] == "[masked]"


def test_masked_payroll_and_expense_fields_stay_masked_in_the_audit_log(es_url):
    for table, fields in (
        ("compensation_rates", ("rate",)),
        ("tutor_pay_terms", ("salary_amount",)),
        ("payroll_adjustments", ("amount", "reason")),
        ("payroll_payments", ("amount", "reference")),
        ("pay_items", ("amount", "description")),
        ("expense_templates", ("name", "amount")),
        ("expense_adjustments", ("amount", "reason")),
    ):
        changes = [
            r.changes
            for r in rows(
                es_url,
                "SELECT changes FROM audit_events WHERE record_type = :t"
                " AND action = 'create'",
                t=table,
            )
        ]
        assert changes, table
        for change in changes:
            for field in fields:
                assert change[field]["new"] == "[masked]", (table, field)


# --- B5: job parity at the anchor (last: it would change the shared database) ---------


def test_zz_the_payroll_and_expense_jobs_have_nothing_to_do_at_the_anchor(es_url):
    assert scalar(es_url, "SELECT count(*) FROM procrastinate_jobs") == 0
    before = table_counts(es_url)

    with simulation_env(es_url), sim_clock(SIM_NOW):
        settings = get_settings()
        opened = asyncio.run(payroll_periods.run_open_periods(settings))
        adjusted = asyncio.run(payroll_adjustments.run_post_adjustments(settings))
        generated = asyncio.run(
            recurring_expenses.run_generate_recurring_expenses(settings)
        )

    assert (opened.opened, opened.failed) == (0, 0)
    assert (adjusted.posted, adjusted.failed) == (0, 0)
    assert (generated.generated, generated.failed) == (0, 0)
    assert table_counts(es_url) == before
    assert scalar(es_url, "SELECT count(*) FROM procrastinate_jobs") == 0
