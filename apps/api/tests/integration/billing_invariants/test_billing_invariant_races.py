"""Cross-operation races (#103 E2): two different operations on the same
rows at once, each case 5 times on a fresh world copy, with real
concurrent connections (HTTP requests released together on a barrier
through the real ASGI app, or Units of Work on separate engines for the
jobs and the closeout hook). Each case asserts the merged winner rule, then
the whole catalogue.

The winner codes come from the merged #96, #100 and #101 code (api.md):
``refund_exceeds_remainder``, ``payment_has_refunds`` / ``payment_reversed``,
``insufficient_account_credit``, ``allocation_exceeds_open``,
``package_not_cancellable`` / ``package_not_withdrawable``."""

import asyncio
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import schemas as bschemas
from app.workflows import (
    billing_corrections,
    package_expiry,
    schedule_reconciliation,
    session_charges,
)
from app.workflows.closeout_approval import on_closeout_approved
from tests.conftest import frozen_clock
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.auth_support import auth_client
from tests.integration.billing_invariants import harness
from tests.integration.billing_invariants import world as worlds
from tests.integration.billing_invariants.routes import (
    Builder,
    Staff,
    account_path,
    post_json,
)
from tests.integration.conftest import run_sql, settings_for, short_lived_engine

REPEATS = 5
TODAY = worlds.TODAY


def _code(response: Response) -> str:
    if response.status_code == 201:
        return "201"
    body: dict[str, Any] = response.json()
    return str(body.get("code"))


def http_race(
    client: TestClient, owner: Staff, requests: list[tuple[str, dict[str, Any]]]
) -> list[Response]:
    """The requests released together (each with its own key)."""
    start = threading.Barrier(len(requests))

    def send(path: str, body: dict[str, Any]) -> Response:
        start.wait(10)
        return post_json(client, owner, path, body)

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        futures = [pool.submit(send, path, body) for path, body in requests]
        return [future.result(60) for future in futures]


type Step = Callable[[UnitOfWork], Awaitable[Any]]


def unit_race(url: str, actor: uuid.UUID, steps: list[Step]) -> list[Any]:
    """Each step in its own Unit of Work on its own engine, at once; an
    :class:`AppError` is returned as its code."""

    async def _one(step: Step) -> Any:
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine), actor) as uow:
                return await step(uow)
        except AppError as error:
            return error.code
        finally:
            await engine.dispose()

    async def _all() -> list[Any]:
        return list(await asyncio.gather(*(_one(step) for step in steps)))

    return asyncio.run(_all())


def _job(func: Callable[..., Awaitable[Any]], url: str, **kwargs: Any) -> Step:
    async def step(uow: UnitOfWork) -> Any:
        return await func(settings_for(url), **kwargs)

    return step


def _session(b: Builder, *, subject: str, day_offset: int = 2) -> uuid.UUID:
    day = TODAY + timedelta(days=day_offset)

    async def _make(session: AsyncSession) -> uuid.UUID:
        made = await persist_class_session(
            session,
            subject_id=b.world.subjects[subject],
            kind="individual",
            state="confirmed",
            starts_at=worlds.local(day, 9),
            ends_at=worlds.local(day, 10),
        )
        await add_participant(session, made, b.student)
        return made.id

    made: uuid.UUID = b.run(_make)
    return made


def _approve(session_id: uuid.UUID) -> Step:
    async def step(uow: UnitOfWork) -> Any:
        await on_closeout_approved(
            uow.session,
            approval_id=uuid.uuid7(),
            supersedes_approval_id=None,
            class_session_id=session_id,
        )
        return "approved"

    return step


def _one(url: str, sql: str, **params: Any) -> Any:
    [row] = run_sql(url, sql, params)
    return row


def _refund_body(amount: str) -> dict[str, Any]:
    return {
        "amount": amount,
        "refunded_on": TODAY.isoformat(),
        "payment_method_code": "transfer",
        "reference": "SPEI-2",
        "reason": "Saldo a favor",
    }


# --- The cases -----------------------------------------------------------------------


def two_refunds(b: Builder, client: TestClient, owner: Staff) -> None:
    payment = b.payment("100.00")
    path = account_path(b.family.id, f"/payments/{payment}/refunds")
    codes = sorted(
        _code(r)
        for r in http_race(
            client,
            owner,
            [(path, _refund_body("60.00")), (path, _refund_body("70.00"))],
        )
    )
    assert codes == ["201", "refund_exceeds_remainder"], codes


def reversal_vs_refund(b: Builder, client: TestClient, owner: Staff) -> None:
    payment = b.payment("100.00")
    reversal, refund = (
        _code(r)
        for r in http_race(
            client,
            owner,
            [
                (
                    account_path(b.family.id, f"/payments/{payment}/reversal"),
                    {"reason": "Rechazado"},
                ),
                (
                    account_path(b.family.id, f"/payments/{payment}/refunds"),
                    _refund_body("30.00"),
                ),
            ],
        )
    )
    assert (reversal, refund) in {
        ("201", "payment_reversed"),
        ("payment_has_refunds", "201"),
    }, (reversal, refund)


def two_credit_allocations(b: Builder, client: TestClient, owner: Staff) -> None:
    b.charge("100.00")
    b.charge("100.00")
    b.payment("100.00")
    body = {"allocation": {"mode": "oldest_first"}, "amount": "70.00"}
    path = account_path(b.family.id, "/allocations")
    codes = sorted(
        _code(r) for r in http_race(client, owner, [(path, body), (path, body)])
    )
    assert codes == ["201", "insufficient_account_credit"], codes


def two_oldest_first_payments(b: Builder, client: TestClient, owner: Staff) -> None:
    b.charge("100.00")
    b.charge("50.00")
    body = {
        "amount": "120.00",
        "received_on": TODAY.isoformat(),
        "payment_method_code": "cash",
        "allocation": {"mode": "oldest_first"},
    }
    path = account_path(b.family.id, "/payments")
    codes = [_code(r) for r in http_race(client, owner, [(path, body), (path, body)])]
    assert codes == ["201", "201"], codes
    allocated = _one(
        b.url,
        "SELECT COALESCE(SUM(amount), 0) AS total FROM payment_allocations"
        " WHERE billing_account_id = :a",
        a=b.family.account,
    ).total
    assert Money(allocated) == Money("150.00")


def credit_vs_allocation(b: Builder, client: TestClient, owner: Staff) -> None:
    charge = b.charge("100.00")
    adjustment, payment = (
        _code(r)
        for r in http_race(
            client,
            owner,
            [
                (
                    account_path(b.family.id, f"/charges/{charge}/adjustments"),
                    {"amount": "-80.00", "reason": "Descuento"},
                ),
                (
                    account_path(b.family.id, "/payments"),
                    {
                        "amount": "100.00",
                        "received_on": TODAY.isoformat(),
                        "payment_method_code": "cash",
                        "allocation": {
                            "mode": "explicit",
                            "lines": [{"charge_id": str(charge), "amount": "100.00"}],
                        },
                    },
                ),
            ],
        )
    )
    assert adjustment == "201"
    assert payment in {"201", "allocation_exceeds_open"}, payment
    if payment == "201":
        # The credit came after the allocation: E5 released 80.00 to credit.
        credit = _one(
            b.url,
            "SELECT COALESCE(SUM(p.amount - COALESCE((SELECT SUM(pa.amount) FROM"
            " payment_allocations pa WHERE pa.payment_id = p.id AND NOT EXISTS"
            " (SELECT 1 FROM allocation_releases r WHERE r.allocation_id = pa.id)),"
            " 0)), 0) AS credit FROM payments p WHERE p.billing_account_id = :a",
            a=b.family.account,
        ).credit
        assert Money(credit) == Money("80.00")


def consumption_vs_sweeper(b: Builder, client: TestClient, owner: Staff) -> None:
    b.package()
    session_id = _session(b, subject="PKG")
    unit_race(
        b.url,
        b.world.owner.id,
        [_approve(session_id), _job(session_charges.run_post_session_charges, b.url)],
    )
    row = _one(
        b.url,
        "SELECT (SELECT COUNT(*) FROM charges WHERE kind = 'session' AND"
        " class_session_id = :s) AS charged, (SELECT COUNT(*) FROM"
        " package_credit_transactions WHERE kind = 'consume' AND class_session_id ="
        " :s) AS consumed",
        s=session_id,
    )
    assert row.charged + row.consumed == 1, row


def consumption_inside_the_sweeper_step(
    b: Builder, client: TestClient, owner: Staff
) -> None:
    """The #91 step for a key has classified it (no package yet) and is
    about to price it when a package is bought and the closeout approval
    consumes the key and commits: ``post_charge`` must refuse under the
    account lock (I9's second side, #94 L3), so the step ends
    ``package_covered``."""
    import pytest as _pytest

    from app.modules.billing import service as billing_service
    from app.workflows import packages as package_workflow
    from app.workflows.package_schemas import PackagePurchase

    session_id = _session(b, subject="PKG")
    real_quote = billing_service.quote

    async def _race() -> Any:
        priced, consumed = asyncio.Event(), asyncio.Event()

        async def held_quote(*args: Any, **kwargs: Any) -> Any:
            priced.set()
            await asyncio.wait_for(consumed.wait(), 10)
            return await real_quote(*args, **kwargs)

        monkey = _pytest.MonkeyPatch()
        monkey.setattr(billing_service, "quote", held_quote)
        engines = [short_lived_engine(b.url), short_lived_engine(b.url)]
        try:

            async def step() -> Any:
                async with UnitOfWork(create_sessionmaker(engines[0])) as uow:
                    return await session_charges.post_one(
                        uow.session, session_id, b.student, zone=worlds.ZONE_NAME
                    )

            async def consume() -> None:
                await asyncio.wait_for(priced.wait(), 10)
                try:
                    async with UnitOfWork(
                        create_sessionmaker(engines[1]), b.world.owner.id
                    ) as uow:
                        monkey.setattr(billing_service, "quote", real_quote)
                        await package_workflow.purchase_package(
                            uow.session,
                            PackagePurchase(
                                student_id=b.student, package_product_id=b.world.product
                            ),
                        )
                        await _approve(session_id)(uow)
                finally:
                    monkey.setattr(billing_service, "quote", held_quote)
                    consumed.set()

            outcome, _ = await asyncio.gather(step(), consume())
            return outcome
        finally:
            monkey.undo()
            for engine in engines:
                await engine.dispose()

    outcome = asyncio.run(_race())
    row = _one(
        b.url,
        "SELECT (SELECT COUNT(*) FROM charges WHERE kind = 'session' AND"
        " class_session_id = :s) AS charged, (SELECT COUNT(*) FROM"
        " package_credit_transactions WHERE kind = 'consume' AND class_session_id ="
        " :s) AS consumed",
        s=session_id,
    )
    assert (outcome, row.charged, row.consumed) == ("package_covered", 0, 1), (
        outcome,
        row,
    )


def expiry_vs_consumption(b: Builder, client: TestClient, owner: Staff) -> None:
    package = b.package()
    session_id = _session(b, subject="PKG", day_offset=3)
    later = worlds.local(TODAY + timedelta(days=worlds.PACKAGE_VALIDITY_DAYS + 2), 12)
    unit_race(
        b.url,
        b.world.owner.id,
        [
            _approve(session_id),
            _job(package_expiry.run_expire_packages, b.url, now=later),
        ],
    )
    row = _one(
        b.url,
        "SELECT COALESCE(SUM(units), 0) AS remaining, COUNT(*) FILTER (WHERE kind ="
        " 'expire') AS expired, COUNT(*) FILTER (WHERE kind = 'consume') AS consumed"
        " FROM package_credit_transactions WHERE package_id = :p",
        p=package,
    )
    assert row.remaining == 0 and row.expired == 1 and row.consumed == 1, row


def reconcile_vs_manual_credit(b: Builder, client: TestClient, owner: Staff) -> None:
    session_id = _session(b, subject="IND")
    asyncio.run(session_charges.run_post_session_charges(settings_for(b.url)))
    posted = _one(
        b.url,
        "SELECT id, amount FROM charges WHERE class_session_id = :s",
        s=session_id,
    )
    charge = posted.id
    run_sql(
        b.url,
        "UPDATE class_sessions SET state = 'cancelled', cancelled_at = :at,"
        " cancellation_reason = 'Cierre', version = version + 1 WHERE id = :id",
        {"id": session_id, "at": worlds.NOW},
    )
    body = bschemas.ManualAdjustmentCreate.model_validate(
        {"amount": "-100.00", "reason": "Descuento"}
    )

    async def manual(uow: UnitOfWork) -> Any:
        await billing_corrections.post_manual_adjustment(
            uow.session, b.family.id, charge, body
        )
        return "credited"

    job, adjusted = unit_race(
        b.url,
        b.world.owner.id,
        [_job(schedule_reconciliation.run_reconcile_session_charges, b.url), manual],
    )
    assert job.failed == 0, job
    assert adjusted in {"credited", "charge_net_negative"}, adjusted
    row = _one(
        b.url,
        "SELECT c.amount + COALESCE((SELECT SUM(a.amount) FROM charge_adjustments a"
        " WHERE a.charge_id = c.id), 0) AS net, (SELECT -SUM(a.amount) FROM"
        " charge_adjustments a WHERE a.charge_id = c.id AND a.kind ="
        " 'cancellation_credit') AS cancellation FROM charges c WHERE c.id = :c",
        c=charge,
    )
    assert Money(row.net) == Money.zero(), row
    # The cancellation credit takes whatever net is left (the sibling
    # discount priced the charge).
    amount = Money(posted.amount)
    expected = amount - Money("100.00") if adjusted == "credited" else amount
    assert Money(row.cancellation) == expected, row


def cancel_vs_withdrawal(b: Builder, client: TestClient, owner: Staff) -> None:
    package = b.package()
    withdrawal = b.withdrawal_body(package, refund=False)
    cancel, withdraw = (
        _code(r)
        for r in http_race(
            client,
            owner,
            [
                (
                    f"/api/v1/billing/packages/{package}/cancellation",
                    {"reason": "Baja"},
                ),
                ("/api/v1/billing/withdrawals/account-credit", withdrawal),
            ],
        )
    )
    assert (cancel, withdraw) in {
        ("201", "package_not_withdrawable"),
        ("201", "withdrawal_preview_stale"),
        ("package_not_cancellable", "201"),
    }, (cancel, withdraw)


def withdrawal_refund_vs_refund(b: Builder, client: TestClient, owner: Staff) -> None:
    package = b.package()
    [purchase] = run_sql(
        b.url, "SELECT id, amount FROM charges WHERE package_id = :p", {"p": package}
    )
    total = Money(purchase.amount) + Money("200.00")
    payment = b.payment(str(total.amount), [(purchase.id, str(purchase.amount))])
    withdrawal = b.withdrawal_body(package, refund=True)
    codes = [
        _code(r)
        for r in http_race(
            client,
            owner,
            [
                ("/api/v1/billing/withdrawals/refund", withdrawal),
                (
                    account_path(b.family.id, f"/payments/{payment}/refunds"),
                    _refund_body("200.00"),
                ),
            ],
        )
    ]
    assert "201" in codes, codes
    assert set(codes) <= {"201", "withdrawal_preview_stale", "refund_exceeds_remainder"}


def receipts_stay_contiguous(b: Builder, client: TestClient, owner: Staff) -> None:
    other = b.world.families["F2"]
    body = {
        "amount": "10.00",
        "received_on": TODAY.isoformat(),
        "payment_method_code": "cash",
        "allocation": {"mode": "none"},
    }
    codes = [
        _code(r)
        for r in http_race(
            client,
            owner,
            [
                (account_path(b.family.id, "/payments"), body),
                (account_path(b.family.id, "/payments"), body),
                (account_path(other.id, "/payments"), body),
                (account_path(other.id, "/payments"), body),
            ],
        )
    ]
    assert codes == ["201"] * 4, codes


CASES: dict[str, Callable[[Builder, TestClient, Staff], None]] = {
    "two_refunds": two_refunds,
    "reversal_vs_refund": reversal_vs_refund,
    "two_credit_allocations": two_credit_allocations,
    "two_oldest_first_payments": two_oldest_first_payments,
    "credit_vs_allocation": credit_vs_allocation,
    "consumption_vs_sweeper": consumption_vs_sweeper,
    "consumption_inside_the_sweeper_step": consumption_inside_the_sweeper_step,
    "expiry_vs_consumption": expiry_vs_consumption,
    "reconcile_vs_manual_credit": reconcile_vs_manual_credit,
    "cancel_vs_withdrawal": cancel_vs_withdrawal,
    "withdrawal_refund_vs_refund": withdrawal_refund_vs_refund,
    "receipts_stay_contiguous": receipts_stay_contiguous,
}


@pytest.fixture
def raced(
    world_template: worlds.Template, world_copy: Callable[[], str]
) -> Iterator[tuple[Builder, TestClient, Staff]]:
    url = world_copy()
    with frozen_clock(worlds.NOW.astimezone(UTC)), auth_client(url) as client:
        owner = Staff.signed_in(
            client, world_template.world.owner.username, reauth=True
        )
        yield Builder(url, world_template.world), client, owner


@pytest.mark.parametrize("repeat", range(REPEATS))
@pytest.mark.parametrize("case", sorted(CASES))
def test_billing_invariant_race(
    raced: tuple[Builder, TestClient, Staff], case: str, repeat: int
) -> None:
    b, client, owner = raced
    CASES[case](b, client, owner)
    harness.assert_catalogue(b.url, TODAY, b.catalogue, what=f"after the {case} race")
