"""Corrections under concurrency on one account (#100 F7; B6; I1, I2, I3,
I8; the #96 lock-test pattern): transaction A runs its step, which takes
the billing account ``FOR UPDATE``, and holds it; B starts once A holds the
lock, waits for A's commit, then decides from what A committed.

- two reversals of one payment: the second is ``payment_already_reversed``;
- a reversal vs a refund of one payment, both orders: the second is
  ``payment_reversed`` / ``payment_has_refunds``;
- two refunds racing for one remainder: the second is
  ``refund_exceeds_remainder``;
- a reversal vs a #96 credit allocation drawing on that payment, both
  orders: never a valid allocation on a reversed payment;
- a manual credit vs a payment allocation on one charge, both orders: I1
  and I2 hold and the freed money is account credit;
- a package cancellation vs a session consumption of that package, both
  orders: I8 holds, never a consumption after the reversal;
- corrections on two accounts don't wait for each other.

The #100 invariants are asserted over the account after every case. Each
case runs 5 times."""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.schemas import (
    AllocationLine,
    AllocationRequest,
    CreditAllocationRequest,
    PaymentDraft,
    RefundDraft,
)
from app.modules.scheduling import service as scheduling
from tests.factories import billing as factories
from tests.integration import corrections_support as cs
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import in_uow

HOLD_SECONDS = 0.5
REPEATS = 5
SOLD = date(2031, 3, 1)

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


def _run(url: str, actor: uuid.UUID, first: Step, second: Step) -> tuple[Any, ...]:
    return asyncio.run(_hold_then(url, actor, first, second))


# --- Steps ----------------------------------------------------------------------------


def _reverse(payment: uuid.UUID) -> Step:
    async def _step(session: AsyncSession) -> Any:
        return await billing.reverse_payment(session, payment, "Rebotó")

    return _step


def _refund(payment: uuid.UUID, amount: str) -> Step:
    async def _step(session: AsyncSession) -> Any:
        return await billing.refund_payment(
            session,
            RefundDraft(
                payment_id=payment,
                amount=Money(amount),
                refunded_on=ps.TODAY,
                payment_method_code="cash",
                reason="Devolución",
            ),
        )

    return _step


def _credit(account: uuid.UUID) -> Step:
    async def _step(session: AsyncSession) -> Any:
        return await billing.allocate_account_credit(
            session,
            CreditAllocationRequest(
                billing_account_id=account,
                allocation=AllocationRequest(mode="oldest_first"),
            ),
        )

    return _step


def _pay_explicit(account: uuid.UUID, charge: uuid.UUID, amount: str) -> Step:
    async def _step(session: AsyncSession) -> Any:
        return await billing.record_payment(
            session,
            PaymentDraft(
                billing_account_id=account,
                amount=Money(amount),
                received_on=ps.TODAY,
                payment_method_code="cash",
                allocation=AllocationRequest(
                    mode="explicit",
                    lines=(AllocationLine(charge_id=charge, amount=Money(amount)),),
                ),
            ),
        )

    return _step


def _manual_credit(charge: uuid.UUID, amount: str) -> Step:
    async def _step(session: AsyncSession) -> Any:
        return await billing.post_manual_adjustment(
            session, charge, Money(amount), "Error de captura"
        )

    return _step


def _cancel(package: uuid.UUID) -> Step:
    async def _step(session: AsyncSession) -> Any:
        return await billing.cancel_package(
            session, package, reason="Se mudó", credit_amount=None, today=ps.TODAY
        )

    return _step


def _consume(class_session: uuid.UUID, student: uuid.UUID) -> Step:
    async def _step(session: AsyncSession) -> Any:
        locked = await scheduling.lock_session_for_billing(session, class_session)
        assert locked is not None
        return await billing.consume_session_credit(
            session,
            billing.SessionCreditClaim(
                class_session_id=locked.id,
                student_id=student,
                subject_id=locked.subject_id,
                service_date=locked.starts_at.astimezone(pks.ZONE).date(),
                scheduled_minutes=locked.scheduled_minutes,
            ),
        )

    return _step


def _payment(url: str, actor: uuid.UUID, amount: str, lines: Any = None) -> Any:
    fam, account, students = cs.new_account(url)
    payment = cs.recorded_payment(url, actor, account, amount, lines)
    return fam, account, students, payment


def _valid_on(url: str, payment: uuid.UUID) -> int:
    return len(
        [
            a
            for a in run_sql(
                url,
                "SELECT pa.id FROM payment_allocations pa WHERE pa.payment_id = :p"
                " AND NOT EXISTS (SELECT 1 FROM allocation_releases r"
                " WHERE r.allocation_id = pa.id)",
                {"p": payment},
            )
        ]
    )


# --- Cases ----------------------------------------------------------------------------


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_reversals_of_one_payment(module_seeded_url, actor, attempt):
    url = module_seeded_url
    _, account, _, payment = _payment(url, actor, "100.00")

    a, b, a_end, b_end = _run(url, actor, _reverse(payment), _reverse(payment))

    assert isinstance(a, billing.PaymentReversalResult)
    assert b == "payment_already_reversed"
    assert b_end > a_end
    cs.assert_invariants(url, account)


@pytest.mark.parametrize("attempt", range(REPEATS))
@pytest.mark.parametrize("first", ["reversal", "refund"])
def test_a_reversal_and_a_refund_of_one_payment(
    module_seeded_url, actor, attempt, first
):
    url = module_seeded_url
    _, account, _, payment = _payment(url, actor, "100.00")
    steps = {"reversal": _reverse(payment), "refund": _refund(payment, "30.00")}
    second = "refund" if first == "reversal" else "reversal"

    _, b, _, _ = _run(url, actor, steps[first], steps[second])

    assert b == ("payment_reversed" if first == "reversal" else "payment_has_refunds")
    cs.assert_invariants(url, account)


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_refunds_race_for_one_remainder(module_seeded_url, actor, attempt):
    url = module_seeded_url
    _, account, _, payment = _payment(url, actor, "100.00")

    a, b, _, _ = _run(url, actor, _refund(payment, "60.00"), _refund(payment, "60.00"))

    assert a.remainder_after == Money("40.00")
    assert b == "refund_exceeds_remainder"
    assert (
        len(
            run_sql(url, "SELECT id FROM refunds WHERE payment_id = :p", {"p": payment})
        )
        == 1
    )
    cs.assert_invariants(url, account)


@pytest.mark.parametrize("attempt", range(REPEATS))
@pytest.mark.parametrize("first", ["reversal", "credit"])
def test_a_reversal_and_a_credit_allocation(module_seeded_url, actor, attempt, first):
    url = module_seeded_url
    _, account, [student], payment = _payment(url, actor, "100.00")
    ps.charges(url, (student, "80.00", ps.TODAY))
    steps = {"reversal": _reverse(payment), "credit": _credit(account)}
    second = "credit" if first == "reversal" else "reversal"

    a, b, _, _ = _run(url, actor, steps[first], steps[second])

    if first == "reversal":
        assert b == "nothing_to_allocate"
    else:
        assert a.allocated_total == Money("80.00")
        assert len(b.released) == 1
    assert _valid_on(url, payment) == 0
    cs.assert_invariants(url, account)


@pytest.mark.parametrize("attempt", range(REPEATS))
@pytest.mark.parametrize("first", ["credit", "payment"])
def test_a_manual_credit_and_a_payment_allocation(
    module_seeded_url, actor, attempt, first
):
    url = module_seeded_url
    _, account, [student] = cs.new_account(url)
    [charge] = ps.charges(url, (student, "500.00", ps.TODAY))
    steps = {
        "credit": _manual_credit(charge, "-200.00"),
        "payment": _pay_explicit(account, charge, "500.00"),
    }
    second = "payment" if first == "credit" else "credit"

    a, b, _, _ = _run(url, actor, steps[first], steps[second])

    if first == "credit":
        assert a.net == Money("300.00")
        assert b == "allocation_exceeds_open"
        assert ps.account_credit(url, account) == Money.zero()
    else:
        assert b.net == Money("300.00")
        assert b.status == "paid"
        assert b.account_credit_after == Money("200.00")
        assert ps.account_credit(url, account) == Money("200.00")
    cs.assert_invariants(url, account)


@pytest.mark.parametrize("attempt", range(REPEATS))
@pytest.mark.parametrize("first", ["cancel", "consume"])
def test_a_package_cancellation_and_a_consumption(
    module_seeded_url, actor, attempt, first
):
    url = module_seeded_url
    pks.reset(url)
    w = pks.world(url)
    package = pks.sell(url, w.student, pks.product(url), SOLD)
    session = pks.session_for(url, w, ps.TODAY)
    steps = {"cancel": _cancel(package), "consume": _consume(session, w.student)}
    second = "consume" if first == "cancel" else "cancel"

    a, b, _, _ = _run(url, actor, steps[first], steps[second])

    if first == "cancel":
        assert a.units_reversed == 10
        assert b.outcome == "no_package"
        assert pks.consumes(url, session) == []
    else:
        assert a.outcome == "consumed"
        assert b.units_reversed == 9
    assert pks.remaining(url, package) == 0
    [account] = run_sql(
        url, "SELECT billing_account_id FROM packages WHERE id = :p", {"p": package}
    )
    cs.assert_invariants(url, account.billing_account_id)


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_accounts_dont_wait_for_each_other(module_seeded_url, actor, attempt):
    url = module_seeded_url
    _, first_account, _, first = _payment(url, actor, "100.00")
    _, second_account, _, second = _payment(url, actor, "100.00")

    a, b, a_end, b_end = _run(url, actor, _reverse(first), _refund(second, "10.00"))

    assert isinstance(a, billing.PaymentReversalResult)
    assert b.remainder_after == Money("90.00")
    assert b_end < a_end  # B never waited for A's lock
    cs.assert_invariants(url, first_account)
    cs.assert_invariants(url, second_account)
