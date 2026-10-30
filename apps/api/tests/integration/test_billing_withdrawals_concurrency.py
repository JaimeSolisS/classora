"""Withdrawals under concurrency (#101 F6; the #96/#100 lock-test pattern):
transaction A runs its step and holds its locks; B starts once A holds
them, waits for A's commit, then decides from what A committed.

- two confirms of one enrollment (different keys): one wins, the other is
  ``withdrawal_exists``;
- a confirm vs the plain M4 withdrawal, both orders: never two
  withdrawals; the loser is ``enrollment_not_active`` or a stale preview;
- a confirm vs the #92 sweeper step for the period: no fee is posted after
  the settlement;
- a package confirm vs a closeout consumption and vs a #100 cancellation:
  I8 holds and exactly one wins;
- a refund confirm vs a #96 credit allocation drawing on the freed money:
  never a partial write, I3 holds;
- withdrawals on two accounts don't wait for each other.

Each case runs 5 times."""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.models import StaleVersionError
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.billing.schemas import AllocationRequest, CreditAllocationRequest
from app.modules.identity.permissions import OWNER
from app.modules.scheduling import service as scheduling
from app.workflows import (
    cohort_enrollment,
    enrollment_charges,
    tuition_charges,
    withdrawals,
)
from app.workflows.cohort_schemas import CohortEnrollmentCreate, CohortWithdrawal
from app.workflows.withdrawal_schemas import (
    WithdrawalCreditConfirm,
    WithdrawalRefundConfirm,
)
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import corrections_support as cs
from tests.integration import enrollment_charge_support as ec
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration import withdrawal_support as ws
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import post
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.lifecycle_support import in_uow
from tests.integration.series_support import Timeline, owner_cookie, timeline
from tests.integration.tuition_support import PLANS

HOLD_SECONDS = 0.5
REPEATS = 5
TL: Timeline = timeline(2077)
SOLD = TL.now.date()

Step = Callable[[UnitOfWork], Awaitable[Any]]


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(TL.now) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,))


async def _hold_then(
    url: str, actor: uuid.UUID, first: Step, second: Step
) -> tuple[Any, Any, float, float]:
    """A runs ``first`` and holds its transaction for :data:`HOLD_SECONDS`;
    B runs ``second`` once A has run its step (an ``AppError`` as its
    code)."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    holding = asyncio.Event()
    ended: dict[str, float] = {}

    async def run_a() -> Any:
        try:
            try:
                async with UnitOfWork(create_sessionmaker(engine_a), actor) as uow:
                    result = await first(uow)
                    holding.set()
                    await asyncio.sleep(HOLD_SECONDS)
            except AppError as error:
                result = error.code
            ended["a"] = time.monotonic()
            return result
        finally:
            holding.set()

    async def run_b() -> Any:
        await holding.wait()
        try:
            async with UnitOfWork(create_sessionmaker(engine_b), actor) as uow:
                result = await second(uow)
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


def _principal(user: CreatedUser) -> Any:
    return pks._principal(user.id, OWNER)


def _preview(url: str, kind: str, subject: Any) -> dict[str, Any]:
    found = in_uow(
        url,
        lambda s: withdrawals.preview_withdrawal(
            s,
            kind,
            uuid.UUID(str(subject)),
            now=TL.now,  # type: ignore[arg-type]
        ),
    )
    return found.model_dump(mode="json")


def _confirm(user: CreatedUser, found: dict[str, Any], *, refund: bool = False) -> Step:
    payload = ws.body(found, refund_on=SOLD if refund else None)

    async def _step(uow: UnitOfWork) -> Any:
        if refund:
            body: Any = WithdrawalRefundConfirm.model_validate(payload)
        else:
            body = WithdrawalCreditConfirm.model_validate(payload)
        await withdrawals.confirm_withdrawal(
            uow,
            _principal(user),
            body,
            outcome="refund" if refund else "account_credit",
        )
        return "confirmed"

    return _step


def _m4_withdraw(world: ws.World, user: CreatedUser) -> Step:
    async def _step(uow: UnitOfWork) -> Any:
        try:
            await cohort_enrollment.withdraw_from_cohort(
                uow,
                uuid.UUID(world.owner_id),
                uuid.UUID(world.enrollment_id),
                CohortWithdrawal(version=1, reason="Baja"),
                actor_id=user.id,
            )
        except StaleVersionError:
            return "version_conflict"  # the route answers 409 version_conflict
        return "withdrawn"

    return _step


def _withdrawals(url: str, **where: Any) -> int:
    return len(ws.withdrawal_rows(url, **where))


# --- Enrollments ---------------------------------------------------------------------


@pytest.mark.parametrize("repeat", range(REPEATS))
def test_two_confirms_of_one_enrollment(
    module_seeded_url: str, client: TestClient, owner: CreatedUser, repeat: int
) -> None:
    url = module_seeded_url
    world = ws.cohort_world(url, client, TL)
    found = _preview(url, "cohort_enrollment", world.enrollment_id)

    a, b, _, _ = _run(url, owner.id, _confirm(owner, found), _confirm(owner, found))

    assert a == "confirmed"
    assert b == "withdrawal_exists"
    assert _withdrawals(url, cohort_enrollment_id=world.enrollment_id) == 1
    cs.assert_invariants(url, ws.account_of(url, world.family))


@pytest.mark.parametrize("confirm_first", [True, False])
@pytest.mark.parametrize("repeat", range(3))
def test_a_confirm_vs_the_plain_m4_withdrawal(
    module_seeded_url: str,
    client: TestClient,
    owner: CreatedUser,
    confirm_first: bool,
    repeat: int,
) -> None:
    url = module_seeded_url
    world = ws.cohort_world(url, client, TL)
    found = _preview(url, "cohort_enrollment", world.enrollment_id)
    confirm, m4 = _confirm(owner, found), _m4_withdraw(world, owner)

    if confirm_first:
        a, b, _, _ = _run(url, owner.id, confirm, m4)
        assert a == "confirmed"
        assert b in {"version_conflict", "enrollment_not_active"}
    else:
        a, b, _, _ = _run(url, owner.id, m4, confirm)
        assert (a, b) == ("withdrawn", "withdrawal_preview_stale")
    assert _withdrawals(url, cohort_enrollment_id=world.enrollment_id) == (
        1 if confirm_first else 0
    )


@pytest.mark.parametrize("repeat", range(REPEATS))
def test_a_settlement_vs_the_enrollment_sweeper(
    module_seeded_url: str, client: TestClient, owner: CreatedUser, repeat: int
) -> None:
    url = module_seeded_url
    world = ws.cohort_world(url, client, TL, fee=None)
    ec.cohort_fee(url, world.subject, "1000.00", cohort_id=uuid.UUID(world.owner_id))
    found = _preview(url, "cohort_enrollment", world.enrollment_id)
    assert found["lines"][0]["posting"] == "now"

    async def _sweep(uow: UnitOfWork) -> Any:
        return await enrollment_charges.post_one(
            uow.session, "cohort_enrollment", uuid.UUID(world.enrollment_id)
        )

    a, b, _, _ = _run(url, owner.id, _confirm(owner, found), _sweep)

    assert a == "confirmed"
    assert b == "already_posted"
    [fee] = ec.fees(url, cohort_enrollment=world.enrollment_id)
    assert ec.net_of(url, fee.id) == "0.00"  # posted now and credited whole


# --- Packages ------------------------------------------------------------------------


def _sold(url: str, owner: CreatedUser, *, paid: bool = False) -> tuple[Any, Any, Any]:
    w = pks.world(url)
    package = pks.sell(url, w.student, pks.product(url), SOLD)
    [purchase] = run_sql(
        url, "SELECT * FROM charges WHERE package_id = :p", {"p": package}
    )
    if paid:
        cs.recorded_payment(
            url,
            owner.id,
            purchase.billing_account_id,
            "1000.00",
            [(purchase.id, "1000.00")],
        )
    return w, package, purchase


def _consume(w: pks.World, class_session: uuid.UUID) -> Step:
    async def _step(uow: UnitOfWork) -> Any:
        locked = await scheduling.lock_session_for_billing(uow.session, class_session)
        assert locked is not None
        result = await billing.consume_session_credit(
            uow.session,
            billing.SessionCreditClaim(
                class_session_id=locked.id,
                student_id=w.student,
                subject_id=locked.subject_id,
                service_date=locked.starts_at.astimezone(ps.ZONE).date(),
                scheduled_minutes=locked.scheduled_minutes,
            ),
        )
        return result.outcome

    return _step


def _cancel(package: uuid.UUID) -> Step:
    async def _step(uow: UnitOfWork) -> Any:
        await billing.cancel_package(
            uow.session, package, reason="Error", credit_amount=None, today=SOLD
        )
        return "cancelled"

    return _step


@pytest.mark.parametrize("withdraw_first", [True, False])
@pytest.mark.parametrize("repeat", range(3))
def test_a_package_confirm_vs_a_consumption(
    module_seeded_url: str,
    client: TestClient,
    owner: CreatedUser,
    withdraw_first: bool,
    repeat: int,
) -> None:
    url = module_seeded_url
    pks.reset(url)
    w, package, purchase = _sold(url, owner)
    held = pks.session_for(url, w, SOLD + timedelta(days=3))
    found = _preview(url, "package", package)
    confirm, consume = _confirm(owner, found), _consume(w, held)

    if withdraw_first:
        a, b, _, _ = _run(url, owner.id, confirm, consume)
        assert (a, b) == ("confirmed", "no_package")
    else:
        a, b, _, _ = _run(url, owner.id, consume, confirm)
        assert (a, b) == ("consumed", "withdrawal_preview_stale")
    assert pks.remaining(url, package) == (0 if withdraw_first else 9)
    cs.assert_invariants(url, purchase.billing_account_id)


@pytest.mark.parametrize("withdraw_first", [True, False])
@pytest.mark.parametrize("repeat", range(3))
def test_a_package_confirm_vs_a_cancellation(
    module_seeded_url: str,
    client: TestClient,
    owner: CreatedUser,
    withdraw_first: bool,
    repeat: int,
) -> None:
    url = module_seeded_url
    pks.reset(url)
    _, package, purchase = _sold(url, owner)
    found = _preview(url, "package", package)
    confirm, cancel = _confirm(owner, found), _cancel(package)

    if withdraw_first:
        a, b, _, _ = _run(url, owner.id, confirm, cancel)
        assert (a, b) == ("confirmed", "package_not_cancellable")
    else:
        a, b, _, _ = _run(url, owner.id, cancel, confirm)
        assert (a, b) == ("cancelled", "package_not_withdrawable")
    assert pks.remaining(url, package) == 0
    kinds = [t.kind for t in pks.transactions(url, package)]
    assert ("refund" in kinds) != ("reversal" in kinds)
    cs.assert_invariants(url, purchase.billing_account_id)


@pytest.mark.parametrize("repeat", range(REPEATS))
def test_a_refund_vs_a_credit_allocation_of_the_freed_money(
    module_seeded_url: str, client: TestClient, owner: CreatedUser, repeat: int
) -> None:
    url = module_seeded_url
    pks.reset(url)
    w, package, purchase = _sold(url, owner, paid=True)
    account = purchase.billing_account_id
    [other] = ps.charges(url, (w.student, "400.00", SOLD), service=SOLD)
    found = _preview(url, "package", package)

    async def _allocate(uow: UnitOfWork) -> Any:
        await billing.allocate_account_credit(
            uow.session,
            CreditAllocationRequest(
                billing_account_id=account,
                allocation=AllocationRequest(mode="oldest_first"),
            ),
        )
        return "allocated"

    a, b, _, _ = _run(url, owner.id, _confirm(owner, found, refund=True), _allocate)

    assert a == "confirmed"
    assert b == "nothing_to_allocate"
    [row] = ws.withdrawal_rows(url, package_id=package)
    refunded = run_sql(
        url,
        "SELECT sum(amount) AS s FROM refunds WHERE withdrawal_id = :w",
        {"w": row.id},
    )[0].s
    assert f"{refunded:.2f}" == f"{row.refund_amount:.2f}" == "1000.00"
    assert other is not None
    cs.assert_invariants(url, account)


@pytest.mark.parametrize("repeat", range(REPEATS))
def test_two_accounts_dont_wait_for_each_other(
    module_seeded_url: str, client: TestClient, owner: CreatedUser, repeat: int
) -> None:
    url = module_seeded_url
    pks.reset(url)
    _, first, _ = _sold(url, owner)
    _, second, _ = _sold(url, owner)
    found_first = _preview(url, "package", first)
    found_second = _preview(url, "package", second)

    a, b, ended_a, ended_b = _run(
        url, owner.id, _confirm(owner, found_first), _confirm(owner, found_second)
    )

    assert (a, b) == ("confirmed", "confirmed")
    assert ended_b < ended_a


@pytest.mark.parametrize("repeat", range(3))
def test_a_confirm_vs_a_re_enrollment_of_the_student(
    module_seeded_url: str, client: TestClient, owner: CreatedUser, repeat: int
) -> None:
    url = module_seeded_url
    world = ws.cohort_world(url, client, TL)
    found = _preview(url, "cohort_enrollment", world.enrollment_id)

    async def _enroll(uow: UnitOfWork) -> Any:
        change = await cohort_enrollment.enroll_in_cohort(
            uow,
            uuid.UUID(world.owner_id),
            CohortEnrollmentCreate(student_id=world.student),
            actor_id=owner.id,
        )
        return change.enrollment_id

    a, b, _, _ = _run(url, owner.id, _confirm(owner, found), _enroll)

    assert a == "confirmed"
    assert isinstance(b, uuid.UUID)  # enrolled again once A committed
    [first] = ec.fees(url, cohort_enrollment=world.enrollment_id)
    assert ec.net_of(url, first.id) == "0.00"  # nothing held: credited whole
    [again] = ec.fees(url, cohort_enrollment=b)  # J5: charged again
    assert ec.net_of(url, again.id) == "3000.00"
    cs.assert_invariants(url, ws.account_of(url, world.family))


@pytest.mark.parametrize("repeat", range(3))
def test_a_settlement_vs_the_tuition_job(
    module_seeded_url: str, client: TestClient, owner: CreatedUser, repeat: int
) -> None:
    url = module_seeded_url
    world = ws.group_world(url, client, TL, fee=None, count=24)
    item = ec.price(
        url,
        world.subject,
        "200.00",
        class_type="group",
        pricing_model="per_period",
        period_frequency="weekly",
    )
    ec.set_floor(url, None)
    linked = post(
        client,
        owner_cookie(url, client),
        PLANS,
        {
            "student_id": str(world.student),
            "service_price_id": str(item),
            "group_enrollment_id": world.enrollment_id,
            "starts_on": str(TL.monday),
        },
    )
    assert linked.status_code == 201, linked.text
    plan_id = uuid.UUID(linked.json()["id"])
    ec.set_floor(url, ec.floor_of(TL))
    with frozen_clock(TL.at(TL.week(1, 2), "12:00")) as later:
        found = in_uow(
            url,
            lambda s: withdrawals.preview_withdrawal(
                s,
                "group_enrollment",
                uuid.UUID(world.enrollment_id),
                now=later.now(),
            ),
        ).model_dump(mode="json")
        [line] = found["lines"]
        assert (line["posting"], line["computed_credit"]) == ("now", "100.00")

        async def _job(uow: UnitOfWork) -> Any:
            done = await tuition_charges.post_plan(
                uow.session,
                plan_id,
                zone=ws.ZONE_NAME,
                today=TL.week(1, 2),
                first=ec.floor_of(TL),
            )
            return dict(done)

        a, b, _, _ = _run(url, owner.id, _confirm(owner, found), _job)

    assert a == "confirmed"
    assert "posted" not in b
    [charge] = run_sql(
        url, "SELECT id FROM charges WHERE tuition_plan_id = :p", {"p": plan_id}
    )
    assert ec.net_of(url, charge.id) == "100.00"
