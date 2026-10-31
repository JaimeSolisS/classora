"""The suite's own tests (#103 B1–B4, C4, C5).

- **B1/B4:** seed and step parsing (defaults, ``BILLING_INVARIANT_SEEDS``,
  ``…_EXTRA_SEED``, ``…_STEPS``; an invalid value is a usage error).
- **B2:** one seed run twice (30 steps) gives the same trace.
- **B3:** a planted failure inside a sequence gives the full report.
- **C4:** each App-enforced check, given one planted violating state
  (written around the service on a world copy), reports exactly it, and no
  other check fires. ``REC`` is planted by making ``billing.service`` lie.
  An empty world fires nothing.
- **C5:** every append-only billing table (a ``*_no_update_delete``
  trigger on a table of the billing models) is covered by I11.

DB-enforced, so the queries are defense in depth (the database refuses the
violating row, nothing to plant): ``I4`` (composite foreign keys), ``I5``
(composite foreign key), ``I6`` (CHECKs), ``I7`` (CHECK), ``I12`` (partial
UNIQUEs), ``T1`` (natural key), ``K1`` (partial UNIQUEs), ``R1`` (UNIQUE),
``S1`` (UNIQUE) and ``I11`` (the append-only triggers refuse ``UPDATE`` and
``DELETE``; its self-test changes a row with the trigger disabled for one
statement, on a throwaway copy)."""

import asyncio
import random
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.db import create_sessionmaker
from app.core.models import Base
from app.core.money import Money
from app.modules.billing import models as billing_models
from app.modules.billing import service as billing_service
from app.modules.scheduling.schemas import GroupEnrollmentCreate
from app.workflows import (
    group_enrollment,
    session_charges,
    tuition_charges,
    tuition_plans,
)
from app.workflows.tuition_plan_schemas import TuitionPlanCreate
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.billing_invariants import checks, harness
from tests.integration.billing_invariants import world as worlds
from tests.integration.billing_invariants.generators import LedgerGenerator
from tests.integration.billing_invariants.harness import Ctx, Op, Outcome
from tests.integration.billing_invariants.routes import Builder
from tests.integration.conftest import run_sql, settings_for, short_lived_engine

# --- B1, B4 --------------------------------------------------------------------------


def test_billing_invariant_seeds_default_replace_and_extra() -> None:
    assert harness.seeds([1, 2], env={}) == [1, 2]
    assert harness.seeds([1, 2], env={harness.SEEDS_ENV: "5,9"}) == [5, 9]
    assert harness.seeds([1, 2], env={harness.EXTRA_SEED_ENV: "123"}) == [1, 2, 123]
    assert harness.seeds(
        [1, 2], env={harness.SEEDS_ENV: "5", harness.EXTRA_SEED_ENV: "5"}
    ) == [5]
    assert harness.step_count(60, env={}) == 60
    assert harness.step_count(60, env={harness.STEPS_ENV: "17"}) == 17
    assert [p.id for p in harness.seed_params([3])] in (["seed=3"], [])


@pytest.mark.parametrize(
    "env",
    [
        {harness.SEEDS_ENV: "abc"},
        {harness.SEEDS_ENV: "1,,2"},
        {harness.SEEDS_ENV: ""},
        {harness.SEEDS_ENV: "-1"},
        {harness.EXTRA_SEED_ENV: "x"},
    ],
)
def test_billing_invariant_bad_seeds_are_a_usage_error(env: dict[str, str]) -> None:
    with pytest.raises(pytest.UsageError):
        harness.seeds([1], env=env)


@pytest.mark.parametrize("raw", ["0", "abc", "-3"])
def test_billing_invariant_bad_steps_are_a_usage_error(raw: str) -> None:
    with pytest.raises(pytest.UsageError):
        harness.step_count(60, env={harness.STEPS_ENV: raw})


# --- B2 ------------------------------------------------------------------------------


def test_billing_invariant_one_seed_twice_gives_the_same_trace(
    world_template: worlds.Template, world_copy: Callable[[], str]
) -> None:
    traces = [
        harness.run_in_world(
            world_copy(),
            world_template.world,
            LedgerGenerator(),
            scenario="ledger",
            seed=5,
            steps=30,
            portal=False,
        )
        for _ in range(2)
    ]
    first, second = (t.signature() for t in traces)
    assert len(first) >= 30
    assert first == second


# --- B3 ------------------------------------------------------------------------------


class PlantingGenerator:
    """Step 1 a real payment; step 2 an adjustment written around the
    service that takes a charge below zero (declared, so only I1 fails)."""

    def __init__(self) -> None:
        self.step = 0

    async def choose(self, rng: random.Random, ctx: Ctx) -> Op:
        self.step += 1
        if self.step == 1:
            from tests.integration.billing_invariants import operations as ops

            state = await ops.read_state(ctx)
            return ops.manual_charge(rng, ctx, state, ctx.world.families["F1"])
        info = ctx.world.families["F1"]

        async def _plant(ctx: Ctx, engine: AsyncEngine) -> Outcome:
            [charge] = await ctx.rows(
                "SELECT id, amount, student_id FROM charges"
                " WHERE billing_account_id = :a",
                a=info.account,
            )
            async with ctx.uow(engine) as uow:
                from sqlalchemy import text

                await uow.session.execute(
                    text(
                        "INSERT INTO charge_adjustments (charge_id, billing_account_id,"
                        " student_id, kind, amount, reason, posted_at) VALUES (:c, :a,"
                        " :s, 'manual', :amount, 'Plantado', now())"
                    ),
                    {
                        "c": charge.id,
                        "a": info.account,
                        "s": charge.student_id,
                        "amount": -(charge.amount + 1),
                    },
                )
            return harness.accepted(
                info.account, -(Money(charge.amount) + Money("1.00"))
            )

        return Op("plant", "I1", _plant, account=info.account)

    async def burst(self, rng: random.Random, ctx: Ctx) -> list[Op]:
        return [await self.choose(rng, ctx)]


def test_billing_invariant_a_failure_reports_seed_step_check_and_reproduce(
    world_template: worlds.Template, world_copy: Callable[[], str]
) -> None:
    with pytest.raises(harness.SequenceFailure) as raised:
        harness.run_in_world(
            world_copy(),
            world_template.world,
            PlantingGenerator(),
            scenario="planted",
            seed=42,
            steps=2,
            portal=False,
        )
    message = str(raised.value)
    for needle in (
        "scenario: planted",
        "seed: 42",
        "step: 2",
        "check: I1:",
        "'net': Decimal('-1.00')",
        "last 25 operations:",
        "manual_charge F1/",
        "plant I1 → accepted",
        "reproduce: make test-billing-invariants SEEDS=42 STEPS=2",
    ):
        assert needle in message, (needle, message)


# --- C4 ------------------------------------------------------------------------------


def _sql(b: Builder, sql: str, **params: Any) -> list[Any]:
    return run_sql(b.url, sql, params)


def _one(b: Builder, sql: str, **params: Any) -> Any:
    [row] = _sql(b, sql, **params)
    return row


def _session_charge(b: Builder) -> Any:
    """A charged individual session of ``A1``: ``(session_id, charge)``."""
    day = worlds.TODAY + timedelta(days=2)

    async def _make(session: AsyncSession) -> uuid.UUID:
        made = await persist_class_session(
            session,
            subject_id=b.world.subjects["IND"],
            kind="individual",
            state="confirmed",
            starts_at=worlds.local(day, 9),
            ends_at=worlds.local(day, 10),
        )
        await add_participant(session, made, b.student)
        return made.id

    session_id = b.run(_make)
    asyncio.run(session_charges.run_post_session_charges(settings_for(b.url)))
    return _one(b, "SELECT * FROM charges WHERE class_session_id = :s", s=session_id)


def _adjust(b: Builder, charge: Any, kind: str, amount: str, key: str | None) -> Any:
    return _one(
        b,
        "INSERT INTO charge_adjustments (charge_id, billing_account_id, student_id,"
        " kind, amount, reason, source_key, posted_at) VALUES (:c, :a, :s, :k, :m,"
        " :r, :key, now()) RETURNING id",
        c=charge.id,
        a=charge.billing_account_id,
        s=charge.student_id,
        k=kind,
        m=amount,
        r="Plantado" if kind == "manual" else None,
        key=key,
    ).id


def _charge_row(b: Builder, charge_id: uuid.UUID) -> Any:
    return _one(b, "SELECT * FROM charges WHERE id = :c", c=charge_id)


def _payment_row(b: Builder, payment_id: uuid.UUID) -> Any:
    return _one(b, "SELECT * FROM payments WHERE id = :p", p=payment_id)


def _allocate(b: Builder, payment: uuid.UUID, charge: uuid.UUID, amount: str) -> None:
    _sql(
        b,
        "INSERT INTO payment_allocations (payment_id, charge_id, billing_account_id,"
        " amount) VALUES (:p, :c, :a, :m)",
        p=payment,
        c=charge,
        a=b.family.account,
        m=amount,
    )


def _refund(
    b: Builder, payment: uuid.UUID, amount: str, withdrawal: Any = None
) -> None:
    _sql(
        b,
        "INSERT INTO refunds (payment_id, billing_account_id, amount, refunded_on,"
        " payment_method_code, reason, refunded_by_user_id, withdrawal_id) VALUES"
        " (:p, :a, :m, :d, 'cash', 'Plantado', :u, :w)",
        p=payment,
        a=b.family.account,
        m=amount,
        d=worlds.TODAY,
        u=b.world.owner.id,
        w=withdrawal,
    )


def _reverse(b: Builder, payment: uuid.UUID) -> None:
    _sql(
        b,
        "INSERT INTO payment_reversals (payment_id, billing_account_id, reason,"
        " reversed_by_user_id) VALUES (:p, :a, 'Plantado', :u)",
        p=payment,
        a=b.family.account,
        u=b.world.owner.id,
    )


def _units(b: Builder, package: uuid.UUID, kind: str, units: int, **more: Any) -> None:
    columns = ", ".join(["package_id", "kind", "units", *more])
    values = ", ".join([":package", ":kind", ":units", *(f":{k}" for k in more)])
    _sql(
        b,
        f"INSERT INTO package_credit_transactions ({columns}) VALUES ({values})",  # noqa: S608
        package=package,
        kind=kind,
        units=units,
        **more,
    )


def _grant_of(b: Builder, package: uuid.UUID) -> Any:
    return _one(
        b,
        "SELECT id FROM package_credit_transactions WHERE package_id = :p"
        " AND kind = 'grant'",
        p=package,
    ).id


def _purchase_charge(b: Builder, package: uuid.UUID) -> Any:
    return _one(b, "SELECT * FROM charges WHERE package_id = :p", p=package)


def _enroll(b: Builder) -> uuid.UUID:
    change = b.run(
        lambda s: group_enrollment.enroll_student(
            s,
            b.world.groups["G1"],
            GroupEnrollmentCreate(student_id=b.student),
            actor_id=b.world.owner.id,
        )
    )
    return uuid.UUID(str(change.enrollment_id))


def _withdrawal_row(b: Builder, *, created: str = "now()", **subject: Any) -> Any:
    [(column, value)] = subject.items()
    return _one(
        b,
        "INSERT INTO withdrawals (billing_account_id, student_id,"  # noqa: S608
        f" {column}, outcome, amount, refund_amount, withdrawn_at, reason, preview,"
        " confirmed_by_user_id, created_at) VALUES (:a, :s, :v, 'account_credit', 0,"
        f" 0, now(), 'Plantado', '{{}}', :u, {created}) RETURNING id",
        a=b.family.account,
        s=b.student,
        v=value,
        u=b.world.owner.id,
    ).id


def plant_i1(b: Builder) -> Any:
    charge = _charge_row(b, b.charge("100.00"))
    _adjust(b, charge, "manual", "-150.00", None)
    return charge.id


def plant_i2(b: Builder) -> Any:
    charge = b.charge("100.00")
    _allocate(b, b.payment("200.00"), charge, "150.00")
    return charge


def plant_i3(b: Builder) -> Any:
    charge = b.charge("100.00")
    payment = b.payment("50.00")
    _allocate(b, payment, charge, "30.00")
    _refund(b, payment, "30.00")
    return payment


def plant_i3r(b: Builder) -> Any:
    charge = b.charge("100.00")
    payment = b.payment("50.00")
    _allocate(b, payment, charge, "30.00")
    _reverse(b, payment)
    return payment


def plant_i3x(b: Builder) -> Any:
    payment = b.payment("50.00")
    _refund(b, payment, "10.00")
    _reverse(b, payment)
    return payment


def plant_i7d(b: Builder) -> Any:
    charge = b.charge("100.00")
    discount = _one(b, "SELECT id FROM discounts WHERE kind = 'sibling'").id
    _sql(
        b,
        "INSERT INTO charge_discounts (charge_id, discount_id, position, kind, name,"
        " percent, amount) VALUES (:c, :d, 1, 'sibling', 'Hermanos', 10, 5)",
        c=charge,
        d=discount,
    )
    return charge


def plant_i8(b: Builder) -> Any:
    package = b.package()
    _units(b, package, "expire", -(worlds.PACKAGE_UNITS + 1))
    return package


def plant_i9(b: Builder) -> Any:
    charge = _session_charge(b)
    package = b.package()
    _units(
        b,
        package,
        "consume",
        -1,
        class_session_id=charge.class_session_id,
        student_id=charge.student_id,
    )
    return charge.class_session_id


def plant_i10(b: Builder) -> Any:
    return _one(
        b,
        "INSERT INTO payments (billing_account_id, amount, received_on,"
        " payment_method_code, recorded_by_user_id) VALUES (:a, 10, :d, 'cash', :u)"
        " RETURNING id",
        a=b.family.account,
        d=worlds.TODAY,
        u=b.world.owner.id,
    ).id


def plant_i10n(b: Builder) -> Any:
    b.payment("10.00")  # R-2041-000001 (a real receipt)
    payment = plant_i10(b)
    _sql(
        b,
        "INSERT INTO receipts (number, payment_id, billing_account_id, issued_at,"
        " language, template_version, snapshot) VALUES ('R-2041-000009', :p, :a,"
        " now(), 'es', 1, '{}')",
        p=payment,
        a=b.family.account,
    )
    return "2041"


def plant_w2(b: Builder) -> Any:
    charge = _charge_row(b, b.charge("100.00"))
    return _adjust(b, charge, "withdrawal", "-10.00", f"withdrawal:{charge.id}:x")


def plant_w3(b: Builder) -> Any:
    withdrawal = _withdrawal_row(b, package_id=b.package())
    _refund(b, b.payment("50.00"), "10.00", withdrawal)
    return withdrawal


def plant_w4(b: Builder) -> Any:
    withdrawal = _withdrawal_row(b, package_id=b.package())
    package = b.package()
    _units(b, package, "refund", -1, withdrawal_id=withdrawal)
    return package


def plant_w5(b: Builder) -> Any:
    enrollment = _enroll(b)
    _withdrawal_row(
        b, created="now() - interval '1 day'", group_enrollment_id=enrollment
    )
    return _one(
        b, "SELECT id FROM charges WHERE group_enrollment_id = :e", e=enrollment
    ).id


def plant_j5(b: Builder) -> Any:
    first = _enroll(b)
    fee = _one(b, "SELECT * FROM charges WHERE group_enrollment_id = :e", e=first)
    _sql(
        b,
        "UPDATE group_enrollments SET withdrawn_at = enrolled_at + interval '1 hour',"
        " withdrawn_by_user_id = enrolled_by_user_id"
        " WHERE id = :e",
        e=first,
    )
    # A second enrollment row of the same student and group, with a fee
    # posted while the first fee still has its whole net.
    second = _one(
        b,
        "INSERT INTO group_enrollments (group_id, student_id, enrolled_at,"
        " enrolled_by_user_id) SELECT group_id, student_id, enrolled_at + interval"
        " '2 hours', enrolled_by_user_id"
        " FROM group_enrollments WHERE id = :e RETURNING id",
        e=first,
    ).id
    return _one(
        b,
        "INSERT INTO charges (billing_account_id, student_id, kind,"
        " group_enrollment_id, service_date, list_amount, discount_amount, amount,"
        " due_date, posted_at, proration_remaining, proration_total) VALUES (:a, :s,"
        " 'group_enrollment', :e, :d, 100, 0, 100, :d, now(), 1, 1) RETURNING id",
        a=fee.billing_account_id,
        s=fee.student_id,
        e=second,
        d=fee.service_date + timedelta(days=1),
    ).id


def plant_t2(b: Builder) -> Any:
    detail, _ = b.run(
        lambda s: tuition_plans.create_tuition_plan(
            s,
            TuitionPlanCreate(
                student_id=b.student,
                service_price_id=b.world.tuition_item,
                starts_on=worlds.FLOOR,
            ),
        )
    )
    asyncio.run(
        tuition_charges.run_post_tuition_charges(settings_for(b.url), now=worlds.NOW)
    )
    _sql(
        b,
        "INSERT INTO tuition_plan_pauses (tuition_plan_id, starts_on, ends_on,"
        " created_at) VALUES (:p, :d, :e, now() - interval '1 day')",
        p=detail.id,
        d=worlds.FLOOR,
        e=worlds.FLOOR + timedelta(days=30),
    )
    return _one(b, "SELECT id FROM charges WHERE tuition_plan_id = :p", p=detail.id).id


def plant_k2(b: Builder) -> Any:
    package = b.package()
    day = worlds.TODAY + timedelta(days=worlds.PACKAGE_VALIDITY_DAYS + 10)

    async def _make(session: AsyncSession) -> uuid.UUID:
        made = await persist_class_session(
            session,
            subject_id=b.world.subjects["PKG"],
            kind="individual",
            state="confirmed",
            starts_at=worlds.local(day, 9),
            ends_at=worlds.local(day, 10),
        )
        await add_participant(session, made, b.student)
        return made.id

    session_id = b.run(_make)
    _units(b, package, "consume", -1, class_session_id=session_id, student_id=b.student)
    return package


def plant_r2(b: Builder) -> Any:
    charge = b.charge("100.00")
    return _one(
        b,
        "INSERT INTO charge_due_date_changes (charge_id, billing_account_id,"
        " previous_due_date, due_date, reason, changed_by_user_id) VALUES (:c, :a,"
        " :p, :d, 'Plantado', :u) RETURNING id",
        c=charge,
        a=b.family.account,
        p=worlds.TODAY - timedelta(days=5),
        d=worlds.TODAY + timedelta(days=5),
        u=b.world.owner.id,
    ).id


def plant_s2(b: Builder) -> Any:
    charge = _session_charge(b)
    _adjust(b, charge, "closure_credit", "10.00", f"closure_credit:{charge.id}:1")
    return charge.id


def plant_s4(b: Builder) -> Any:
    charge = _charge_row(b, b.charge("100.00"))
    return _adjust(
        b, charge, "cancellation_credit", "-10.00", f"cancellation_credit:{charge.id}"
    )


def plant_x2(b: Builder) -> Any:
    package = b.package()
    _units(b, package, "reversal", -1, reverses_transaction_id=_grant_of(b, package))
    return package


def plant_x3(b: Builder) -> Any:
    charge = _charge_row(b, b.charge("100.00"))
    return _adjust(
        b, charge, "package_cancellation", "-10.00", "package_cancellation:x"
    )


def plant_x4(b: Builder) -> Any:
    charge = _purchase_charge(b, b.package())
    return _adjust(b, charge, "manual", "-10.00", None)


def plant_sk(b: Builder) -> Any:
    charge = _session_charge(b)
    return _adjust(
        b, charge, "duration_change", "5.00", f"duration_change:{uuid.uuid4()}:9"
    )


def plant_s3(b: Builder) -> Any:
    charge = _session_charge(b)
    _sql(
        b,
        "UPDATE class_sessions SET scheduled_minutes = 90, ends_at = starts_at +"
        " interval '90 minutes', version = version + 1 WHERE id = :s",
        s=charge.class_session_id,
    )
    return charge.id


PLANTS: dict[str, Callable[[Builder], Any]] = {
    "I1": plant_i1,
    "I2": plant_i2,
    "I3": plant_i3,
    "I3r": plant_i3r,
    "I3x": plant_i3x,
    "I7d": plant_i7d,
    "I8": plant_i8,
    "I9": plant_i9,
    "I10": plant_i10,
    "I10n": plant_i10n,
    "W2": plant_w2,
    "W3": plant_w3,
    "W4": plant_w4,
    "W5": plant_w5,
    "J5": plant_j5,
    "T2": plant_t2,
    "K2": plant_k2,
    "R2": plant_r2,
    "S2": plant_s2,
    "S4": plant_s4,
    "X2": plant_x2,
    "X3": plant_x3,
    "X4": plant_x4,
    "SK": plant_sk,
    "S3": plant_s3,
}
IMPLIED: dict[str, set[str]] = {
    "I1": {"I2"},
    "I3r": {checks.RECONCILIATION_ID},
    "I3x": {checks.RECONCILIATION_ID},
}
"""Checks a planted violation breaks by definition: a negative net is
below any allocation (I2), and the ledger identity (REC) holds only while a
reversed payment has no valid allocation and no refund."""
DB_ENFORCED = frozenset({"I4", "I5", "I6", "I7", "I12", "T1", "K1", "R1", "S1"})


def _fired(
    url: str, *, after: tuple[str, ...] = ()
) -> dict[str, tuple[dict[str, Any], ...]]:
    async def _run() -> list[checks.Violation]:
        engine = short_lived_engine(url)
        try:
            async with create_sessionmaker(engine)() as session:
                return await checks.Catalogue().run(
                    session, today=worlds.TODAY, after=after
                )
        finally:
            await engine.dispose()

    return {v.check_id: v.rows for v in asyncio.run(_run())}


def test_billing_invariant_every_check_is_planted_or_db_enforced() -> None:
    plantable = {c.id for c in checks.CHECKS if c.plantable}
    assert set(PLANTS) == plantable
    assert {c.id for c in checks.CHECKS if not c.plantable} == DB_ENFORCED


def test_billing_invariant_an_empty_world_fires_nothing(
    world_template: worlds.Template, world_copy: Callable[[], str]
) -> None:
    assert _fired(world_copy(), after=tuple(checks.STEP_CHECK_KINDS)) == {}


@pytest.mark.parametrize("check_id", sorted(PLANTS))
def test_billing_invariant_a_planted_violation_fires_exactly_its_check(
    check_id: str, world_template: worlds.Template, world_copy: Callable[[], str]
) -> None:
    from tests.conftest import frozen_clock

    url = world_copy()
    with frozen_clock(worlds.NOW):
        planted = PLANTS[check_id](Builder(url, world_template.world))
    fired = _fired(url, after=tuple(checks.STEP_CHECK_KINDS))
    assert set(fired) == {check_id} | IMPLIED.get(check_id, set()), fired
    assert str(planted) in repr(fired[check_id]), (planted, fired[check_id])


def test_billing_invariant_reconciliation_catches_a_lying_service(
    world_template: worlds.Template,
    world_copy: Callable[[], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.conftest import frozen_clock

    url = world_copy()
    b = Builder(url, world_template.world)
    with frozen_clock(worlds.NOW):
        b.charge("100.00")
    real = billing_service.account_balance

    async def lying(session: AsyncSession, account: uuid.UUID, **kwargs: Any) -> Any:
        found = await real(session, account, **kwargs)
        from dataclasses import replace

        return replace(found, outstanding=found.outstanding + Money("0.01"))

    monkeypatch.setattr(billing_service, "account_balance", lying)
    fired = _fired(url)
    assert set(fired) == {checks.RECONCILIATION_ID}, fired
    assert str(b.family.account) in repr(fired[checks.RECONCILIATION_ID])


def test_billing_invariant_i11_catches_a_changed_posted_row(
    world_template: worlds.Template, world_copy: Callable[[], str]
) -> None:
    from tests.conftest import frozen_clock

    url = world_copy()
    b = Builder(url, world_template.world)
    with frozen_clock(worlds.NOW):
        charge = b.charge("100.00")
    catalogue = checks.Catalogue()

    async def _run() -> list[checks.Violation]:
        engine = short_lived_engine(url)
        try:
            async with create_sessionmaker(engine)() as session:
                return await catalogue.run(session, today=worlds.TODAY)
        finally:
            await engine.dispose()

    assert asyncio.run(_run()) == []
    # Throwaway copy: the trigger is off for this one statement only.
    run_sql(url, "ALTER TABLE charges DISABLE TRIGGER charges_no_update_delete")
    run_sql(
        url, "UPDATE charges SET description = 'Cambiado' WHERE id = :c", {"c": charge}
    )
    run_sql(url, "ALTER TABLE charges ENABLE TRIGGER charges_no_update_delete")
    found = asyncio.run(_run())
    assert [v.check_id for v in found] == [checks.IMMUTABILITY_ID]
    assert str(charge) in repr(found[0].rows)


# --- C5 ------------------------------------------------------------------------------


def test_billing_invariant_i11_covers_every_append_only_billing_table(
    world_template: worlds.Template,
) -> None:
    billing_tables = {
        table.name
        for table in Base.metadata.sorted_tables
        if table.name in _billing_model_tables()
    }
    rows = run_sql(
        world_template.url,
        "SELECT DISTINCT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid ="
        " t.tgrelid WHERE t.tgname LIKE '%\\_no\\_update\\_delete'"
        " AND NOT t.tgisinternal",
    )
    append_only = {row.relname for row in rows} & billing_tables
    assert append_only, "no append-only billing table found"
    missing = sorted(append_only - set(checks.APPEND_ONLY_TABLES))
    assert missing == [], (
        f"append-only billing tables the I11 check doesn't hash: {missing}"
    )


def _billing_model_tables() -> set[str]:
    return {
        mapper.class_.__tablename__
        for mapper in Base.registry.mappers
        if mapper.class_.__module__ == billing_models.__name__
    }
