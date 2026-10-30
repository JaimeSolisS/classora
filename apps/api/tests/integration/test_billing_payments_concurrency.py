"""Payments under concurrency on one account (#96 F4, B5; I2, I3; the
#90/#94 lock-test pattern): transaction A runs its step, which takes the
billing account ``FOR UPDATE``, and holds it; B starts once A holds the
lock, waits for A's commit, then decides from what A committed.

- two oldest-first payments of 500 against one open charge of 500: A
  allocates it, B is all account credit;
- an explicit payment line of 300 vs an explicit credit line of 300 on a
  charge with 500 open: the second is ``409 allocation_exceeds_open`` and
  writes nothing;
- two credit allocations of 200 racing for 300 of credit: the second is
  ``409 insufficient_account_credit``;
- a payment allocation vs a negative adjustment on the same charge, both
  orders: I2 holds and the freed money is account credit;
- an account lock doesn't block a payment on another account; two payments
  on different accounts serialize only on the receipt counter (#98 B6),
  without a deadlock, and get consecutive numbers.

I2 and I3 are asserted over the account after every case. Each case runs
5 times."""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.schemas import (
    AdjustmentDraft,
    AllocationLine,
    AllocationRequest,
    CreditAllocationRequest,
    PaymentDraft,
)
from tests.factories import billing as factories
from tests.integration import payment_support as ps
from tests.integration.conftest import short_lived_engine
from tests.integration.lifecycle_support import in_uow

HOLD_SECONDS = 0.5
REPEATS = 5

Step = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture(scope="module")
def actor(module_seeded_url: str) -> uuid.UUID:
    return in_uow(module_seeded_url, factories.staff_user_id)


async def _hold_then(
    url: str, actor: uuid.UUID, first: Step, second: Step
) -> tuple[Any, Any, float, float]:
    """A runs ``first`` and holds its transaction for :data:`HOLD_SECONDS`;
    B runs ``second`` once A has run its step. Returns both results (an
    ``AppError`` as its code) and when each transaction ended."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    holding = asyncio.Event()
    ended: dict[str, float] = {}

    async def run_a() -> Any:
        try:
            async with UnitOfWork(create_sessionmaker(engine_a), actor) as uow:
                result = await first(uow.session)
                holding.set()
                await asyncio.sleep(HOLD_SECONDS)
            ended["a"] = time.monotonic()
            return result
        finally:
            holding.set()

    async def run_b() -> Any:
        await holding.wait()
        try:
            async with UnitOfWork(create_sessionmaker(engine_b), actor) as uow:
                result = await second(uow.session)
        except AppError as error:
            result = error.code
        ended["b"] = time.monotonic()
        return result

    try:
        a, b = await asyncio.gather(run_a(), run_b())
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
    return a, b, ended["a"], ended["b"]


def _account(url: str, fam: uuid.UUID) -> uuid.UUID:
    return in_uow(url, lambda s: billing.ensure_billing_account(s, fam))


def _payment(account: uuid.UUID, amount: str, request: AllocationRequest) -> Step:
    async def _run(session: AsyncSession) -> Any:
        return await billing.record_payment(
            session,
            PaymentDraft(
                billing_account_id=account,
                amount=Money(amount),
                received_on=ps.TODAY,
                payment_method_code="cash",
                allocation=request,
            ),
        )

    return _run


def _credit(account: uuid.UUID, request: AllocationRequest) -> Step:
    async def _run(session: AsyncSession) -> Any:
        return await billing.allocate_account_credit(
            session,
            CreditAllocationRequest(billing_account_id=account, allocation=request),
        )

    return _run


def _adjust(charge: uuid.UUID, amount: str) -> Step:
    async def _run(session: AsyncSession) -> Any:
        return await billing.post_adjustment(
            session,
            AdjustmentDraft(
                charge_id=charge,
                kind="cancellation_credit",
                amount=Money(amount),
                source_key=f"test:{uuid.uuid4()}",
            ),
        )

    return _run


def explicit(charge: uuid.UUID, amount: str) -> AllocationRequest:
    return AllocationRequest(
        mode="explicit", lines=(AllocationLine(charge_id=charge, amount=Money(amount)),)
    )


OLDEST = AllocationRequest(mode="oldest_first")
NONE = AllocationRequest(mode="none")


def _setup(url: str, *amounts: str) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    fam, [s1] = ps.family(url)
    charges = ps.charges(url, *((s1, amount, ps.TODAY) for amount in amounts))
    return fam, _account(url, fam), charges


def _seed_credit(url: str, actor: uuid.UUID, account: uuid.UUID, amount: str) -> None:
    in_uow(url, _payment(account, amount, NONE), actor_id=actor)


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_oldest_first_payments_for_one_charge(module_seeded_url, actor, attempt):
    url = module_seeded_url
    fam, account, [charge] = _setup(url, "500.00")

    a, b, a_end, b_end = asyncio.run(
        _hold_then(
            url,
            actor,
            _payment(account, "500.00", OLDEST),
            _payment(account, "500.00", OLDEST),
        )
    )

    assert [(x.charge_id, x.amount) for x in a.allocations] == [
        (charge, Money("500.00"))
    ]
    assert (b.allocations, b.unallocated) == ((), Money("500.00"))
    assert b.account_credit_after == Money("500.00")
    assert b_end > a_end  # B waited for A's commit
    assert ps.valid_allocated(url, charge) == Money("500.00")
    ps.assert_invariants(url, account)


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_payment_line_and_a_credit_line_compete_for_one_charge(
    module_seeded_url, actor, attempt
):
    url = module_seeded_url
    fam, account, [charge] = _setup(url, "500.00")
    _seed_credit(url, actor, account, "300.00")
    before = len(ps.allocations_of(url, account))

    a, b, _, _ = asyncio.run(
        _hold_then(
            url,
            actor,
            _payment(account, "300.00", explicit(charge, "300.00")),
            _credit(account, explicit(charge, "300.00")),
        )
    )

    assert len(a.allocations) == 1
    assert b == "allocation_exceeds_open"
    assert len(ps.allocations_of(url, account)) == before + 1
    assert ps.valid_allocated(url, charge) == Money("300.00")
    ps.assert_invariants(url, account)


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_credit_allocations_race_for_the_same_credit(
    module_seeded_url, actor, attempt
):
    url = module_seeded_url
    fam, account, [c1, c2] = _setup(url, "250.00", "250.00")
    _seed_credit(url, actor, account, "300.00")

    a, b, _, _ = asyncio.run(
        _hold_then(
            url,
            actor,
            _credit(account, explicit(c1, "200.00")),
            _credit(account, explicit(c2, "200.00")),
        )
    )

    assert a.allocated_total == Money("200.00")
    assert b == "insufficient_account_credit"
    assert ps.valid_allocated(url, c2) == Money.zero()
    assert ps.account_credit(url, account) == Money("100.00")
    ps.assert_invariants(url, account)


@pytest.mark.parametrize("attempt", range(REPEATS))
@pytest.mark.parametrize("payment_first", [True, False])
def test_a_payment_and_a_credit_adjustment_on_one_charge(
    module_seeded_url, actor, attempt, payment_first
):
    url = module_seeded_url
    fam, account, [charge] = _setup(url, "500.00")
    pay = _payment(account, "500.00", OLDEST)
    adjust = _adjust(charge, "-200.00")

    asyncio.run(
        _hold_then(url, actor, *((pay, adjust) if payment_first else (adjust, pay)))
    )

    assert ps.valid_allocated(url, charge) == Money("300.00")
    assert ps.account_credit(url, account) == Money("200.00")
    ps.assert_invariants(url, account)


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_an_account_lock_doesnt_block_another_accounts_payment(
    module_seeded_url, actor, attempt
):
    url = module_seeded_url
    _, account_x, [cx] = _setup(url, "100.00")
    _, account_y, [cy] = _setup(url, "100.00")

    async def lock_x(session: AsyncSession) -> None:
        await billing.lock_billing_account(session, account_x)

    a, b, a_end, b_end = asyncio.run(
        _hold_then(url, actor, lock_x, _payment(account_y, "100.00", OLDEST))
    )

    assert b_end < a_end  # B committed while A still held account X
    assert ps.valid_allocated(url, cy) == Money("100.00")
    assert ps.valid_allocated(url, cx) == Money("0.00")


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_payments_on_two_accounts_serialize_only_on_the_receipt_counter(
    module_seeded_url, actor, attempt
):
    # #98 B6: a payment holds the receipt number_series row (the last lock)
    # from its receipt to its commit, so a payment on another account waits
    # for that commit (gap-free numbering, E13), then gets the next number.
    url = module_seeded_url
    _, account_x, [cx] = _setup(url, "100.00")
    _, account_y, [cy] = _setup(url, "100.00")

    a, b, a_end, b_end = asyncio.run(
        _hold_then(
            url,
            actor,
            _payment(account_x, "100.00", OLDEST),
            _payment(account_y, "100.00", OLDEST),
        )
    )

    assert a_end <= b_end
    assert ps.valid_allocated(url, cx) == ps.valid_allocated(url, cy) == Money("100.00")
    first, second = (int(r.receipt_number[-6:]) for r in (a, b))
    assert second == first + 1
    assert a.receipt_number[:7] == b.receipt_number[:7]
