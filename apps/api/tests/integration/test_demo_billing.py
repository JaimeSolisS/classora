"""The M6-demo billing data, part 1 (B, C, D, E2-E6): ``billing.demo_pricing``,
``billing.demo_packages`` and ``billing.demo_charges``, the whole run with
them, reruns, ``dev`` and ``--without-billing``, markers, the stale-schedule
and hand-configured skips, prerequisites, rollback, worker parity and the
invariant catalogue. What each account sees is in
``test_demo_billing_views``."""

import asyncio
import dataclasses
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from app.core import clock as app_clock
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.errors import PackageNotOfferedError
from app.workflows.enrollment_charges import run_post_enrollment_charges
from app.workflows.package_expiry import run_expire_packages
from app.workflows.session_charges import run_post_session_charges
from app.workflows.tuition_charges import run_post_tuition_charges
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.billing import persist_service_price
from tests.factories.demo_billing import (
    BILLING_CONFIGURED,
    H4_TOPICS,
    PK1_NAME,
    PK2_NAME,
    SCHOLARSHIP,
    SIBLING_DISCOUNT,
    WITHOUT_BILLING,
    DemoBillingPlan,
    DemoChargesGenerator,
    DemoPackagesGenerator,
    DemoPricingGenerator,
    SeedStep,
    plan_demo_billing,
)
from tests.factories.demo_changes import STALE_SCHEDULE
from tests.factories.identity import DemoPrerequisiteMissingError
from tests.integration.billing_invariants.checks import Catalogue
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.demo_support import (
    ALL_TABLE_COUNTS,
    BILLING_GENERATORS,
    DEMO_BILLING_TABLE_COUNTS,
    DEV_LINES,
    FIRST_RUN_LINES,
    FROZEN_NOW,
    GENERATOR_NAMES,
    PROGRESS_REPORTS_GENERATOR,
    RERUN_LINES,
    RERUN_REFERENCE_LINES,
    TABLE_COUNTS_THROUGH_354,
    UntouchedSession,
    count,
    demo_env,
    demo_table_counts,
    generators_through,
    migrate,
)
from tests.integration.test_demo_schedule import ZONE, student_labels

# T = Wednesday 2026-11-04: P(1) = 11-03, P(2) = 11-02, P(3) = 10-30,
# P(4) = 10-29, P(5) = 10-28, P(8) = 10-23, P(13) = 10-16 = B.
TODAY = date(2026, 11, 4)
FLOOR = date(2026, 10, 16)
BILLING_TABLES = (
    "service_prices",
    "price_versions",
    "discounts",
    "discount_assignments",
    "package_products",
    "packages",
    "package_credit_transactions",
    "billing_accounts",
    "tuition_plans",
    "charges",
    "charge_discounts",
)
MATH_SEC = "Matemáticas (Secundaria)"
MATH_PRI = "Matemáticas (Primaria)"
ENGLISH = "Inglés (Secundaria)"
CIENCIAS = "Ciencias (Secundaria)"

SESSION_OUTCOMES = Counter({"posted": 40, "package_covered": 0})
"""D3: the session step's outcomes at ``FROZEN_NOW`` (pinned)."""
ENROLLMENT_OUTCOMES = Counter({"posted": 2, "no_price": 3})
TUITION_OUTCOMES = Counter({"posted": 1})


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _fresh_billing() -> list[Any]:
    """The registered generators with new billing instances (their step
    counts are read after the run)."""
    return [
        *generators_through(PROGRESS_REPORTS_GENERATOR),
        DemoPricingGenerator(),
        DemoPackagesGenerator(),
        DemoChargesGenerator(),
    ]


def _labels(url: str) -> dict[Any, str]:
    labels = student_labels(url)
    [f3] = run_sql(
        url,
        "SELECT student_id FROM student_guardians WHERE relationship = 'grandparent'",
    )
    labels[f3.student_id] = "F3"
    return labels


def _families(url: str) -> dict[Any, str]:
    """F1 (S's family), F2 (A's) and F3 by family id."""
    labels = _labels(url)
    rows = run_sql(url, "SELECT family_id, student_id FROM family_students")
    out: dict[Any, str] = {}
    for row in rows:
        label = labels.get(row.student_id)
        if label in {"S", "Sib"}:
            out[row.family_id] = "F1"
        elif label in {"A", "B"}:
            out[row.family_id] = "F2"
        elif label == "F3":
            out[row.family_id] = "F3"
    return out


def price_rows(url: str) -> list[tuple[Any, ...]]:
    rows = run_sql(
        url,
        "SELECT p.class_type, p.pricing_model, s.name || ' (' || l.name || ')',"
        " p.duration_minutes, p.period_frequency, g.name AS grp, c.name AS cohort,"
        " p.education_level_id IS NULL AS any_level,"
        " array_agg(v.effective_from::text || '=' || v.amount::text"
        "  ORDER BY v.effective_from) AS versions"
        " FROM service_prices p JOIN subjects s ON s.id = p.subject_id"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " JOIN price_versions v ON v.service_price_id = p.id"
        " LEFT JOIN class_groups g ON g.id = p.group_id"
        " LEFT JOIN course_cohorts c ON c.id = p.cohort_id"
        " WHERE p.pricing_model <> 'package'"
        " GROUP BY 1, 2, 3, 4, 5, 6, 7, 8 ORDER BY 1, 2, 3",
    )
    return [tuple(r) for r in rows]


def package_rows(url: str) -> list[tuple[Any, ...]]:
    rows = run_sql(
        url,
        "SELECT pp.name, p.student_id, p.purchased_on::text, p.expires_on::text,"
        " array_agg(t.kind || ':' || t.units ORDER BY t.units DESC)"
        " FROM packages p JOIN package_products pp ON pp.id = p.package_product_id"
        " JOIN package_credit_transactions t ON t.package_id = p.id"
        " GROUP BY 1, 2, 3, 4 ORDER BY 3",
    )
    return [tuple(r) for r in rows]


async def _balances(
    url: str, families: dict[Any, str]
) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    """Per family: outstanding, overdue and Σ open of its charges on T."""
    engine = short_lived_engine(url)
    out: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            accounts = await uow.session.execute(
                text("SELECT id, family_id FROM billing_accounts")
            )
            for account in accounts.all():
                balance = await billing.account_balance(
                    uow.session, account.id, today=TODAY
                )
                charges = await billing.charge_balances(
                    uow.session, account.id, today=TODAY
                )
                out[families[account.family_id]] = (
                    balance.outstanding.amount,
                    balance.overdue.amount,
                    sum((c.open.amount for c in charges), Decimal("0.00")),
                )
    finally:
        await engine.dispose()
    return out


async def _violations(url: str) -> list[Any]:
    """D7: #103's whole-database check catalogue on ``T``."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return await Catalogue().run(uow.session, today=TODAY)
    finally:
        await engine.dispose()


def _discount_lines(url: str) -> dict[tuple[str, str, str], list[tuple[Any, ...]]]:
    """``(student label, kind, service date)`` → the charge's discount lines
    ``(position, kind, name, percent, amount)``."""
    labels = _labels(url)
    rows = run_sql(
        url,
        "SELECT c.student_id, c.kind, c.service_date::text AS day, d.position,"
        " d.kind AS dkind, d.name, d.percent, d.amount FROM charges c"
        " JOIN charge_discounts d ON d.charge_id = c.id"
        " ORDER BY c.student_id, c.kind, c.service_date, d.position",
    )
    out: dict[tuple[str, str, str], list[tuple[Any, ...]]] = {}
    for r in rows:
        key = (labels[r.student_id], r.kind, r.day)
        out.setdefault(key, [])
        line = (r.position, r.dkind, r.name, r.percent, r.amount)
        if line not in out[key]:
            out[key].append(line)
    return out


def test_a_first_run_writes_the_billing_data(migrated_url, env_for, clock, capsys):
    # E2 on Wednesday 2026-11-04, 11:00 local.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    generators = _fresh_billing()

    assert demo.main([], generators=generators) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    # The seed clock was restored.
    assert app_clock.now() == FROZEN_NOW
    labels = _labels(migrated_url)

    # B2: billing on from B, by demo.finance (one update event).
    [settings] = run_sql(migrated_url, "SELECT billing_starts_on FROM billing_settings")
    assert settings.billing_starts_on == FLOOR
    events = run_sql(
        migrated_url,
        "SELECT e.action, u.username FROM audit_events e"
        " LEFT JOIN users u ON u.id = e.actor_user_id"
        " WHERE e.record_type = 'billing_settings'",
    )
    assert [tuple(e) for e in events] == [("update", "demo.finance")]
    assert price_rows(migrated_url) == [
        ("course", "full_course", MATH_SEC, None, None, None,
         "Examen de ingreso, grupo 1", True, ["2026-10-16=2400.00"]),
        ("group", "per_session", MATH_SEC, None, None, "Club de Matemáticas", None,
         True, ["2026-10-16=180.00"]),
        ("individual", "per_period", CIENCIAS, None, "monthly", None, None, True,
         ["2026-10-16=1600.00"]),
        ("individual", "per_session", ENGLISH, 60, None, None, None, True,
         ["2026-10-16=380.00"]),
        ("individual", "per_session", MATH_PRI, 60, None, None, None, True,
         ["2026-10-16=300.00"]),
        ("individual", "per_session", MATH_SEC, 60, None, None, None, True,
         ["2026-10-16=350.00", "2026-12-01=380.00"]),
    ]  # fmt: skip
    discounts = run_sql(
        migrated_url,
        "SELECT d.kind, d.audience, d.name, d.percent, d.is_active,"
        " (SELECT array_agg(a.student_id) FROM discount_assignments a"
        "  WHERE a.discount_id = d.id) AS students"
        " FROM discounts d ORDER BY d.kind",
    )
    assert [(d[:5], [labels[s] for s in d.students or []]) for d in discounts] == [
        (("scholarship", "assigned", SCHOLARSHIP, Decimal("25.00"), True), ["A"]),
        (("sibling", "all", SIBLING_DISCOUNT, Decimal("10.00"), True), []),
    ]
    products = run_sql(
        migrated_url,
        "SELECT name, quantity, validity_days, is_active FROM package_products"
        " ORDER BY name",
    )
    assert [tuple(p) for p in products] == [
        (PK2_NAME, 4, 7, True),
        (PK1_NAME, 8, 60, True),
    ]

    # C2, C3: Sib's package lapsed whole; S's has 7 of 8 left (H4 consumed).
    assert [(r[0], labels[r[1]], *r[2:]) for r in package_rows(migrated_url)] == [
        (PK2_NAME, "Sib", "2026-10-16", "2026-10-23", ["grant:4", "expire:-4"]),
        (PK1_NAME, "S", "2026-10-30", "2026-12-29", ["grant:8", "consume:-1"]),
    ]
    purchases = run_sql(
        migrated_url,
        "SELECT service_date::text, due_date::text, list_amount, amount,"
        " u.username FROM charges c LEFT JOIN users u ON u.id = c.posted_by_user_id"
        " WHERE kind = 'package_purchase' ORDER BY service_date",
    )
    d = Decimal
    assert [tuple(p) for p in purchases] == [
        ("2026-10-16", "2026-10-16", d("1100.00"), d("990.00"), "demo.finance"),
        ("2026-10-30", "2026-10-30", d("2800.00"), d("2520.00"), "demo.finance"),
    ]
    [h4] = run_sql(
        migrated_url,
        "SELECT cs.state, to_char(cs.starts_at AT TIME ZONE :zone,"
        " 'YYYY-MM-DD HH24:MI') AS starts, a.status AS attended, sc.status,"
        " sc.topics, ru.username AS reviewer, t.kind AS consumed"
        " FROM class_sessions cs JOIN session_closeouts sc"
        "  ON sc.class_session_id = cs.id"
        " JOIN class_session_attendance a ON a.class_session_id = cs.id"
        " JOIN session_closeout_approvals ap ON ap.class_session_id = cs.id"
        " JOIN users ru ON ru.id = ap.approved_by_user_id"
        " JOIN package_credit_transactions t ON t.class_session_id = cs.id",
        {"zone": ZONE},
    )
    assert tuple(h4) == (
        "completed",
        "2026-11-02 17:30",
        "present",
        "approved",
        H4_TOPICS,
        "demo.coordinator",
        "consume",
    )

    # D2-D3: the job steps' outcomes and the charges by kind.
    charges_step = generators[-1]
    assert charges_step.steps.session == SESSION_OUTCOMES
    assert charges_step.steps.enrollment == ENROLLMENT_OUTCOMES
    assert charges_step.steps.tuition == TUITION_OUTCOMES
    packages_step = generators[-2]
    assert dataclasses.astuple(packages_step.expiry) == (1, 0, 0, 1, 0)
    by_kind = run_sql(
        migrated_url,
        "SELECT kind, count(*) AS n, count(posted_by_user_id) AS by_user"
        " FROM charges GROUP BY kind ORDER BY kind",
    )
    assert [tuple(r) for r in by_kind] == [
        ("cohort_enrollment", 2, 0),
        ("package_purchase", 2, 2),
        ("session", 40, 0),
        ("tuition_period", 1, 0),
    ]
    # No session charge for H4 (consumed) nor for an unconfirmed class.
    assert not run_sql(
        migrated_url,
        "SELECT c.id FROM charges c JOIN package_credit_transactions t"
        " ON t.class_session_id = c.class_session_id",
    )
    assert not run_sql(
        migrated_url,
        "SELECT c.id FROM charges c JOIN class_sessions cs"
        " ON cs.id = c.class_session_id"
        " WHERE cs.state NOT IN ('confirmed', 'completed')",
    )
    cohort = run_sql(
        migrated_url,
        "SELECT c.student_id FROM charges c JOIN cohort_enrollments e"
        " ON e.id = c.cohort_enrollment_id JOIN course_cohorts k"
        " ON k.id = e.cohort_id WHERE k.name = 'Examen de ingreso, grupo 1'",
    )
    assert sorted(labels[r.student_id] for r in cohort) == ["A", "S"]
    [plan] = run_sql(
        migrated_url,
        "SELECT p.student_id, p.starts_on, p.ends_on, p.frequency FROM tuition_plans p",
    )
    assert (labels[plan.student_id], plan.starts_on, plan.ends_on, plan.frequency) == (
        "F3",
        FLOOR,
        None,
        "monthly",
    )

    # D4: S's charges carry D1; A's group charges D2 then D1.
    lines = _discount_lines(migrated_url)
    sibling = (1, "sibling", SIBLING_DISCOUNT, Decimal("10.00"))
    assert [line[:4] for line in lines[("S", "session", "2026-10-27")]] == [sibling]
    group = [key for key in lines if key[0] == "A" and key[1] == "session"]
    assert group
    for key in group:
        assert [line[:4] for line in lines[key]] == [
            (1, "scholarship", SCHOLARSHIP, Decimal("25.00")),
            (2, "sibling", SIBLING_DISCOUNT, Decimal("10.00")),
        ]
    assert not [key for key in lines if key[0] == "F3"]

    # D5: F1 and F3 are overdue; F2 has only upcoming charges in part 1.
    balances = asyncio.run(_balances(migrated_url, _families(migrated_url)))
    for family, (outstanding, _, open_total) in balances.items():
        assert outstanding == open_total, family
    assert balances["F1"][1] > 0
    assert balances["F3"][1] == Decimal("1600.00")
    assert balances["F2"][1] == Decimal("0.00")
    assert balances["F2"][0] > 0

    # Every billing write is demo.finance's or a job step's (no actor).
    actors = run_sql(
        migrated_url,
        "SELECT DISTINCT e.record_type, u.username FROM audit_events e"
        " LEFT JOIN users u ON u.id = e.actor_user_id"
        " WHERE e.record_type IN ('service_prices', 'price_versions', 'discounts',"
        " 'discount_assignments', 'package_products', 'packages', 'tuition_plans')"
        " ORDER BY 1, 2",
    )
    assert {r.username for r in actors} == {"demo.finance"}

    # D7: no invariant violation.
    assert asyncio.run(_violations(migrated_url)) == []
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS


def test_the_billing_jobs_post_nothing_after_the_seed(migrated_url, env_for, clock):
    # D6: worker parity on the same frozen day.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    settings = settings_for(migrated_url)
    before = demo_table_counts(migrated_url, ALL_TABLE_COUNTS)

    sessions = asyncio.run(run_post_session_charges(settings))
    enrollments = asyncio.run(run_post_enrollment_charges(settings))
    tuition = asyncio.run(run_post_tuition_charges(settings))
    expiry = asyncio.run(run_expire_packages(settings))

    assert (sessions.posted, sessions.failed) == (0, 0)
    assert (enrollments.posted, enrollments.failed) == (0, 0)
    assert enrollments.no_price == 3  # the #353 cohort, Unbilled
    assert (tuition.posted, tuition.failed) == (0, 0)
    assert (expiry.expired, expiry.fallback_consumed, expiry.failed) == (0, 0, 0)
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == before


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 11, 8, 5, 30, tzinfo=UTC),  # Saturday 23:30 local
        datetime(2026, 11, 8, 18, 0, tzinfo=UTC),  # Sunday 12:00 local
    ],
    ids=["saturday-night", "sunday"],
)
def test_a_weekend_run_writes_the_same_counts(
    migrated_url, env_for, clock, capsys, now
):
    # E3.
    clock.set(now)
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS
    assert app_clock.now() == now


def test_a_rerun_writes_nothing_even_days_later(migrated_url, env_for, clock, capsys):
    # E4: every generator (25) prints 0 rows.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([]) == 0
    capsys.readouterr()
    assert len(GENERATOR_NAMES) == 25

    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines() == RERUN_LINES
    assert demo_table_counts(migrated_url, ALL_TABLE_COUNTS) == ALL_TABLE_COUNTS
    assert app_clock.now() == FROZEN_NOW + timedelta(days=10)


def _no_billing_data(url: str) -> None:
    [settings] = run_sql(url, "SELECT billing_starts_on FROM billing_settings")
    assert settings.billing_starts_on is None
    for table in BILLING_TABLES:
        assert count(url, table) == 0, table


def test_dev_writes_no_billing_data(migrated_url, env_for, capsys):
    # E5.
    env_for("dev", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == DEV_LINES
    _no_billing_data(migrated_url)


def test_without_billing_leaves_billing_off(migrated_url, env_for, clock, capsys):
    # E5, E3: the E2E seed. Every other line and table is unchanged.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    assert demo.main(["--seed", "1", "--without-billing"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"{line.split(':')[0]}: skipped ({WITHOUT_BILLING})"
        if line.split(":")[0] in BILLING_GENERATORS
        else line
        for line in FIRST_RUN_LINES
    ]
    _no_billing_data(migrated_url)
    assert (
        demo_table_counts(migrated_url, TABLE_COUNTS_THROUGH_354)
        == TABLE_COUNTS_THROUGH_354
    )
    # #299's P4 keeps no closeout; no H4.
    assert count(migrated_url, "session_closeouts") == 3 + 6
    # The flag doesn't outlive the run.
    capsys.readouterr()
    assert demo.main([]) == 0
    assert capsys.readouterr().out.splitlines()[-3:] == [
        "billing.demo_pricing: 22 rows",
        "billing.demo_packages: 21 rows",
        "billing.demo_charges: 97 rows",
    ]


NEW_GENERATORS = [DemoPricingGenerator, DemoPackagesGenerator, DemoChargesGenerator]


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
    # E5: --without-billing, without touching the session.
    result = asyncio.run(
        generator(app_env=lambda: "local", skipped=lambda: True).generate(
            UntouchedSession()  # type: ignore[arg-type]
        )
    )

    assert result == GeneratorSkipped(WITHOUT_BILLING)


def test_the_billing_generators_come_last():
    # A1.
    assert GENERATOR_NAMES[-3:] == list(BILLING_GENERATORS)
    assert GENERATOR_NAMES[-4] == PROGRESS_REPORTS_GENERATOR
    assert sum(DEMO_BILLING_TABLE_COUNTS.values()) == 22 + 21 + 97


# --- E6: markers, stale data, hand-configured billing, prerequisites, rollback --------


def test_a_schedule_from_an_earlier_day_is_skipped(
    migrated_url, env_for, clock, capsys
):
    # The #354 run on 2026-11-04, then every generator 10 days later.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(PROGRESS_REPORTS_GENERATOR)) == 0
    capsys.readouterr()

    clock.advance(timedelta(days=10))
    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        *RERUN_REFERENCE_LINES,
        *(
            f"{name}: skipped ({STALE_SCHEDULE})"
            if name in BILLING_GENERATORS
            else f"{name}: 0 rows"
            for name in GENERATOR_NAMES
        ),
    ]
    _no_billing_data(migrated_url)
    assert app_clock.now() == FROZEN_NOW + timedelta(days=10)


async def _a_price_by_hand(url: str) -> None:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            [subject] = (
                await uow.session.execute(text("SELECT id FROM subjects LIMIT 1"))
            ).all()
            await persist_service_price(
                uow.session,
                subject_id=subject.id,
                duration_minutes=60,
                versions=[(TODAY, "500.00")],
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("by_hand", ["floor", "price"])
def test_billing_set_up_by_hand_is_skipped(
    migrated_url, env_for, clock, capsys, by_hand
):
    # G6: the owner set billing or a price first.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)
    assert demo.main([], generators=generators_through(PROGRESS_REPORTS_GENERATOR)) == 0
    capsys.readouterr()
    if by_hand == "floor":
        run_sql(migrated_url, "UPDATE billing_settings SET billing_starts_on = :d",
                {"d": TODAY})  # fmt: skip
    else:
        asyncio.run(_a_price_by_hand(migrated_url))
    prices = count(migrated_url, "service_prices")

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines()[-3:] == [
        f"{name}: skipped ({BILLING_CONFIGURED})" for name in BILLING_GENERATORS
    ]
    assert count(migrated_url, "service_prices") == prices
    for table in BILLING_TABLES[2:]:
        assert count(migrated_url, table) == 0, table


@pytest.mark.parametrize(
    "generator", [DemoPackagesGenerator, DemoChargesGenerator], ids=lambda g: g.name
)
def test_without_the_prices_the_run_fails(migrated_url, env_for, clock, generator):
    # A2: a later billing generator without billing.demo_pricing raises and
    # nothing at all is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    with pytest.raises(DemoPrerequisiteMissingError, match="demo_pricing"):
        demo.main(
            [],
            generators=[*generators_through(PROGRESS_REPORTS_GENERATOR), generator()],
        )

    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "class_sessions") == 0


def test_a_refused_sale_rolls_the_run_back(migrated_url, env_for, clock):
    # E6: a plan double that sells PE the day before PK1's first version;
    # the price check refuses it and nothing at all is written.
    clock.set(FROZEN_NOW)
    env_for("local", migrated_url)

    def too_early(today: date) -> DemoBillingPlan:
        plan = plan_demo_billing(today)
        steps = tuple(
            SeedStep(s.action, plan.floor - timedelta(days=1), s.at)
            if s.action == "sell_pe"
            else s
            for s in plan.seed_steps
        )
        return dataclasses.replace(plan, seed_steps=steps)

    generators = [
        *generators_through(PROGRESS_REPORTS_GENERATOR),
        DemoPricingGenerator(),
        DemoPackagesGenerator(plan=too_early),
        DemoChargesGenerator(),
    ]
    with pytest.raises(PackageNotOfferedError):
        demo.main([], generators=generators)

    assert app_clock.now() == FROZEN_NOW
    assert count(migrated_url, "users") == 0
    for table in BILLING_TABLES:
        assert count(migrated_url, table) == 0, table
