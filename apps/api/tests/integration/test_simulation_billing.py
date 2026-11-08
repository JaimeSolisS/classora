"""The simulated billing against PostgreSQL (#552 HF-01d: B, C). One
``small`` Spanish run (shared, read only) with the real clock frozen at
:data:`~tests.integration.simulation_support.FROZEN_NOW`; the HF-01c-only
rerun (B6) uses its own database. B6's run-date independence is #543 C4's
test (``test_simulation_seed.py``, whose canonical dump holds the billing
tables), the English text check is #543 D4's test there, and the full
profile's pinned total is checked by both full runs. The job parity test
(B5) runs last on the shared database. Receipt PDFs are rendered only in
CI or the API image (no native Pango)."""

import asyncio
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.workflows import (
    charge_reminders,
    enrollment_charges,
    package_expiry,
    schedule_reconciliation,
    session_charges,
    tuition_charges,
)
from tests.factories.simulation import SIMULATION_MODULES
from tests.factories.simulation.anchor import SIM_ANCHOR, SIM_NOW, ZONE, sim_clock
from tests.factories.simulation.plan import plan_simulation
from tests.factories.simulation.texts import RECEIPT_MESSAGES
from tests.factories.simulation.timeline import SEEDED_EARLIER
from tests.integration.auth_support import auth_client, login
from tests.integration.billing_invariants.checks import Catalogue
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.receipt_support import pdf_text, receipt_path
from tests.integration.simulation_support import (
    billing_free_text,
    billing_texts,
    migrate,
    run_command,
    simulation_env,
    table_counts,
)

PLAN = plan_simulation("es", "small", 1)
BILLING = PLAN.billing


def _fresh(database_url: str) -> Iterator[str]:
    import uuid

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


def counts(url: str, table: str, column: str) -> dict[str, int]:
    return dict(
        rows(
            url,
            f"SELECT {column}, count(*) FROM {table} GROUP BY {column}",  # noqa: S608
        )
    )


NET = (
    "c.amount + COALESCE((SELECT sum(a.amount) FROM charge_adjustments a"
    " WHERE a.charge_id = c.id), 0)"
)
ALLOCATED = (
    "COALESCE((SELECT sum(pa.amount) FROM payment_allocations pa"
    " WHERE pa.charge_id = c.id AND NOT EXISTS (SELECT 1 FROM allocation_releases r"
    " WHERE r.allocation_id = pa.id)), 0)"
)
DUE = (
    "COALESCE((SELECT d.due_date FROM charge_due_date_changes d"
    " WHERE d.charge_id = c.id ORDER BY d.created_at DESC, d.id DESC LIMIT 1),"
    " c.due_date)"
)
PAID_ON = (
    "(SELECT max(p.received_on) FROM payment_allocations pa JOIN payments p"
    " ON p.id = pa.payment_id WHERE pa.charge_id = c.id AND NOT EXISTS"
    " (SELECT 1 FROM allocation_releases r WHERE r.allocation_id = pa.id))"
)
CHARGES = (
    f"SELECT c.id, c.kind, c.billing_account_id AS account, {NET} AS net,"  # noqa: S608
    f" {ALLOCATED} AS allocated, {DUE} AS due, {PAID_ON} AS paid_on FROM charges c"
)


def family_id(url: str, key: str) -> Any:
    family = next(f for f in PLAN.families if f.key == key)
    return scalar(url, "SELECT id FROM families WHERE name = :n", n=family.name)


# --- B1: every billing table the plan names has rows ----------------------------------


def test_the_run_prints_the_billing_total(es_url):
    from tests.integration.simulation_support import MODULE_ROWS

    assert f"billing: {MODULE_ROWS['small']['billing']} rows" in SMALL_RUN["out"]


def test_every_billing_table_has_rows_and_every_kind_is_present(es_url):
    for table in (
        "service_prices",
        "price_versions",
        "custom_prices",
        "discounts",
        "discount_assignments",
        "charge_discounts",
        "tuition_plans",
        "tuition_plan_pauses",
        "package_products",
        "packages",
        "payments",
        "payment_allocations",
        "payment_reversals",
        "allocation_releases",
        "refunds",
        "withdrawals",
        "charge_due_date_changes",
        "charge_reminders",
        "receipts",
        "session_cancellation_decisions",
        "charge_duration_repricings",
    ):
        assert scalar(es_url, f"SELECT count(*) FROM {table}") > 0, table  # noqa: S608
    # One price change; discounts of each kind, one expired, one upcoming.
    assert scalar(
        es_url,
        "SELECT count(*) FROM price_versions WHERE effective_from = '2026-10-01'",
    ) == len(BILLING.versions)
    assert set(counts(es_url, "discounts", "kind")) == {
        "sibling",
        "scholarship",
        "promotion",
        "discount",
    }
    assert (
        scalar(
            es_url, "SELECT count(*) FROM discounts WHERE expires_on < :a", a=SIM_ANCHOR
        )
        == 1
    )
    assert (
        scalar(
            es_url, "SELECT count(*) FROM discounts WHERE starts_on > :a", a=SIM_ANCHOR
        )
        == 1
    )
    assert set(counts(es_url, "package_credit_transactions", "kind")) >= {
        "grant",
        "consume",
        "expire",
        "refund",
    }
    assert set(counts(es_url, "charges", "kind")) == {
        "session",
        "group_enrollment",
        "cohort_enrollment",
        "tuition_period",
        "package_purchase",
        "manual",
    }
    assert len(counts(es_url, "charge_adjustments", "kind")) >= 4
    assert scalar(es_url, "SELECT count(*) FROM billing_accounts") == len(PLAN.families)
    assert scalar(es_url, "SELECT count(*) FROM charge_due_date_changes") >= 2
    assert scalar(es_url, "SELECT count(*) FROM charges WHERE kind = 'manual'") >= 2


def test_the_package_story(es_url):
    """Renewed after use, an intensive one lapsing with units left, one
    cancelled with a refund and one withdrawn (A2: the cancelled student
    moves to a plan on 1 Nov)."""
    expired = scalar(
        es_url,
        "SELECT min(t.units) FROM package_credit_transactions t"
        " WHERE t.kind = 'expire'",
    )
    assert expired < 0
    assert scalar(es_url, "SELECT count(*) FROM packages") >= 5
    assert counts(es_url, "withdrawals", "outcome") == {"account_credit": 1}
    assert scalar(es_url, "SELECT count(*) FROM refunds") >= 1
    cancelled = next(p for p in BILLING.packages if p.cancelled)
    student = next(s for s in PLAN.students if s.key == cancelled.student)
    starts = rows(
        es_url,
        "SELECT t.starts_on FROM tuition_plans t JOIN students s"
        " ON s.id = t.student_id WHERE s.given_names = :g AND s.family_names = :f",
        g=student.given_names,
        f=student.family_names,
    )
    assert [row[0] for row in starts] == [date(2026, 11, 1)]


# --- B2: final states at the anchor ---------------------------------------------------


def test_charges_due_before_the_anchor_are_settled_or_a_few_overdue(es_url):
    due = [
        row
        for row in rows(es_url, CHARGES)
        if row.due < SIM_ANCHOR and Decimal(row.net) > 0
    ]
    open_ = [row for row in due if Decimal(row.net) > Decimal(row.allocated)]
    late = [
        row
        for row in due
        if row not in open_ and row.paid_on is not None and row.paid_on > row.due
    ]
    assert len(open_) <= 0.05 * len(due)
    assert len(late) <= 0.15 * len(due)
    assert all((r.paid_on - r.due).days <= 20 for r in late)
    overdue_accounts = {row.account for row in open_}
    assert len(overdue_accounts) >= len(BILLING.overdue_families)
    reminded = {
        row[0]
        for row in rows(
            es_url,
            "SELECT c.billing_account_id FROM charge_reminders r"
            " JOIN charges c ON c.id = r.charge_id WHERE r.stage <> 'due_soon'",
        )
    }
    assert overdue_accounts <= reminded


def test_nothing_is_paid_on_or_after_the_anchor_and_no_december_tuition(es_url):
    assert scalar(es_url, "SELECT max(received_on) FROM payments") < SIM_ANCHOR
    assert scalar(es_url, "SELECT max(refunded_on) FROM refunds") < SIM_ANCHOR
    assert scalar(es_url, "SELECT max(issued_at) FROM receipts") < SIM_NOW
    # A reversal stores no business date (created_at is the real run time,
    # E7): the reversed payment and the plan's instant are before the anchor.
    assert BILLING.reversal.at < SIM_NOW
    assert (
        scalar(
            es_url,
            "SELECT max(p.received_on) FROM payment_reversals x"
            " JOIN payments p ON p.id = x.payment_id",
        )
        < SIM_ANCHOR
    )
    assert (
        scalar(
            es_url,
            "SELECT count(*) FROM charges WHERE kind = 'tuition_period'"
            " AND service_date >= '2026-12-01'",
        )
        == 0
    )


def test_pay_ahead_families_keep_account_credit(es_url):
    for key in BILLING.ahead_families:
        credit = scalar(
            es_url,
            "SELECT sum(p.amount - COALESCE((SELECT sum(pa.amount) FROM"
            " payment_allocations pa WHERE pa.payment_id = p.id AND NOT EXISTS"
            " (SELECT 1 FROM allocation_releases r WHERE r.allocation_id = pa.id)), 0)"
            " - COALESCE((SELECT sum(f.amount) FROM refunds f WHERE f.payment_id ="
            " p.id), 0)) FROM payments p JOIN billing_accounts a ON a.id ="
            " p.billing_account_id WHERE a.family_id = :f AND NOT EXISTS (SELECT 1"
            " FROM payment_reversals x WHERE x.payment_id = p.id)",
            f=family_id(es_url, key),
        )
        assert credit > 0


def test_reminders_are_sent_before_the_anchor_on_their_stage_days(es_url):
    """The reminder step runs at 15:00 each history day; a stage's reminder
    is sent inside its window (due_soon D−3..D, overdue D+1..D+13,
    overdue_followup from D+14), never after SIM_NOW. At SIM_NOW the
    module runs the step once more (B5), so a charge entering a window on
    19 Nov is reminded at SIM_NOW exactly."""
    found = rows(es_url, "SELECT stage, due_date, sent_at FROM charge_reminders")
    assert found
    for row in found:
        sent = row.sent_at.astimezone(ZONE)
        assert row.sent_at <= SIM_NOW
        assert row.sent_at == SIM_NOW or (sent.hour, sent.minute) == (15, 0)
        days = (sent.date() - row.due_date).days
        if row.stage == "due_soon":
            assert -3 <= days <= 0
        elif row.stage == "overdue":
            assert 1 <= days <= 13
        else:
            assert days >= 14


# --- B3: ledger identities through the existing seams ---------------------------------


def test_the_billing_invariant_checks_pass_on_the_seeded_data(es_url):
    """The #103 check catalogue (``billing_invariants.checks.Catalogue``, as
    ``make test-billing-invariants`` runs it): every state check, I11's
    first sight and the reconciliation of the suite's recomputation, the
    ``billing.service`` balances and Σ ``billing_ledger_entries``."""

    async def check() -> list[Any]:
        engine = short_lived_engine(es_url)
        try:
            async with engine.connect() as conn:
                from sqlalchemy.ext.asyncio import AsyncSession

                async with AsyncSession(bind=conn) as session:
                    return await Catalogue().run(session, today=SIM_ANCHOR)
        finally:
            await engine.dispose()

    violations = asyncio.run(check())
    assert not violations, "\n".join(v.describe() for v in violations)


def test_the_family_summary_matches_its_open_charges_less_credit(es_url):
    family = BILLING.overdue_families[0]
    fid = family_id(es_url, family)
    with auth_client(es_url) as client:
        assert (
            login(client, PLAN.finance.username, PLAN.finance.password).status_code
            == 200
        )
        response = client.get(f"/api/v1/families/{fid}/billing-account")
        assert response.status_code == 200, response.text
        summary = response.json()
    account = scalar(
        es_url, "SELECT id FROM billing_accounts WHERE family_id = :f", f=fid
    )
    open_total = sum(
        (
            Decimal(r.net) - Decimal(r.allocated)
            for r in rows(es_url, CHARGES)
            if r.account == account
        ),
        Decimal(0),
    )
    assert Decimal(summary["outstanding"]) == open_total
    assert Decimal(summary["net_balance"]) == open_total - Decimal(
        summary["account_credit"]
    )


# --- B4: receipts ---------------------------------------------------------------------


def test_one_receipt_per_payment_in_the_run_language_with_consecutive_numbers(es_url):
    assert scalar(es_url, "SELECT count(*) FROM receipts") == scalar(
        es_url, "SELECT count(*) FROM payments"
    )
    assert counts(es_url, "receipts", "language") == {
        "es": scalar(es_url, "SELECT count(*) FROM receipts")
    }
    messages = {
        row[0] for row in rows(es_url, "SELECT snapshot->>'message' FROM receipts")
    }
    assert messages == {RECEIPT_MESSAGES["es"]}
    numbers = [
        row[0] for row in rows(es_url, "SELECT number FROM receipts ORDER BY issued_at")
    ]
    assert [int(n.rsplit("-", 1)[1]) for n in sorted(numbers)] == list(
        range(1, len(numbers) + 1)
    )


def test_a_guardian_downloads_a_receipt_in_the_run_language(es_url):
    """B4's PDF text check. It renders with WeasyPrint, so it needs Pango:
    on the owner's Mac it is not run natively ("cannot load library" there
    is expected, BRIEF #383 D6); the evidence is CI ``api-simulation``."""
    family = next(
        f for f in PLAN.families if f.guardians and f.guardians[0].account is not None
    )
    guardian = family.guardians[0].account
    assert guardian is not None
    fid = family_id(es_url, family.key)
    receipt_id, number = rows(
        es_url,
        "SELECT r.id, r.number FROM receipts r JOIN billing_accounts a"
        " ON a.id = r.billing_account_id WHERE a.family_id = :f"
        " ORDER BY r.number LIMIT 1",
        f=fid,
    )[0]
    with auth_client(es_url) as client:
        assert login(client, guardian.username, guardian.password).status_code == 200
        response = client.get(receipt_path(fid, receipt_id))
    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert number in text
    assert "".join(RECEIPT_MESSAGES["es"].split()) in "".join(text.split())


# --- B6, B7: rerun, skip, language ----------------------------------------------------


def test_a_rerun_writes_nothing(es_url):
    before = table_counts(es_url)

    code, out, _ = run_command(es_url, "es")

    assert code == 0
    assert out[-5:-2] == ["billing: 0 rows", "payroll: 0 rows", "finance: 0 rows"]
    assert table_counts(es_url) == before


def test_a_database_seeded_by_hf01c_only_skips_the_billing(database_url):
    for url in _fresh(database_url):
        assert run_command(url, "es", modules=SIMULATION_MODULES[:7])[0] == 0
        before = table_counts(url)

        code, out, _ = run_command(url, "es")

        assert code == 0
        assert out[-6:-2] == [
            "academics: 0 rows",
            f"billing: skipped ({SEEDED_EARLIER})",
            f"payroll: skipped ({SEEDED_EARLIER})",
            f"finance: skipped ({SEEDED_EARLIER})",
        ]
        assert table_counts(url) == before


def test_billing_text_comes_from_the_spanish_pool(es_url):
    allowed = billing_texts("es")
    found = [value for _, value in billing_free_text(es_url)]
    assert found and set(found) <= allowed


# --- C1: per account, through the API -------------------------------------------------


def test_who_sees_which_account(es_url):
    family = next(
        f for f in PLAN.families if f.guardians and f.guardians[0].account is not None
    )
    guardian = family.guardians[0].account
    assert guardian is not None
    other = next(f for f in PLAN.families if f.key != family.key)
    own_id, other_id = family_id(es_url, family.key), family_id(es_url, other.key)
    for staff in (PLAN.finance, PLAN.owner):
        with auth_client(es_url) as client:
            assert login(client, staff.username, staff.password).status_code == 200
            for fid in (own_id, other_id):
                assert (
                    client.get(f"/api/v1/families/{fid}/billing-account").status_code
                    == 200
                )
            assert client.get("/api/v1/billing/overdue-accounts").status_code == 200
    with auth_client(es_url) as client:
        assert login(client, guardian.username, guardian.password).status_code == 200
        assert (
            client.get(f"/api/v1/families/{own_id}/billing-account").status_code == 200
        )
        charges = client.get(f"/api/v1/families/{own_id}/billing-account/charges")
        assert charges.status_code == 200 and charges.json()["items"]
        for path in ("", "/charges", "/payments"):
            assert (
                client.get(
                    f"/api/v1/families/{other_id}/billing-account{path}"
                ).status_code
                == 404
            )
    for account in (PLAN.coordinator, PLAN.tutors[0].account):
        with auth_client(es_url) as client:
            assert login(client, account.username, account.password).status_code == 200
            assert client.get("/api/v1/billing/overdue-accounts").status_code == 403


# --- B5: job parity at the anchor (last: it would change the shared database) ---------


def test_zz_the_billing_jobs_have_nothing_to_do_at_the_anchor(es_url):
    assert scalar(es_url, "SELECT count(*) FROM procrastinate_jobs") == 0
    before = table_counts(es_url)

    with simulation_env(es_url), sim_clock(SIM_NOW):
        settings = get_settings()
        sessions = asyncio.run(session_charges.run_post_session_charges(settings))
        fees = asyncio.run(enrollment_charges.run_post_enrollment_charges(settings))
        tuition = asyncio.run(tuition_charges.run_post_tuition_charges(settings))
        reminders = asyncio.run(charge_reminders.run_send_charge_reminders(settings))
        expired = asyncio.run(package_expiry.run_expire_packages(settings))
        reconciled = asyncio.run(
            schedule_reconciliation.run_reconcile_session_charges(settings)
        )

    assert (sessions.posted, fees.posted, tuition.posted) == (0, 0, 0)
    assert (reminders.sent_charges, reminders.notifications) == (0, 0)
    assert expired.expired == 0
    assert reconciled.cancellation_credited + reconciled.repriced == 0
    assert table_counts(es_url) == before
    assert scalar(es_url, "SELECT count(*) FROM procrastinate_jobs") == 0
