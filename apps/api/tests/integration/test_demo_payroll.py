"""The M7-demo payroll data, part 1 (#499; B, C, D, E2-E7): ``payroll.demo_setup``,
``payroll.demo_periods`` and ``payroll.demo_corrections``, the whole run with
them, worker parity and reruns, weekends, ``dev`` and the flag, the stale and
hand-configured skips, prerequisites and rollback. What each account sees is
in ``test_demo_payroll_views``; the payslip PDFs in
``test_demo_payroll_payslip_pdf``."""

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
from app.modules.academics.schemas import CloseoutCorrection
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import PayItemTypeCreate
from app.workflows import payroll_calculation, session_closeout_review
from app.workflows.payroll_adjustments import run_post_adjustments
from app.workflows.payroll_periods import run_open_periods
from app.workflows.session_charges import run_post_session_charges
from tests.factories import demo
from tests.factories.demo_academics import DEMO_COORDINATOR_USERNAME
from tests.factories.demo_billing import WITHOUT_BILLING
from tests.factories.demo_changes import STALE_SCHEDULE
from tests.factories.demo_payroll import (
    BONUS_ITEM,
    BONUS_TYPE,
    CORRECTION_REASON,
    DEDUCTION_ITEM,
    DEDUCTION_TYPE,
    MANUAL_REASON,
    PAYROLL_CONFIGURED,
    PP1_REFERENCE,
    PP2_REFERENCE,
    PP3_REFERENCE,
    REVERSAL_REASON,
    DemoPayrollCorrectionsGenerator,
    DemoPayrollPeriodsGenerator,
    DemoPayrollSetupGenerator,
)
from tests.factories.identity import DemoPrerequisiteMissingError, demo_principal
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.demo_support import (
    ALL_TABLE_COUNTS,
    DEMO_PAYROLL_CORRECTIONS_ROWS,
    DEMO_PAYROLL_PERIODS_ROWS,
    DEMO_PAYROLL_SETUP_ROWS,
    DEMO_PAYROLL_TABLE_COUNTS,
    DEV_LINES,
    FIRST_RUN_LINES,
    FLAGGED_GENERATORS,
    FROZEN_NOW,
    GENERATOR_NAMES,
    PAYROLL_GENERATORS,
    REMINDERS_GENERATOR,
    RERUN_LINES,
    RERUN_REFERENCE_LINES,
    TABLE_COUNTS_THROUGH_354,
    TABLE_COUNTS_THROUGH_420,
    count,
    demo_env,
    demo_table_counts,
    first_run_lines,
    generators_through,
    migrate,
)
from tests.integration.test_demo_schedule import ZONE

D = Decimal
# T = Wednesday 2026-11-04: F = 10-12; W1 10-12, W2 10-19, W3 10-26; the
# current week 11-02. L1 = 10-19, N1 = 10-21; H3 10-23, H2 10-26, H1 10-27.
F = date(2026, 10, 12)
WEEKS = [date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)]
CURRENT = date(2026, 11, 2)
L1_START = "2026-10-19 16:00"
N1_START = "2026-10-21 16:00"
H2_START = "2026-10-26 16:00"

TOTALS = {
    "demo.tutor": [D("70.00"), D("630.00"), D("970.00"), None],
    "demo.tutor2": [D("2800.00"), D("3600.00"), D("3600.00"), None],
}
"""C5 at ``FROZEN_NOW``, per week (the current one open). ``demo.tutor``'s W3
is 970.00, not the groomed 1,210.00: #299's P4 (10-29) was never closed out
(its attendance is locked, M6-demo), so it is no work to pay (engineer D1)."""
LINES = {
    ("demo.tutor", 0): [
        ("pay_item", None, None, D("-80.00")),
        ("pay_item", None, None, D("150.00")),
    ],
    ("demo.tutor", 1): [
        ("no_show", "2026-10-21", 60, D("240.00")),
        ("pay_item", None, None, D("150.00")),
        ("session", "2026-10-23", 60, D("240.00")),
    ],
    ("demo.tutor", 2): [
        ("adjustment", None, None, D("120.00")),
        ("adjustment", None, None, D("240.00")),
        ("pay_item", None, None, D("150.00")),
        ("session", "2026-10-26", 55, D("220.00")),
        ("session", "2026-10-27", 60, D("240.00")),
    ],
    # The current week: only the hours correction until the next
    # recalculation (engineer D2).
    ("demo.tutor", 3): [("adjustment", None, None, D("20.00"))],
    ("demo.tutor2", 0): [
        ("salary", None, None, D("2000.00")),
        ("session", "2026-10-16", 90, D("800.00")),
    ],
    ("demo.tutor2", 1): [
        ("salary", None, None, D("2000.00")),
        ("session", "2026-10-20", 90, D("800.00")),
        ("session", "2026-10-23", 90, D("800.00")),
    ],
    ("demo.tutor2", 2): [
        ("salary", None, None, D("2000.00")),
        ("session", "2026-10-27", 90, D("800.00")),
        ("session", "2026-10-30", 90, D("800.00")),
    ],
    ("demo.tutor2", 3): [],
}
"""Every line by tutor and week (kind, service date, minutes, amount)."""
PERIOD_STEPS = Counter({"posted": 2})
"""E8: L1's and N1's charges."""
ADJUSTMENT_STEPS = Counter({"candidates": 1, "posted": 1})
"""G5.6 and G6.1: one candidate each time (L1's approval, H2's correction)."""
WEEKEND_PERIODS_ROWS = 63
"""``payroll.demo_periods`` with two ended weeks (``T`` Saturday or Sunday; F
is then 10-19): one week of both tutors fewer than
:data:`DEMO_PAYROLL_PERIODS_ROWS` (2 periods, 2 approval requests, 2
notifications, 2 payslips and that week's lines), and the reminder step's
rows for L1's and N1's charges on that day."""
PAYROLL_TABLES = (
    "tutor_pay_terms",
    "compensation_rates",
    "pay_item_types",
    "pay_items",
    "payroll_periods",
    "payroll_lines",
    "payroll_adjustments",
    "payslips",
    "payroll_payments",
    "payroll_payment_reversals",
)


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _fresh_payroll() -> list[Any]:
    """The registered generators with new payroll instances (their step
    counts are read after the run)."""
    return [
        *generators_through(REMINDERS_GENERATOR),
        DemoPayrollSetupGenerator(),
        DemoPayrollPeriodsGenerator(),
        DemoPayrollCorrectionsGenerator(),
    ]


def _line(name: str, rows: int | str) -> str:
    return f"{name}: {rows} rows" if isinstance(rows, int) else f"{name}: {rows}"


def _floor(url: str) -> Any:
    [row] = run_sql(
        url,
        "SELECT payroll_starts_on, biweekly_anchor_on, salary_proration_basis"
        " FROM payroll_settings",
    )
    return row


def _no_payroll_data(url: str) -> None:
    assert _floor(url).payroll_starts_on is None
    for table in PAYROLL_TABLES:
        assert count(url, table) == 0, table


def _class(url: str, start: str) -> Any:
    [row] = run_sql(
        url,
        "SELECT cs.id, cs.state, cs.kind, u.username AS tutor, cl.name AS room"
        " FROM class_sessions cs JOIN users u ON u.id = cs.tutor_user_id"
        " JOIN classrooms cl ON cl.id = cs.classroom_id"
        " WHERE to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
        " = :start AND u.username = 'demo.tutor'",
        {"zone": ZONE, "start": start},
    )
    return row


def _periods(url: str) -> dict[str, list[Any]]:
    rows = run_sql(
        url,
        "SELECT p.id, u.username, p.starts_on, p.ends_on, p.pay_frequency,"
        " p.status, p.total_amount, p.prepared_at, p.finalized_at,"
        " pb.username AS prepared_by, fb.username AS finalized_by"
        " FROM payroll_periods p JOIN users u ON u.id = p.tutor_user_id"
        " LEFT JOIN users pb ON pb.id = p.prepared_by_user_id"
        " LEFT JOIN users fb ON fb.id = p.finalized_by_user_id"
        " ORDER BY u.username, p.starts_on",
    )
    out: dict[str, list[Any]] = {}
    for r in rows:
        out.setdefault(r.username, []).append(r)
    return out


def _lines(url: str, period_id: Any) -> list[tuple[Any, ...]]:
    rows = run_sql(
        url,
        "SELECT kind, service_date::text AS day, minutes, amount FROM payroll_lines"
        " WHERE payroll_period_id = :p ORDER BY kind, service_date NULLS FIRST,"
        " amount",
        {"p": period_id},
    )
    return [tuple(r) for r in rows]


async def _unresolved(url: str) -> list[Any]:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            items = []
            for username in ("demo.tutor", "demo.tutor2"):
                principal = await demo_principal(uow.session, username, "test")
                items += await payroll_calculation.unresolved_work(
                    uow.session, principal.user_id, None
                )
            return items
    finally:
        await engine.dispose()


def test_a_first_run_writes_the_payroll_data(migrated_url, env_for, clock, capsys):
    # E2 on Wednesday 2026-11-04, 11:00 local.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    defaults = _floor(migrated_url)
    generators = _fresh_payroll()

    assert demo.main([], generators=generators) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    assert app_clock.now() == FROZEN_NOW
    _, periods_generator, corrections = generators[-3:]

    # B2: payroll on from F, one audited update by demo.finance; the anchor
    # and the basis unchanged.
    settings = _floor(migrated_url)
    assert settings.payroll_starts_on == F
    assert (settings.biweekly_anchor_on, settings.salary_proration_basis) == (
        defaults.biweekly_anchor_on,
        defaults.salary_proration_basis,
    )
    events = run_sql(
        migrated_url,
        "SELECT e.action, u.username FROM audit_events e"
        " LEFT JOIN users u ON u.id = e.actor_user_id"
        " WHERE e.record_type = 'payroll_settings'",
    )
    assert [tuple(e) for e in events] == [("update", "demo.finance")]

    # B3: terms, rates and items, all from F.
    terms = run_sql(
        migrated_url,
        "SELECT u.username, t.effective_from, t.pay_frequency, t.salary_amount"
        " FROM tutor_pay_terms t JOIN users u ON u.id = t.tutor_user_id ORDER BY 1",
    )
    assert [tuple(t) for t in terms] == [
        ("demo.tutor", F, "weekly", None),
        ("demo.tutor2", F, "weekly", D("2000.00")),
    ]
    rates = run_sql(
        migrated_url,
        "SELECT u.username, r.class_kind, r.effective_from, r.rate_model, r.rate"
        " FROM compensation_rates r JOIN users u ON u.id = r.tutor_user_id"
        " ORDER BY 1, 2",
    )
    assert [tuple(r) for r in rates] == [
        ("demo.tutor", "group", F, "per_session", D("300.0000")),
        ("demo.tutor", "individual", F, "per_hour", D("240.0000")),
        ("demo.tutor2", "course", F, "per_course", D("4800.0000")),
    ]
    items = run_sql(
        migrated_url,
        "SELECT u.username, t.name, t.direction, t.is_active, i.description,"
        " i.amount, i.recurrence, i.effective_from, i.effective_to"
        " FROM pay_items i JOIN pay_item_types t ON t.id = i.pay_item_type_id"
        " JOIN users u ON u.id = i.tutor_user_id ORDER BY t.name",
    )
    assert [tuple(i) for i in items] == [
        ("demo.tutor", BONUS_TYPE, "bonus", True, BONUS_ITEM, D("150.00"),
         "recurring", F, None),
        ("demo.tutor", DEDUCTION_TYPE, "deduction", True, DEDUCTION_ITEM,
         D("80.00"), "one_time", F, None),
    ]  # fmt: skip

    # C1: L1 and N1 completed in Aula 1; N1 a no-show approved at its seed
    # instant, L1 approved on the real clock after W2 was finalized.
    l1, n1 = _class(migrated_url, L1_START), _class(migrated_url, N1_START)
    for row in (l1, n1):
        assert (row.state, row.kind, row.room) == ("completed", "individual", "Aula 1")
    approvals = {
        r.class_session_id: r
        for r in run_sql(
            migrated_url,
            "SELECT a.id, a.class_session_id, a.approved_minutes, a.attendance,"
            " a.source, a.approved_at, u.username FROM session_closeout_approvals a"
            " JOIN users u ON u.id = a.approved_by_user_id"
            " WHERE a.class_session_id IN (:l1, :n1)",
            {"l1": l1.id, "n1": n1.id},
        )
    }
    assert {a.username for a in approvals.values()} == {DEMO_COORDINATOR_USERNAME}
    assert list(approvals[n1.id].attendance.values()) == ["absent"]
    assert list(approvals[l1.id].attendance.values()) == ["present"]
    assert approvals[l1.id].approved_minutes == approvals[n1.id].approved_minutes == 60
    assert approvals[n1.id].approved_at == datetime(2026, 10, 22, 1, 0, tzinfo=UTC)
    periods = _periods(migrated_url)
    assert approvals[l1.id].approved_at >= periods["demo.tutor"][1].finalized_at
    assert approvals[l1.id].approved_at == FROZEN_NOW  # the real (frozen) clock
    assert periods_generator.steps.charges == PERIOD_STEPS
    reminded = periods_generator.steps.reminders
    assert (reminded["sent_charges"], reminded["notifications"]) == (2, 1)
    charges = run_sql(
        migrated_url,
        "SELECT class_session_id, kind, amount FROM charges"
        " WHERE class_session_id IN (:l1, :n1)",
        {"l1": l1.id, "n1": n1.id},
    )
    assert sorted((c.kind, c.amount) for c in charges) == [
        ("session", D("315.00")),  # PR1 350.00 less the sibling 10 %
        ("session", D("315.00")),
    ]

    # C2: n + 1 weekly periods each; n finalized oldest first by demo.finance
    # and demo.owner, with approved requests, payslips and notifications.
    assert periods_generator.steps.opened == 8
    for username, rows in periods.items():
        assert [r.starts_on for r in rows] == [*WEEKS, CURRENT], username
        assert [r.ends_on for r in rows] == [
            d + timedelta(days=6) for d in [*WEEKS, CURRENT]
        ]
        assert {r.pay_frequency for r in rows} == {"weekly"}
        assert [r.status for r in rows] == ["finalized"] * 3 + ["open"]
        assert [r.total_amount for r in rows] == TOTALS[username]  # C5
        finalized = rows[:3]
        assert {(r.prepared_by, r.finalized_by) for r in finalized} == {
            ("demo.finance", "demo.owner")
        }
        assert [r.finalized_at for r in finalized] == sorted(
            r.finalized_at for r in finalized
        )
        assert (rows[3].prepared_at, rows[3].finalized_at) == (None, None)
        for k, row in enumerate(rows):
            assert _lines(migrated_url, row.id) == LINES[(username, k)], (username, k)
    requests = run_sql(
        migrated_url,
        "SELECT r.subject_id, r.status FROM approval_requests r"
        " WHERE r.request_type = 'payroll.period'",
    )
    finalized_ids = {r.id for rows in periods.values() for r in rows[:3]}
    assert {r.subject_id for r in requests} == finalized_ids
    assert {r.status for r in requests} == {"approved"}
    payslips = run_sql(
        migrated_url,
        "SELECT s.number, s.language, s.payroll_period_id, u.username, p.starts_on,"
        " s.snapshot FROM payslips s JOIN payroll_periods p"
        " ON p.id = s.payroll_period_id JOIN users u ON u.id = s.tutor_user_id"
        " ORDER BY s.number",
    )
    assert [(s.number, s.language, s.username, s.starts_on) for s in payslips] == [
        (f"P-2026-00000{k + 1}", "es", tutor, WEEKS[k // 2])
        for k, tutor in enumerate(["demo.tutor", "demo.tutor2"] * 3)
    ]
    notified = run_sql(
        migrated_url,
        "SELECT u.username, n.params->>'payroll_period_id' AS period"
        " FROM notifications n JOIN users u ON u.id = n.recipient_user_id"
        " WHERE n.notification_type = 'payroll.period_finalized'",
    )
    assert Counter(r.username for r in notified) == {"demo.tutor": 3, "demo.tutor2": 3}
    assert {r.period for r in notified} == {str(i) for i in finalized_ids}

    # C3: W3's late approval (the job step's) and manual (demo.finance's);
    # the payslip shows them.
    w3 = periods["demo.tutor"][2]
    adjustments = run_sql(
        migrated_url,
        "SELECT a.kind, a.amount, a.reason, a.source_key, a.class_kind,"
        " a.class_session_id, a.target_period_id, u.username"
        " FROM payroll_adjustments a LEFT JOIN users u"
        " ON u.id = a.created_by_user_id ORDER BY a.kind",
    )
    by_kind = {a.kind: a for a in adjustments}
    assert sorted(by_kind) == ["hours_correction", "late_approval", "manual"]
    late, manual = by_kind["late_approval"], by_kind["manual"]
    assert periods_generator.steps.adjustments == ADJUSTMENT_STEPS
    assert (late.amount, late.class_kind, late.class_session_id, late.username) == (
        D("240.00"),
        "individual",
        l1.id,
        None,
    )
    assert late.source_key == f"approval:{approvals[l1.id].id}"
    assert (manual.amount, manual.reason, manual.source_key, manual.username) == (
        D("120.00"),
        MANUAL_REASON,
        None,
        "demo.finance",
    )
    assert late.target_period_id == manual.target_period_id == w3.id
    [slip] = [s for s in payslips if s.payroll_period_id == w3.id]
    shown = [
        (line["adjustment"]["kind"], line["adjustment"]["service_date"],
         line["adjustment"]["class_kind"], line["adjustment"]["reason"])
        for line in slip.snapshot["lines"]
        if line["adjustment"] is not None
    ]  # fmt: skip
    assert sorted(shown) == [
        ("late_approval", "2026-10-19", "individual", None),
        ("manual", None, None, MANUAL_REASON),
    ]
    assert slip.snapshot["total"] == "970.00"

    # C4: no unresolved work for either tutor.
    assert asyncio.run(_unresolved(migrated_url)) == []

    # D1: H2 corrected to 60 minutes; one hours_correction of +20.00 in the
    # current week.
    h2 = _class(migrated_url, H2_START)
    [current] = run_sql(
        migrated_url,
        "SELECT a.id, a.approved_minutes, a.source, c.reason, c.new_minutes"
        " FROM session_closeouts s"
        " JOIN session_closeout_approvals a ON a.id = s.current_approval_id"
        " JOIN session_closeout_corrections c ON c.closeout_id = s.id"
        " WHERE s.class_session_id = :h2",
        {"h2": h2.id},
    )
    assert (current.approved_minutes, current.source) == (60, "staff_correction")
    assert (current.reason, current.new_minutes) == (CORRECTION_REASON, 60)
    correction = by_kind["hours_correction"]
    assert corrections.adjustments == ADJUSTMENT_STEPS
    assert (correction.amount, correction.class_session_id, correction.username) == (
        D("20.00"),
        h2.id,
        None,
    )
    assert correction.source_key == f"approval:{current.id}"
    assert correction.target_period_id == periods["demo.tutor"][3].id

    # D2: PP1 pays W1; PP2 stays on W2; PP3 reversed by demo.owner.
    recorded = corrections.recorded
    assert recorded is not None
    paid = run_sql(
        migrated_url,
        "SELECT pp.id, pp.payroll_period_id, pp.paid_on, pp.amount, pp.method_code,"
        " pp.reference, u.username, v.reason, vu.username AS reversed_by"
        " FROM payroll_payments pp JOIN users u ON u.id = pp.recorded_by_user_id"
        " LEFT JOIN payroll_payment_reversals v ON v.payroll_payment_id = pp.id"
        " LEFT JOIN users vu ON vu.id = v.reversed_by_user_id ORDER BY pp.reference",
    )
    tutor_weeks = [r.id for r in periods["demo.tutor"]]
    assert [p.id for p in paid] == [recorded.pp1, recorded.pp2, recorded.pp3]
    assert [tuple(p)[1:] for p in paid] == [
        (tutor_weeks[0], date(2026, 11, 4), D("70.00"), "transfer", PP1_REFERENCE,
         "demo.finance", None, None),
        (tutor_weeks[1], date(2026, 11, 4), D("200.00"), "transfer", PP2_REFERENCE,
         "demo.finance", None, None),
        (tutor_weeks[1], date(2026, 11, 4), D("100.00"), "cash", PP3_REFERENCE,
         "demo.finance", REVERSAL_REASON, "demo.owner"),
    ]  # fmt: skip
    assert _paid_states(migrated_url) == {
        ("demo.tutor", WEEKS[0]): ("paid", D("70.00")),
        ("demo.tutor", WEEKS[1]): ("partially_paid", D("200.00")),
        ("demo.tutor", WEEKS[2]): ("unpaid", D("0.00")),
        ("demo.tutor2", WEEKS[0]): ("unpaid", D("0.00")),
        ("demo.tutor2", WEEKS[1]): ("unpaid", D("0.00")),
        ("demo.tutor2", WEEKS[2]): ("unpaid", D("0.00")),
    }

    # The audit actors: demo.finance prepares, posts the manual and pays;
    # demo.owner finalizes and reverses; the job steps none.
    actors = run_sql(
        migrated_url,
        "SELECT e.record_type, e.action, u.username FROM audit_events e"
        " LEFT JOIN users u ON u.id = e.actor_user_id"
        " WHERE e.record_type IN ('payroll_period', 'payroll_payments',"
        " 'payroll_payment_reversals', 'payroll_adjustments', 'payslips',"
        " 'tutor_pay_terms', 'compensation_rates', 'pay_item_types', 'pay_items')"
        " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
    )
    by_record: dict[tuple[str, str], set[str | None]] = {}
    for r in actors:
        by_record.setdefault((r.record_type, r.action), set()).add(r.username)
    assert by_record[("payroll_period", "payroll.period_prepared")] == {"demo.finance"}
    assert by_record[("payroll_period", "payroll.period_finalized")] == {"demo.owner"}
    assert by_record[("payslips", "create")] == {"demo.owner"}
    assert by_record[("payroll_payments", "create")] == {"demo.finance"}
    assert by_record[("payroll_payment_reversals", "create")] == {"demo.owner"}
    assert by_record[("payroll_adjustments", "create")] == {None, "demo.finance"}
    for record in (
        "tutor_pay_terms",
        "compensation_rates",
        "pay_item_types",
        "pay_items",
    ):
        assert by_record[(record, "create")] == {"demo.finance"}, record
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS


def _paid_states(url: str) -> dict[tuple[str, date], tuple[str, Decimal]]:
    """D2: each finalized period's paid state and paid total, by the payroll
    module's own reads."""
    rows = run_sql(
        url,
        "SELECT p.id, u.username, p.starts_on, p.total_amount FROM payroll_periods p"
        " JOIN users u ON u.id = p.tutor_user_id WHERE p.status = 'finalized'",
    )
    return asyncio.run(_paid_states_of(url, rows))


async def _paid_states_of(
    url: str, rows: list[Any]
) -> dict[tuple[str, date], tuple[str, Decimal]]:
    engine = short_lived_engine(url)
    out: dict[tuple[str, date], tuple[str, Decimal]] = {}
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            for row in rows:
                paid = await payroll.paid_total(uow.session, row.id)
                out[(row.username, row.starts_on)] = (
                    payroll.paid_state(Money(row.total_amount), paid),
                    paid.amount,
                )
    finally:
        await engine.dispose()
    return out


def test_the_jobs_do_nothing_after_the_seed_and_a_rerun_writes_nothing(
    migrated_url, env_for, clock, capsys
):
    # E7: worker parity on the same frozen day; then E4: a rerun the same day
    # and 10 days later prints 0 rows (the markers come first).
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()
    settings = settings_for(migrated_url)
    before = demo_table_counts(migrated_url, ALL_TABLE_COUNTS)

    opened = asyncio.run(run_open_periods(settings))
    adjusted = asyncio.run(run_post_adjustments(settings))
    charged = asyncio.run(run_post_session_charges(settings))

    assert (opened.tutors, opened.opened, opened.failed) == (2, 0, 0)
    assert (adjusted.posted, adjusted.failed) == (0, 0)
    assert (charged.posted, charged.failed) == (0, 0)
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
    # E3: two ended weeks (10-19 and 10-26; F = 10-19 on a weekend).
    clock.set(now)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[:-3] == FIRST_RUN_LINES[:-3]
    assert lines[-3:] == [
        _line(PAYROLL_GENERATORS[0], DEMO_PAYROLL_SETUP_ROWS),
        _line(PAYROLL_GENERATORS[1], WEEKEND_PERIODS_ROWS),
        _line(PAYROLL_GENERATORS[2], DEMO_PAYROLL_CORRECTIONS_ROWS),
    ]
    periods = _periods(migrated_url)
    for rows in periods.values():
        assert [r.status for r in rows] == ["finalized", "finalized", "open"]
    assert count(migrated_url, "payslips") == 4
    assert asyncio.run(_unresolved(migrated_url)) == []
    assert app_clock.now() == now


def test_dev_writes_no_payroll_data(migrated_url, env_for, capsys):
    # E5.
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    _no_payroll_data(migrated_url)


def test_without_billing_leaves_payroll_off(migrated_url, env_for, clock, capsys):
    # E5, E3: the E2E seed. The three payroll generators skip; L1 and N1
    # don't exist and H2 keeps 55 minutes.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main(["--without-billing"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[-3:] == [
        f"{name}: skipped ({WITHOUT_BILLING})" for name in PAYROLL_GENERATORS
    ]
    assert lines[-9:-3] == [
        f"{name}: skipped ({WITHOUT_BILLING})" for name in FLAGGED_GENERATORS[:6]
    ]
    assert lines[:-9] == first_run_lines(
        generators_through("academics.demo_progress_reports")
    )
    _no_payroll_data(migrated_url)
    assert (
        demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_354)
        == TABLE_COUNTS_THROUGH_354
    )
    h2 = _class(migrated_url, H2_START)
    [minutes] = run_sql(
        migrated_url,
        "SELECT a.approved_minutes FROM session_closeouts s"
        " JOIN session_closeout_approvals a ON a.id = s.current_approval_id"
        " WHERE s.class_session_id = :h2",
        {"h2": h2.id},
    )
    assert minutes.approved_minutes == 55


def test_the_payroll_generators_come_last():
    # A1.
    assert len(GENERATOR_NAMES) == 31
    assert GENERATOR_NAMES[-3:] == list(PAYROLL_GENERATORS)
    assert GENERATOR_NAMES[-4] == REMINDERS_GENERATOR
    assert FLAGGED_GENERATORS[-3:] == PAYROLL_GENERATORS
    assert (
        sum(DEMO_PAYROLL_TABLE_COUNTS.values())
        == DEMO_PAYROLL_SETUP_ROWS
        + DEMO_PAYROLL_PERIODS_ROWS
        + DEMO_PAYROLL_CORRECTIONS_ROWS
    )


# --- E6: stale data, hand-configured payroll, prerequisites, rollback -----------------


def test_billing_from_an_earlier_day_is_skipped(migrated_url, env_for, clock, capsys):
    # The generators through billing.demo_reminders on 2026-11-04, then
    # every generator 10 days later.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(REMINDERS_GENERATOR)) == 0
    capsys.readouterr()

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *RERUN_REFERENCE_LINES,
        *(
            f"{name}: skipped ({STALE_SCHEDULE})"
            if name in PAYROLL_GENERATORS
            else f"{name}: 0 rows"
            for name in GENERATOR_NAMES
        ),
    ]
    _no_payroll_data(migrated_url)
    assert (
        demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_420)
        == TABLE_COUNTS_THROUGH_420
    )


async def _a_type_by_hand(url: str) -> None:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await payroll.add_pay_item_type(
                uow.session,
                PayItemTypeCreate(name="Bono de fin de año", direction="bonus"),
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("by_hand", ["floor", "type"])
def test_payroll_set_up_by_hand_is_skipped(
    migrated_url, env_for, clock, capsys, by_hand
):
    # G8: the owner turned payroll on, or added a pay item type, first.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(REMINDERS_GENERATOR)) == 0
    capsys.readouterr()
    if by_hand == "floor":
        run_sql(migrated_url, "UPDATE payroll_settings SET payroll_starts_on = :d",
                {"d": date(2026, 11, 2)})  # fmt: skip
    else:
        asyncio.run(_a_type_by_hand(migrated_url))

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines()[-3:] == [
        f"{name}: skipped ({PAYROLL_CONFIGURED})" for name in PAYROLL_GENERATORS
    ]
    for table in PAYROLL_TABLES:
        if table != "pay_item_types":
            assert count(migrated_url, table) == 0, table
    assert count(migrated_url, "pay_item_types") == (1 if by_hand == "type" else 0)
    assert (
        count(migrated_url, "class_sessions")
        == TABLE_COUNTS_THROUGH_420["class_sessions"]
    )


def test_a_missing_tutor2_fails_the_run(migrated_url, env_for, clock):
    # G8.5: demo.tutor2 missing (renamed); the run rolls back.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(REMINDERS_GENERATOR)) == 0
    run_sql(
        migrated_url,
        "UPDATE users SET username = 'otro.tutor' WHERE username = 'demo.tutor2'",
    )

    with pytest.raises(DemoPrerequisiteMissingError, match=r"demo\.tutor2"):
        demo.main([], generators=_fresh_payroll()[-3:])

    _no_payroll_data(migrated_url)


async def _correct_h2_by_hand(url: str) -> None:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            reviewer = await demo_principal(
                uow.session, DEMO_COORDINATOR_USERNAME, "test"
            )
            [row] = (
                await uow.session.execute(_H2_QUERY, {"zone": ZONE, "start": H2_START})
            ).all()
            await session_closeout_review.correct_closeout(
                uow.session,
                principal=reviewer,
                class_session_id=row.id,
                body=CloseoutCorrection(
                    version=row.version, actual_minutes=58, reason="Ajuste manual"
                ),
            )
    finally:
        await engine.dispose()


_H2_QUERY = text(
    "SELECT cs.id, s.version FROM class_sessions cs"
    " JOIN session_closeouts s ON s.class_session_id = cs.id"
    " JOIN users u ON u.id = cs.tutor_user_id"
    " WHERE to_char(cs.starts_at AT TIME ZONE :zone, 'YYYY-MM-DD HH24:MI')"
    " = :start AND u.username = 'demo.tutor'"
)


def test_h2_already_corrected_fails_the_run(migrated_url, env_for, clock):
    # G8.5: H2 corrected by hand first; setup and periods roll back with the
    # corrections' prerequisite error, L1 and N1 included.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(REMINDERS_GENERATOR)) == 0
    asyncio.run(_correct_h2_by_hand(migrated_url))
    sessions = count(migrated_url, "class_sessions")

    with pytest.raises(DemoPrerequisiteMissingError, match="H2"):
        demo.main([])

    _no_payroll_data(migrated_url)
    assert count(migrated_url, "class_sessions") == sessions


def test_a_refused_payment_rolls_the_run_back(migrated_url, env_for, clock):
    # E6: PP2 above W2's balance is refused; everything rolls back (L1, N1,
    # the charges, the floor); the clock and the session actor are restored.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = [
        *_fresh_payroll()[:-1],
        DemoPayrollCorrectionsGenerator(pp2_amount="1000.00"),
    ]

    with pytest.raises(payroll.PayrollPaymentExceedsBalanceError):
        demo.main([], generators=generators)

    assert app_clock.now() == FROZEN_NOW
    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "class_sessions") == 0
    assert count(migrated_url, "charges") == 0
    _no_payroll_data(migrated_url)

    # The actor: the generator on a session of a seeded database.
    assert demo.main([], generators=_fresh_payroll()[:-1]) == 0
    asyncio.run(_refused_in_a_session(migrated_url))
    assert count(migrated_url, "payroll_payments") == 0


async def _refused_in_a_session(url: str) -> None:
    engine = short_lived_engine(url)
    try:
        with pytest.raises(payroll.PayrollPaymentExceedsBalanceError):
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                saved = uow.session.info[ACTOR_INFO_KEY]
                try:
                    await DemoPayrollCorrectionsGenerator(
                        pp2_amount="1000.00"
                    ).generate(uow.session)
                finally:
                    assert uow.session.info[ACTOR_INFO_KEY] is saved
                    assert app_clock.now() == FROZEN_NOW
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "generator",
    [DemoPayrollPeriodsGenerator, DemoPayrollCorrectionsGenerator],
    ids=lambda g: g.name,
)
def test_without_the_setup_the_run_fails(migrated_url, env_for, clock, generator):
    # A2: a later payroll generator without payroll.demo_setup raises and
    # nothing at all is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    with pytest.raises(DemoPrerequisiteMissingError, match="demo_setup"):
        demo.main(
            [], generators=[*generators_through(REMINDERS_GENERATOR), generator()]
        )

    assert count(migrated_url, "users") == 0
