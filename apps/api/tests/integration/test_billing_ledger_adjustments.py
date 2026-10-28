"""``post_adjustment`` (#90 E5; C4; I1, I2; #86 E5; data-model §3.10, §6).

- Positive and negative adjustments change the net; a credit to exactly 0
  is allowed (``void`` without allocations); below 0 is ``409
  charge_net_negative`` with nothing written (I1); a repeated
  ``source_key`` is ``created = False``.
- **Release (E5):** a credit that takes the net below the valid
  allocations releases the newest allocations first and re-inserts the part
  of the last one that wasn't needed, so Σ valid allocations = the new net
  and the freed money is account credit of the allocations' payments. A
  credit that leaves the net at or above the allocations, and a positive
  adjustment, release nothing.
- **Lock order:** with the account row held ``FOR UPDATE`` by another
  connection, ``post_adjustment`` waits until it is released (the #276
  lock-test pattern). Repeated 5 times.
"""

import asyncio
import time as monotonic_clock
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.schemas import AdjustmentDraft
from tests.factories import billing as factories
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import in_uow

HOLD_SECONDS = 1.0
REPEATS = 5


@dataclass(frozen=True)
class World:
    user: uuid.UUID
    account: uuid.UUID
    student: uuid.UUID
    charge: uuid.UUID
    older_payment: uuid.UUID | None = None
    newer_payment: uuid.UUID | None = None
    older_allocation: uuid.UUID | None = None
    newer_allocation: uuid.UUID | None = None


def _world(url: str, amount: str = "500.00") -> World:
    async def _make(session: AsyncSession) -> World:
        user = await factories.staff_user_id(session)
        _, [student] = await factories.family_with_students(session)
        posted = await factories.charge(session, student, amount)
        return World(
            user=user,
            account=posted.billing_account_id,
            student=student,
            charge=posted.charge_id,
        )

    return in_uow(url, _make)


def _pay(
    url: str, world: World, amount: str, allocated: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """A payment of ``amount`` with ``allocated`` on the world's charge (its
    own transaction, so allocations made later are newer)."""

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        paid = await factories.payment(
            session, world.account, amount, recorded_by=world.user
        )
        row = await factories.allocation(session, paid, world.charge, allocated)
        return paid.id, row.id

    return in_uow(url, _make)


def _allocated_world(url: str) -> World:
    """A 500 charge with 300 (older payment) and 200 (newer payment)
    allocated."""
    world = _world(url)
    older_payment, older_allocation = _pay(url, world, "300.00", "300.00")
    newer_payment, newer_allocation = _pay(url, world, "200.00", "200.00")
    return World(
        **{
            **world.__dict__,
            "older_payment": older_payment,
            "newer_payment": newer_payment,
            "older_allocation": older_allocation,
            "newer_allocation": newer_allocation,
        }
    )


def _adjust(
    url: str, world: World, amount: str, *, key: str | None = None, **kw: Any
) -> billing.PostedAdjustment:
    draft = AdjustmentDraft(
        charge_id=world.charge,
        kind=kw.pop("kind", "cancellation_credit"),
        amount=Money(amount),
        source_key=key if key is not None else f"test:{uuid.uuid4()}",
        **kw,
    )
    return in_uow(url, lambda s: billing.post_adjustment(s, draft))


def _balance(url: str, world: World) -> billing.ChargeBalance:
    [balance] = in_uow(url, lambda s: billing.charge_balances(s, world.account))
    return balance


def _credit(url: str, world: World) -> Money:
    return in_uow(url, lambda s: billing.account_credit(s, world.account))


def _valid_allocations(url: str, world: World) -> list[tuple[uuid.UUID, Any]]:
    """``(payment_id, amount)`` of the charge's valid allocations, oldest
    first."""
    rows = run_sql(
        url,
        "SELECT pa.payment_id, pa.amount FROM payment_allocations pa"
        " WHERE pa.charge_id = :c AND NOT EXISTS (SELECT 1 FROM"
        " allocation_releases ar WHERE ar.allocation_id = pa.id)"
        " ORDER BY pa.created_at, pa.id",
        {"c": world.charge},
    )
    return [(row.payment_id, Money(row.amount)) for row in rows]


def _count(url: str, table: str, world: World) -> int:
    [row] = run_sql(
        url,
        f"SELECT count(*) AS n FROM {table} WHERE billing_account_id = :a",  # noqa: S608
        {"a": world.account},
    )
    return int(row.n)


def test_positive_and_negative_adjustments_change_the_net(module_seeded_url):
    url = module_seeded_url
    world = _world(url)

    plus = _adjust(url, world, "100.00", kind="duration_change")
    assert _balance(url, world).net == Money("600.00")
    minus = _adjust(url, world, "-250.00")

    balance = _balance(url, world)
    assert plus.created and minus.created
    assert (balance.adjustments_total, balance.net, balance.status) == (
        Money("-150.00"),
        Money("350.00"),
        "pending",
    )
    [row] = run_sql(
        url,
        "SELECT billing_account_id, student_id, kind, amount FROM"
        " charge_adjustments WHERE id = :id",
        {"id": minus.adjustment_id},
    )
    assert (row.billing_account_id, row.student_id, row.kind) == (
        world.account,
        world.student,
        "cancellation_credit",
    )


def test_a_credit_to_exactly_zero_voids_the_charge(module_seeded_url):
    url = module_seeded_url
    world = _world(url)

    _adjust(url, world, "-500.00")

    balance = _balance(url, world)
    assert (balance.net, balance.open, balance.status) == (
        Money.zero(),
        Money.zero(),
        "void",
    )


def test_a_credit_below_zero_is_refused_and_writes_nothing(module_seeded_url):
    url = module_seeded_url
    world = _world(url)
    _adjust(url, world, "-200.00")

    with pytest.raises(billing.ChargeNetNegativeError) as raised:
        _adjust(url, world, "-300.01")

    assert (raised.value.status, raised.value.code) == (409, "charge_net_negative")
    assert raised.value.details == {
        "charge_id": str(world.charge),
        "net": "300.00",
        "amount": "-300.01",
    }
    assert _count(url, "charge_adjustments", world) == 1
    assert _balance(url, world).net == Money("300.00")


def test_a_repeated_source_key_is_posted_once(module_seeded_url):
    url = module_seeded_url
    world = _world(url)
    key = f"cancellation_credit:{world.charge}"
    first = _adjust(url, world, "-100.00", key=key)

    again = _adjust(url, world, "-100.00", key=key)

    assert again == billing.PostedAdjustment(
        adjustment_id=first.adjustment_id, created=False
    )
    assert _count(url, "charge_adjustments", world) == 1


def test_a_manual_adjustment_needs_only_a_reason(module_seeded_url):
    url = module_seeded_url
    world = _world(url)
    draft = AdjustmentDraft(
        charge_id=world.charge, kind="manual", amount=Money("-1.00"), reason="Ajuste"
    )

    first = in_uow(url, lambda s: billing.post_adjustment(s, draft))
    second = in_uow(url, lambda s: billing.post_adjustment(s, draft))

    assert first.created and second.created
    assert _count(url, "charge_adjustments", world) == 2


def test_an_unknown_charge_is_a_programming_error(module_seeded_url):
    draft = AdjustmentDraft(
        charge_id=uuid.uuid4(), kind="manual", amount=Money("1.00"), reason="x"
    )
    with pytest.raises(ValueError, match="unknown charge"):
        in_uow(module_seeded_url, lambda s: billing.post_adjustment(s, draft))


def test_a_credit_below_both_allocations_releases_newest_first(module_seeded_url):
    url = module_seeded_url
    world = _allocated_world(url)
    credit_before = _credit(url, world)

    posted = _adjust(url, world, "-250.00")

    assert posted.released_allocation_ids == (
        world.newer_allocation,
        world.older_allocation,
    )
    assert posted.reinserted_allocation_id is not None
    assert _valid_allocations(url, world) == [(world.older_payment, Money("250.00"))]
    balance = _balance(url, world)
    assert (balance.net, balance.allocated, balance.open, balance.status) == (
        Money("250.00"),
        Money("250.00"),
        Money.zero(),
        "paid",
    )
    assert _credit(url, world) - credit_before == Money("250.00")
    per_payment = {
        row.id: Money(row.free)
        for row in run_sql(
            url,
            "SELECT p.id, p.amount - coalesce((SELECT sum(pa.amount) FROM"
            " payment_allocations pa WHERE pa.payment_id = p.id AND NOT EXISTS"
            " (SELECT 1 FROM allocation_releases ar WHERE ar.allocation_id ="
            " pa.id)), 0) AS free FROM payments p WHERE p.billing_account_id = :a",
            {"a": world.account},
        )
    }
    assert per_payment == {
        world.newer_payment: Money("200.00"),
        world.older_payment: Money("50.00"),
    }
    [reinserted] = run_sql(
        url,
        "SELECT payment_id, charge_id, amount, created_by_user_id FROM"
        " payment_allocations WHERE id = :id",
        {"id": posted.reinserted_allocation_id},
    )
    assert (
        reinserted.payment_id,
        reinserted.charge_id,
        reinserted.created_by_user_id,
    ) == (
        world.older_payment,
        world.charge,
        None,
    )
    releases = run_sql(
        url,
        "SELECT allocation_id, charge_adjustment_id, payment_reversal_id FROM"
        " allocation_releases WHERE charge_adjustment_id = :a",
        {"a": posted.adjustment_id},
    )
    assert {r.allocation_id for r in releases} == {
        world.newer_allocation,
        world.older_allocation,
    }
    assert all(r.payment_reversal_id is None for r in releases)


def test_a_smaller_credit_releases_only_the_newest_allocation(module_seeded_url):
    url = module_seeded_url
    world = _allocated_world(url)

    posted = _adjust(url, world, "-100.00")

    assert posted.released_allocation_ids == (world.newer_allocation,)
    assert _valid_allocations(url, world) == [
        (world.older_payment, Money("300.00")),
        (world.newer_payment, Money("100.00")),
    ]
    balance = _balance(url, world)
    assert (balance.net, balance.allocated) == (Money("400.00"), Money("400.00"))


def test_a_credit_releasing_exactly_one_allocation_reinserts_nothing(
    module_seeded_url,
):
    url = module_seeded_url
    world = _allocated_world(url)

    posted = _adjust(url, world, "-200.00")

    assert posted.released_allocation_ids == (world.newer_allocation,)
    assert posted.reinserted_allocation_id is None
    assert _valid_allocations(url, world) == [(world.older_payment, Money("300.00"))]


def test_a_credit_that_leaves_net_above_allocations_releases_nothing(
    module_seeded_url,
):
    url = module_seeded_url
    world = _world(url)
    _pay(url, world, "300.00", "300.00")

    posted = _adjust(url, world, "-200.00")

    assert posted.released_allocation_ids == ()
    assert posted.reinserted_allocation_id is None
    assert _count(url, "payment_allocations", world) == 1
    balance = _balance(url, world)
    assert (balance.net, balance.allocated, balance.status) == (
        Money("300.00"),
        Money("300.00"),
        "paid",
    )


def test_a_positive_adjustment_never_releases(module_seeded_url):
    url = module_seeded_url
    world = _allocated_world(url)

    posted = _adjust(url, world, "50.00", kind="duration_change")

    assert posted.released_allocation_ids == ()
    assert len(_valid_allocations(url, world)) == 2
    assert _balance(url, world).status == "partially_paid"


async def _hold_account(
    url: str, account_id: uuid.UUID, held: asyncio.Event, released: list[float]
) -> None:
    engine = short_lived_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT id FROM billing_accounts WHERE id = :id FOR UPDATE"),
                {"id": account_id},
            )
            held.set()
            await asyncio.sleep(HOLD_SECONDS)
            released.append(monotonic_clock.monotonic())
            await conn.commit()
    finally:
        held.set()
        await engine.dispose()


async def _after_hold(url: str, world: World) -> tuple[float, float]:
    held = asyncio.Event()
    released: list[float] = []
    engine = short_lived_engine(url)
    draft = AdjustmentDraft(
        charge_id=world.charge,
        kind="cancellation_credit",
        amount=Money("-10.00"),
        source_key=f"test:{uuid.uuid4()}",
    )

    async def run_b() -> float:
        await held.wait()
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await billing.post_adjustment(uow.session, draft)
        return monotonic_clock.monotonic()

    try:
        _, finished = await asyncio.gather(
            _hold_account(url, world.account, held, released), run_b()
        )
    finally:
        await engine.dispose()
    return released[0], finished


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_post_adjustment_waits_on_the_account_row(module_seeded_url, attempt):
    url = module_seeded_url
    world = _world(url)

    released, finished = asyncio.run(_after_hold(url, world))

    assert finished >= released
    assert _balance(url, world).net == Money("490.00")
