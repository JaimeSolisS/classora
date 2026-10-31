"""The M6-demo billing data, part 2 (M6-demo-b #420; B, C, D, E2-E6):
``billing.demo_reconciliation``, ``billing.demo_payments`` and
``billing.demo_reminders``, the whole run with them, worker parity, the
invariant catalogue, weekends, the flags, markers, the stale skip,
prerequisites and rollback. Reruns, ``dev``, ``--without-billing`` and the
hand-configured skip of all six billing generators are in
``test_demo_billing``; what each account sees is in
``test_demo_billing_money_views``."""

import asyncio
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from app.core import clock as app_clock
from app.core.audit import ACTOR_INFO_KEY
from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.billing.schemas import PaymentAllocationBody, PaymentCreate
from app.modules.scheduling import service as scheduling
from app.workflows import billing_payments
from app.workflows.charge_reminders import run_send_charge_reminders
from app.workflows.enrollment_charges import run_post_enrollment_charges
from app.workflows.package_expiry import run_expire_packages
from app.workflows.schedule_reconciliation import run_reconcile_session_charges
from app.workflows.session_charges import run_post_session_charges
from app.workflows.tuition_charges import run_post_tuition_charges
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.demo_billing import WITHOUT_BILLING
from tests.factories.demo_billing_money import (
    CX_REASON,
    DC_REASON,
    MC_DESCRIPTION,
    PY2_REFERENCE,
    PY3_REFERENCE,
    RV_REASON,
    DemoBillingMoneyPlan,
    DemoPaymentsGenerator,
    DemoReconciliationGenerator,
    DemoRemindersGenerator,
    plan_demo_billing_money,
)
from tests.factories.demo_changes import STALE_SCHEDULE
from tests.factories.demo_clock import seed_actor
from tests.factories.identity import DemoPrerequisiteMissingError
from tests.integration.billing_invariants.checks import Catalogue
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.demo_support import (
    ALL_TABLE_COUNTS,
    BILLING_420_GENERATORS,
    BILLING_GENERATORS,
    CHARGES_GENERATOR,
    CX_NOTIFICATIONS,
    DEMO_BILLING_MONEY_TABLE_COUNTS,
    DEMO_PAYMENTS_ROWS,
    DEMO_RECONCILIATION_ROWS,
    DEMO_REMINDERS_ROWS,
    FIRST_RUN_LINES,
    FROZEN_NOW,
    GENERATOR_NAMES,
    PAYMENTS_GENERATOR,
    RECONCILIATION_GENERATOR,
    REMINDERS_GENERATOR,
    RERUN_REFERENCE_LINES,
    UntouchedSession,
    count,
    demo_env,
    demo_table_counts,
    first_run_lines,
    generators_through,
    migrate,
)
from tests.integration.test_demo_billing import (
    _balances,
    _families,
    _labels,
    _violations,
)
from tests.integration.test_demo_schedule import ZONE

# T = Wednesday 2026-11-04: P(1) = 11-03, P(3) = 10-30, D(13) = 11-24 (the
# series weekday, Tuesday); DC → 11-06; MC due 11-03.
TODAY = date(2026, 11, 4)
CX_START = "2026-11-24 17:00"
D = Decimal

RECONCILIATION_EVENTS = Counter({"candidates": 1, "cancellation_credited": 1})
"""B2: the reconciliation step at ``FROZEN_NOW`` (only CX is a candidate)."""
REMINDER_COUNTS = Counter(
    {"sent": 1, "no_recipient": 2, "sent_charges": 10, "notifications": 2}
)
"""D3: the reminder step at ``FROZEN_NOW``: F1 sent (4 ``due_soon``, 6
``overdue``), F2 and F3 ``no_recipient``."""
PY1_ALLOCATIONS = [
    ("Sib", "package_purchase", "2026-10-16", D("990.00")),
    ("S", "session", "2026-10-23", D("342.00")),
    ("Sib", "session", "2026-10-26", D("168.00")),
]
"""C2: PY1's oldest-first lines (the last one partly pays the charge)."""
MONEY_TABLES = (
    "session_cancellation_decisions",
    "charge_adjustments",
    "payments",
    "payment_allocations",
    "receipts",
    "payment_reversals",
    "allocation_releases",
    "charge_due_date_changes",
    "charge_reminders",
)


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _fresh_money() -> list[Any]:
    """The registered generators with new part 2 instances (their step
    counts are read after the run)."""
    return [
        *generators_through(CHARGES_GENERATOR),
        DemoReconciliationGenerator(),
        DemoPaymentsGenerator(),
        DemoRemindersGenerator(),
    ]


def _cx(url: str) -> Any:
    [row] = run_sql(
        url,
        "SELECT cs.id, cs.state, cs.cancellation_reason, cs.series_id"
        " FROM class_sessions cs"
        " WHERE to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
        " = :start AND cs.series_id IS NOT NULL AND cs.kind = 'individual'",
        {"zone": ZONE, "start": CX_START},
    )
    return row


def _no_money_data(url: str) -> None:
    for table in MONEY_TABLES:
        assert count(url, table) == 0, table


def _line(name: str, rows: int | str) -> str:
    return f"{name}: {rows} rows" if isinstance(rows, int) else f"{name}: {rows}"


def test_a_first_run_writes_the_money_data(migrated_url, env_for, clock, capsys):
    # E2 on Wednesday 2026-11-04, 11:00 local.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = _fresh_money()

    assert demo.main([], generators=generators) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    assert app_clock.now() == FROZEN_NOW
    labels = _labels(migrated_url)
    families = {label: fid for fid, label in _families(migrated_url).items()}
    reconciliation, payments, reminders = generators[-3:]

    # B2: CX cancelled by demo.owner as the center, then credited.
    cx = _cx(migrated_url)
    assert (cx.state, cx.cancellation_reason) == ("cancelled", CX_REASON)
    assert reconciliation.events == RECONCILIATION_EVENTS
    [decision] = run_sql(
        migrated_url,
        "SELECT d.initiated_by, d.classification, d.makeup_allowed, d.outcome,"
        " d.result, d.student_id, a.kind, a.amount, c.amount AS charge_amount,"
        " c.id = d.charge_id AS same_charge"
        " FROM session_cancellation_decisions d"
        " JOIN charge_adjustments a ON a.id = d.charge_adjustment_id"
        " JOIN charges c ON c.id = a.charge_id WHERE d.class_session_id = :cx",
        {"cx": cx.id},
    )
    assert tuple(decision)[:5] == ("center", "excused", True, "credit", "credited")
    assert labels[decision.student_id] == "S"
    assert decision.same_charge
    # S's Matemáticas (Secundaria) 350.00 less the sibling 10 %: net 0.00.
    assert (decision.kind, decision.amount, decision.charge_amount) == (
        "cancellation_credit",
        D("-315.00"),
        D("315.00"),
    )
    cancelled = run_sql(
        migrated_url,
        "SELECT u.username FROM notifications n"
        " JOIN users u ON u.id = n.recipient_user_id"
        " WHERE n.notification_type = 'scheduling.session_cancelled'"
        " AND n.params->>'class_session_id' = :cx ORDER BY 1",
        {"cx": str(cx.id)},
    )
    assert [r.username for r in cancelled] == [
        "demo.guardian",
        "demo.student",
        "demo.tutor",
    ]
    assert len(cancelled) == CX_NOTIFICATIONS

    # C2: F2's offset; PY1 oldest-first, PY2 credit; one receipt each.
    by_family = _families(migrated_url)
    offsets = run_sql(
        migrated_url, "SELECT family_id, due_offset_days FROM billing_accounts"
    )
    assert {by_family[r.family_id]: r.due_offset_days for r in offsets} == {
        "F1": 0,
        "F2": 5,
        "F3": 0,
    }
    assert payments.recorded is not None
    recorded = payments.recorded
    paid = run_sql(
        migrated_url,
        "SELECT p.id, p.amount, p.received_on::text, p.payment_method_code,"
        " p.reference, EXISTS (SELECT 1 FROM payment_reversals v"
        "  WHERE v.payment_id = p.id) AS reversed, r.number"
        " FROM payments p JOIN receipts r ON r.payment_id = p.id"
        " ORDER BY r.number",
    )
    assert [tuple(p)[1:] for p in paid] == [
        (D("1500.00"), "2026-10-30", "cash", None, False, "R-2026-000001"),
        (D("500.00"), "2026-11-03", "transfer", PY2_REFERENCE, False, "R-2026-000002"),
        (D("250.00"), "2026-11-04", "transfer", PY3_REFERENCE, True, "R-2026-000003"),
    ]
    assert [p.id for p in paid] == [recorded.py1, recorded.py2, recorded.py3]
    allocations = run_sql(
        migrated_url,
        "SELECT a.payment_id, c.student_id, c.kind, c.service_date::text AS day,"
        " a.amount, EXISTS (SELECT 1 FROM allocation_releases r"
        "  WHERE r.allocation_id = a.id AND r.payment_reversal_id IS NOT NULL)"
        "  AS released"
        " FROM payment_allocations a JOIN charges c ON c.id = a.charge_id"
        " ORDER BY c.due_date, c.service_date, c.posted_at",
    )
    assert [
        (labels[a.student_id], a.kind, a.day, a.amount)
        for a in allocations
        if a.payment_id == recorded.py1
    ] == PY1_ALLOCATIONS
    assert not [a for a in allocations if a.payment_id == recorded.py2]
    [py3_line] = [a for a in allocations if a.payment_id == recorded.py3]
    assert (labels[py3_line.student_id], py3_line.kind, py3_line.amount) == (
        "A",
        "manual",
        D("250.00"),
    )
    assert py3_line.released
    assert not [a for a in allocations if a.released and a.payment_id != recorded.py3]
    [mc] = run_sql(
        migrated_url,
        "SELECT c.id, c.student_id, c.description, c.amount, c.service_date::text"
        " AS service, c.due_date::text AS due, u.username FROM charges c"
        " JOIN users u ON u.id = c.posted_by_user_id WHERE c.kind = 'manual'",
    )
    assert mc.id == recorded.mc
    assert (labels[mc.student_id], mc.description, mc.amount, mc.service, mc.due) == (
        "A",
        MC_DESCRIPTION,
        D("250.00"),
        "2026-10-30",
        "2026-11-03",  # no offset (#97 E11), already past due
    )
    assert mc.username == "demo.finance"
    [reversal] = run_sql(
        migrated_url,
        "SELECT v.reason, u.username FROM payment_reversals v"
        " JOIN users u ON u.id = v.reversed_by_user_id WHERE v.payment_id = :p",
        {"p": recorded.py3},
    )
    assert tuple(reversal) == (RV_REASON, "demo.owner")

    # D2: DC, F1's oldest open charge after PY1 (the partly paid one).
    [change] = run_sql(
        migrated_url,
        "SELECT d.charge_id, c.student_id, c.kind, c.service_date::text AS day,"
        " d.previous_due_date::text AS previous, d.due_date::text AS due,"
        " c.due_date::text AS original, d.reason, u.username"
        " FROM charge_due_date_changes d JOIN charges c ON c.id = d.charge_id"
        " JOIN users u ON u.id = d.changed_by_user_id",
    )
    assert change.charge_id == reminders.dc
    assert (labels[change.student_id], change.kind, change.day) == PY1_ALLOCATIONS[-1][
        :3
    ]
    assert (change.previous, change.due, change.original) == (
        "2026-10-26",
        "2026-11-06",
        "2026-10-26",
    )
    assert (change.reason, change.username) == (DC_REASON, "demo.finance")

    # D3: F1's reminders; demo.guardian's notifications; nobody else.
    assert reminders.counts == REMINDER_COUNTS
    stages = run_sql(
        migrated_url,
        "SELECT a.family_id, r.stage, count(*) AS n FROM charge_reminders r"
        " JOIN charges c ON c.id = r.charge_id"
        " JOIN billing_accounts a ON a.id = c.billing_account_id"
        " GROUP BY 1, 2 ORDER BY 2",
    )
    assert [(_families(migrated_url)[r.family_id], r.stage, r.n) for r in stages] == [
        ("F1", "due_soon", 4),
        ("F1", "overdue", 6),
    ]
    assert run_sql(
        migrated_url,
        "SELECT 1 FROM charge_reminders r WHERE r.stage = 'due_soon'"
        " AND r.charge_id = :dc",
        {"dc": change.charge_id},
    )
    sent = run_sql(
        migrated_url,
        "SELECT u.username, n.params FROM notifications n"
        " JOIN users u ON u.id = n.recipient_user_id"
        " WHERE n.notification_type = 'billing.payment_reminder'"
        " ORDER BY n.params->>'kind'",
    )
    f1 = str(families["F1"])
    assert [(r.username, r.params) for r in sent] == [
        (
            "demo.guardian",
            {"family_id": f1, "kind": "due_soon", "count": 4,
             "open_total": "741.00", "due_on": "2026-11-06"},
        ),
        (
            "demo.guardian",
            {"family_id": f1, "kind": "overdue", "count": 6,
             "open_total": "4077.00", "due_on": "2026-10-27"},
        ),
    ]  # fmt: skip

    # The audit actors: demo.owner for CX and RV, demo.finance for the rest;
    # the job steps none.
    actors = run_sql(
        migrated_url,
        "SELECT e.record_type, e.action, u.username FROM audit_events e"
        " LEFT JOIN users u ON u.id = e.actor_user_id"
        " WHERE e.record_type IN ('payments', 'payment_allocations', 'receipts',"
        " 'payment_reversals', 'allocation_releases', 'charge_due_date_changes',"
        " 'session_cancellation_decisions', 'charge_adjustments')"
        " OR (e.record_type = 'class_sessions' AND e.record_id = :cx)"
        " OR (e.record_type = 'charges' AND e.record_id = :mc)"
        " OR (e.record_type = 'billing_accounts' AND e.action = 'update')"
        " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        {"cx": cx.id, "mc": recorded.mc},
    )
    by_record: dict[tuple[str, str], set[str | None]] = {}
    for r in actors:
        by_record.setdefault((r.record_type, r.action), set()).add(r.username)
    assert by_record[("payment_reversals", "create")] == {"demo.owner"}
    assert by_record[("class_sessions", "scheduling.session_cancelled")] == {
        "demo.owner"
    }
    assert by_record[("session_cancellation_decisions", "create")] == {None}
    assert by_record[("charge_adjustments", "create")] == {None}
    for record in (
        "payments",
        "payment_allocations",
        "receipts",
        "charge_due_date_changes",
        "charges",
    ):
        assert by_record[(record, "create")] == {"demo.finance"}, record
    assert by_record[("billing_accounts", "update")] == {"demo.finance"}

    # D5 and the #413 QA AC: Σ open = balance; F1 credit 500; F2 overdue.
    balances = asyncio.run(_balances(migrated_url, _families(migrated_url)))
    for family, (outstanding, _, open_total) in balances.items():
        assert outstanding == open_total, family
    assert balances["F1"][:2] == (D("11271.00"), D("4077.00"))
    assert balances["F2"][:2] == (D("2639.50"), D("250.00"))
    assert balances["F3"][:2] == (D("1600.00"), D("1600.00"))
    credits = asyncio.run(_credits(migrated_url, families))
    assert credits == {"F1": D("500.00"), "F2": D("0.00"), "F3": D("0.00")}
    assert asyncio.run(_violations(migrated_url)) == []
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS


async def _credits(url: str, families: dict[str, Any]) -> dict[str, Decimal]:
    engine = short_lived_engine(url)
    out: dict[str, Decimal] = {}
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            for label, family_id in families.items():
                account = await billing.account_of_family(uow.session, family_id)
                assert account is not None, label
                out[label] = (
                    await billing.account_credit(uow.session, account.id)
                ).amount
    finally:
        await engine.dispose()
    return out


def test_the_jobs_do_nothing_after_the_seed(migrated_url, env_for, clock):
    # D4: worker parity on the same frozen day.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    settings = settings_for(migrated_url)
    before = demo_table_counts(migrated_url, ALL_TABLE_COUNTS)

    reconciled = asyncio.run(run_reconcile_session_charges(settings))
    reminded = asyncio.run(run_send_charge_reminders(settings))
    sessions = asyncio.run(run_post_session_charges(settings))
    enrollments = asyncio.run(run_post_enrollment_charges(settings))
    tuition = asyncio.run(run_post_tuition_charges(settings))
    expiry = asyncio.run(run_expire_packages(settings))

    assert (reconciled.candidates, reconciled.failed) == (0, 0)
    for name, value in vars(reconciled).items():
        if name.startswith(("cancellation_", "closure_", "repriced")):
            assert value == 0, name
    assert (reminded.sent_charges, reminded.notifications, reminded.failed) == (0, 0, 0)
    assert reminded.no_recipient == 2  # F2 and F3: no guardian login
    assert (sessions.posted, sessions.failed) == (0, 0)
    assert (enrollments.posted, enrollments.failed) == (0, 0)
    assert (tuition.posted, tuition.failed) == (0, 0)
    assert (expiry.expired, expiry.fallback_consumed, expiry.failed) == (0, 0, 0)
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == before


@pytest.mark.parametrize(
    ("now", "reminder_rows"),
    [
        (datetime(2026, 11, 8, 5, 30, tzinfo=UTC), DEMO_REMINDERS_ROWS),
        (datetime(2026, 11, 8, 18, 0, tzinfo=UTC), DEMO_REMINDERS_ROWS),
    ],
    ids=["saturday-night", "sunday"],
)
def test_a_weekend_run(migrated_url, env_for, clock, capsys, now, reminder_rows):
    # E3: Saturday 23:30 and Sunday 12:00 local.
    clock.set(now)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[:-3] == FIRST_RUN_LINES[:-3]
    assert lines[-3:] == [
        _line(RECONCILIATION_GENERATOR, DEMO_RECONCILIATION_ROWS),
        _line(PAYMENTS_GENERATOR, DEMO_PAYMENTS_ROWS),
        _line(REMINDERS_GENERATOR, reminder_rows),
    ]
    assert asyncio.run(_violations_on(migrated_url)) == []
    assert app_clock.now() == now


async def _violations_on(url: str) -> list[Any]:
    """D5 on the run's own center today."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            today = await administration.center_today(uow.session)
            return await Catalogue().run(uow.session, today=today)
    finally:
        await engine.dispose()


NEW_GENERATORS = [
    DemoReconciliationGenerator,
    DemoPaymentsGenerator,
    DemoRemindersGenerator,
]


@pytest.mark.parametrize("app_env", ["dev", "prod", "staging"])
@pytest.mark.parametrize("generator", NEW_GENERATORS, ids=lambda g: g.name)
def test_a_new_generator_writes_nothing_outside_local(generator, app_env):
    # E5: skipped without touching the session.
    result = asyncio.run(
        generator(app_env=lambda: app_env).generate(UntouchedSession())  # type: ignore[arg-type]
    )

    assert result == GeneratorSkipped(f"APP_ENV={app_env}; local only")


@pytest.mark.parametrize("generator", NEW_GENERATORS, ids=lambda g: g.name)
def test_a_new_generator_writes_nothing_under_the_flag(generator):
    # E5, A5: --without-billing, without touching the session.
    result = asyncio.run(
        generator(app_env=lambda: "local", skipped=lambda: True).generate(
            UntouchedSession()  # type: ignore[arg-type]
        )
    )

    assert result == GeneratorSkipped(WITHOUT_BILLING)


def test_the_money_generators_come_last():
    # A1.
    assert len(GENERATOR_NAMES) == 28
    assert GENERATOR_NAMES[-3:] == list(BILLING_420_GENERATORS)
    assert GENERATOR_NAMES[-4] == CHARGES_GENERATOR
    assert BILLING_GENERATORS[-3:] == BILLING_420_GENERATORS
    assert (
        sum(DEMO_BILLING_MONEY_TABLE_COUNTS.values()) - CX_NOTIFICATIONS
        == DEMO_RECONCILIATION_ROWS + DEMO_PAYMENTS_ROWS + DEMO_REMINDERS_ROWS
    )


# --- E6: stale data, markers, prerequisites, rollback ---------------------------------


def test_part_1_from_an_earlier_day_is_skipped(migrated_url, env_for, clock, capsys):
    # Part 1 on 2026-11-04, then every generator 10 days later.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(CHARGES_GENERATOR)) == 0
    capsys.readouterr()

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *RERUN_REFERENCE_LINES,
        *(
            f"{name}: skipped ({STALE_SCHEDULE})"
            if name in BILLING_420_GENERATORS
            else f"{name}: 0 rows"
            for name in GENERATOR_NAMES
        ),
    ]
    _no_money_data(migrated_url)
    assert _cx(migrated_url).state == "confirmed"


def _rest(capsys: Any) -> list[str]:
    return capsys.readouterr().out.splitlines()[-3:]


async def _cancel_cx_by_hand(url: str) -> None:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            cx = _cx_in(
                await uow.session.execute(_CX_QUERY, {"zone": ZONE, "start": CX_START})
            )
            await scheduling.cancel_class_session(
                uow.session,
                cx.id,
                expected_version=cx.version,
                reason="Cancelada por la dirección",
                by_user_id=None,
            )
    finally:
        await engine.dispose()


def _cx_in(result: Any) -> Any:
    [row] = result.all()
    return row


_CX_QUERY = text(
    "SELECT cs.id, cs.version FROM class_sessions cs"
    " WHERE to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
    " = :start AND cs.series_id IS NOT NULL AND cs.kind = 'individual'"
)


def test_cx_cancelled_by_hand_is_the_marker(migrated_url, env_for, clock, capsys):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(CHARGES_GENERATOR)) == 0
    capsys.readouterr()
    asyncio.run(_cancel_cx_by_hand(migrated_url))

    assert demo.main([]) == 0

    reconciliation, payments, reminders = _rest(capsys)
    assert reconciliation == _line(RECONCILIATION_GENERATOR, 0)
    assert payments == _line(PAYMENTS_GENERATOR, DEMO_PAYMENTS_ROWS)
    assert reminders.startswith(f"{REMINDERS_GENERATOR}: ")
    assert reminders.endswith(" rows") and reminders != _line(REMINDERS_GENERATOR, 0)
    assert count(migrated_url, "session_cancellation_decisions") == 0


async def _a_payment_by_hand(url: str) -> None:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            [row] = (
                await uow.session.execute(
                    text(
                        "SELECT fs.family_id, u.id AS owner FROM family_students fs"
                        " JOIN students s ON s.id = fs.student_id"
                        " JOIN users su ON su.id = s.user_id"
                        " CROSS JOIN users u WHERE su.username = 'demo.student'"
                        " AND u.username = 'demo.owner'"
                    )
                )
            ).all()
            with seed_actor(uow.session, row.owner):
                await billing_payments.record_payment(
                    uow.session,
                    row.family_id,
                    PaymentCreate(
                        amount=Money("100.00"),
                        received_on=TODAY,
                        payment_method_code="cash",
                        allocation=PaymentAllocationBody(mode="none"),
                    ),
                )
    finally:
        await engine.dispose()


def test_a_payment_by_hand_is_the_marker(migrated_url, env_for, clock, capsys):
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(CHARGES_GENERATOR)) == 0
    capsys.readouterr()
    asyncio.run(_a_payment_by_hand(migrated_url))

    assert demo.main([]) == 0

    reconciliation, payments, reminders = _rest(capsys)
    assert reconciliation == _line(RECONCILIATION_GENERATOR, DEMO_RECONCILIATION_ROWS)
    assert payments == _line(PAYMENTS_GENERATOR, 0)
    assert reminders != _line(REMINDERS_GENERATOR, 0)
    assert count(migrated_url, "payments") == 1
    assert count(migrated_url, "charge_due_date_changes") == 1


def test_a_missing_cx_charge_fails_the_run(migrated_url, env_for, clock):
    # A plan double pointing at U3 (offered, never charged): nothing at all
    # is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    def uncharged(today: date) -> DemoBillingMoneyPlan:
        plan = plan_demo_billing_money(today)
        return DemoBillingMoneyPlan(
            **{**vars(plan), "cx_day": date(2026, 11, 9), "cx_start": "18:00"}
        )

    generators = [
        *generators_through(CHARGES_GENERATOR),
        DemoReconciliationGenerator(plan=uncharged),
        DemoPaymentsGenerator(),
        DemoRemindersGenerator(),
    ]
    with pytest.raises(DemoPrerequisiteMissingError, match="session charge"):
        demo.main([], generators=generators)

    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "charges") == 0


def _future_py1(today: date) -> DemoBillingMoneyPlan:
    plan = plan_demo_billing_money(today)
    return DemoBillingMoneyPlan(
        **{**vars(plan), "py1_received_on": today + timedelta(days=1)}
    )


def test_a_refused_payment_rolls_the_run_back(migrated_url, env_for, clock):
    # E6: received_on_in_future rolls everything back; the clock and the
    # session actor are restored.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = [
        *generators_through(CHARGES_GENERATOR),
        DemoReconciliationGenerator(),
        DemoPaymentsGenerator(plan=_future_py1),
        DemoRemindersGenerator(),
    ]

    with pytest.raises(billing.ReceivedOnInFutureError):
        demo.main([], generators=generators)

    assert app_clock.now() == FROZEN_NOW
    assert count(migrated_url, "users") == 0
    _no_money_data(migrated_url)
    assert count(migrated_url, "charges") == 0

    # The actor: the generator on a session of a seeded database.
    assert demo.main([], generators=generators_through(RECONCILIATION_GENERATOR)) == 0
    asyncio.run(_refused_in_a_session(migrated_url))
    assert count(migrated_url, "payments") == 0


async def _refused_in_a_session(url: str) -> None:
    engine = short_lived_engine(url)
    try:
        with pytest.raises(billing.ReceivedOnInFutureError):
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                saved = uow.session.info[ACTOR_INFO_KEY]
                try:
                    await DemoPaymentsGenerator(plan=_future_py1).generate(uow.session)
                finally:
                    assert uow.session.info[ACTOR_INFO_KEY] is saved
    finally:
        await engine.dispose()


def test_without_billing_keeps_cx_confirmed(migrated_url, env_for, clock, capsys):
    # E5, A5: the E2E seed; the six billing generators skip.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main(["--without-billing"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[-6:] == [
        f"{name}: skipped ({WITHOUT_BILLING})" for name in BILLING_GENERATORS
    ]
    assert _cx(migrated_url).state == "confirmed"
    _no_money_data(migrated_url)
    assert (
        first_run_lines(generators_through("academics.demo_progress_reports"))
        == lines[:-6]
    )
