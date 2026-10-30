"""The reconciliation step against concurrent writers (#99 D3, D10, E10; the
#91 F13 two-connection pattern; data-model §6).

1. The job holds the session ``FOR SHARE``: a duration change (``FOR
   UPDATE``, as every scheduling writer) waits; the next run reprices to the
   final minutes.
2. Two runs of the step on one key: one adjustment, one decision.
3. The job and the #91 sweeper on a session cancelled ``late`` before it was
   charged: exactly one charge.
4. The job and a #92-style withdrawal (the session ``FOR UPDATE``, the
   participant removed, ``enrollment_removal`` of the net): never both
   crediting the same net.
5. The job and a closure restore of the same row: one credit, one restore.

Each case runs :data:`REPEATS` times.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.workflows import schedule_reconciliation as reconciliation
from app.workflows import session_charges
from tests.integration import schedule_reconciliation_support as sr
from tests.integration import session_charge_support as scs
from tests.integration.conftest import short_lived_engine

HOLD_SECONDS = 0.5
REPEATS = 3
LOCK = "SELECT id FROM class_sessions WHERE id = :id FOR UPDATE"


@pytest.fixture(autouse=True)
def fresh(module_seeded_url: str) -> None:
    sr.reset(module_seeded_url)


async def _step(
    url: str,
    key: tuple[uuid.UUID, uuid.UUID],
    *,
    hold: float = 0.0,
    holding: asyncio.Event | None = None,
) -> list[str]:
    """The reconciliation step in its own connection; optionally holds its
    locks ``hold`` seconds before the commit."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            done = await reconciliation.reconcile_one(uow.session, *key, zone=sr.ZONE)
            if holding is not None:
                holding.set()
            await asyncio.sleep(hold)
            return list(done)
    finally:
        if holding is not None:
            holding.set()
        await engine.dispose()


async def _writer(
    url: str,
    session_id: uuid.UUID,
    change: Callable[[AsyncConnection], Awaitable[None]],
    *,
    holding: asyncio.Event | None = None,
    wait_for: asyncio.Event | None = None,
    hold: float = 0.0,
) -> float:
    """A scheduling writer: the session ``FOR UPDATE``, ``change``, an
    optional hold. Returns when it got the lock (monotonic seconds)."""
    if wait_for is not None:
        await wait_for.wait()
    engine = short_lived_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(LOCK), {"id": session_id})
            locked_at = time.monotonic()
            await change(conn)
            if holding is not None:
                holding.set()
            await asyncio.sleep(hold)
        return locked_at
    finally:
        if holding is not None:
            holding.set()
        await engine.dispose()


def _sql(sql: str, **params: object) -> Callable[[AsyncConnection], Awaitable[None]]:
    async def _run(conn: AsyncConnection) -> None:
        await conn.execute(text(sql), params)

    return _run


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_duration_change_waits_for_the_job_and_is_repriced_next(
    module_seeded_url, attempt
):
    url = module_seeded_url
    c = sr.charged(url)
    scs.add_price(
        url, c.item.subject, [(scs.PRICE_FROM, "150.00")], duration_minutes=30
    )
    sr.retime(url, c.session, 45)  # any-duration price: a zero repricing

    async def scenario() -> tuple[list[str], float, float]:
        holding = asyncio.Event()
        started = time.monotonic()
        done, locked_at = await asyncio.gather(
            _step(url, c.key, hold=HOLD_SECONDS, holding=holding),
            _writer(
                url,
                c.session,
                _sql(
                    "UPDATE class_sessions SET ends_at = starts_at + interval"
                    " '30 minutes', scheduled_minutes = 30, version = version + 1"
                    " WHERE id = :id",
                    id=c.session,
                ),
                wait_for=holding,
            ),
        )
        return done, started, locked_at

    done, started, locked_at = asyncio.run(scenario())

    assert done == ["repriced_zero"]
    assert locked_at - started >= HOLD_SECONDS * 0.9  # the writer waited
    assert sr.run(url).repriced == 1
    rows = sr.repricings(url, c.charge)
    assert [r.scheduled_minutes for r in rows] == [45, 30]
    assert sr.net(url, c.charge) == Money("150.00")


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_steps_on_one_key_post_once(module_seeded_url, attempt):
    url = module_seeded_url
    c = sr.charged(url)
    sr.cancel(url, c.session)

    async def scenario() -> list[list[str]]:
        return list(await asyncio.gather(_step(url, c.key), _step(url, c.key)))

    outcomes = sorted(asyncio.run(scenario()))

    assert outcomes == [["cancellation_credited"], ["skipped_not_applicable"]]
    assert len(sr.adjustments(url, c.charge)) == 1
    assert sr.decision(url, c.session, c.student).result == "credited"


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_the_job_and_the_sweeper_post_one_charge(module_seeded_url, attempt):
    url = module_seeded_url
    it = scs.item(url, starts_at=sr.local(sr.DAY))
    scs.set_floor(url, sr.FLOOR)
    sr.approved_request(url, it.session, student=it.student, classification="late")

    async def sweep() -> str:
        engine = short_lived_engine(url)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                return await session_charges.post_one(
                    uow.session, it.session, it.student, zone=sr.ZONE
                )
        finally:
            await engine.dispose()

    async def scenario() -> tuple[float, str, list[str]]:
        holding = asyncio.Event()

        async def later_step() -> list[str]:
            await holding.wait()
            return await _step(url, (it.session, it.student))

        async def later_sweep() -> str:
            await holding.wait()
            return await sweep()

        return await asyncio.gather(
            _writer(
                url,
                it.session,
                _sql(
                    sr.CANCEL_SQL,
                    id=it.session,
                    at=sr.local(sr.FLOOR),
                    reason="Cancelada tarde",
                ),
                holding=holding,
                hold=HOLD_SECONDS,
            ),
            later_sweep(),
            later_step(),
        )

    _, swept, done = asyncio.run(scenario())

    assert swept == "not_billable"
    assert done == ["cancellation_charged"]
    [charge] = sr.charges_of(url, it.session)
    assert sr.decision(url, it.session, it.student).charge_id == charge.id


def test_a_charge_posted_before_a_late_cancel_stands(module_seeded_url):
    url = module_seeded_url
    c = sr.charged(url)
    sr.approved_request(url, c.session, student=c.student, classification="late")
    sr.cancel(url, c.session)

    assert sr.run(url).cancellation_charge_stands == 1
    assert len(sr.charges_of(url, c.session)) == 1


async def _withdrawal(
    url: str,
    c: sr.Charged,
    *,
    holding: asyncio.Event | None = None,
    wait_for: asyncio.Event | None = None,
    hold: float = 0.0,
) -> None:
    """A #92-style withdrawal in one Unit of Work: the session ``FOR
    UPDATE``, the participant removed, then ``enrollment_removal`` of the
    net (read after the session lock; ``post_adjustment`` takes the account
    lock)."""
    if wait_for is not None:
        await wait_for.wait()
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            session = uow.session
            await session.execute(text(LOCK), {"id": c.session})
            await session.execute(
                text(
                    "DELETE FROM class_session_participants"
                    " WHERE class_session_id = :s AND student_id = :st"
                ),
                {"s": c.session, "st": c.student},
            )
            state = (await billing.session_charge_states(session, [c.key]))[c.key]
            if state.net.is_positive():
                await billing.post_adjustment(
                    session,
                    billing.AdjustmentDraft(
                        charge_id=state.charge_id,
                        kind="enrollment_removal",
                        amount=-state.net,
                        source_key=(
                            f"enrollment_removal:{state.charge_id}:test:withdrawn"
                        ),
                    ),
                )
            if holding is not None:
                holding.set()
            await asyncio.sleep(hold)
    finally:
        if holding is not None:
            holding.set()
        await engine.dispose()


@pytest.mark.parametrize("job_first", [True, False])
@pytest.mark.parametrize("attempt", range(REPEATS))
def test_the_job_and_a_withdrawal_never_both_credit(
    module_seeded_url, job_first, attempt
):
    url = module_seeded_url
    c = sr.charged(url)
    sr.cancel(url, c.session)

    async def scenario() -> None:
        holding = asyncio.Event()
        if job_first:
            await asyncio.gather(
                _step(url, c.key, hold=HOLD_SECONDS, holding=holding),
                _withdrawal(url, c, wait_for=holding),
            )
        else:

            async def later_step() -> list[str]:
                await holding.wait()
                return await _step(url, c.key)

            await asyncio.gather(
                _withdrawal(url, c, holding=holding, hold=HOLD_SECONDS),
                later_step(),
            )

    asyncio.run(scenario())

    credits = [
        row.kind
        for row in sr.adjustments(url, c.charge)
        if row.kind in ("cancellation_credit", "enrollment_removal")
    ]
    assert credits == (["cancellation_credit"] if job_first else ["enrollment_removal"])
    assert sr.net(url, c.charge) == Money.zero()


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_the_job_and_a_closure_restore_converge(module_seeded_url, attempt):
    url = module_seeded_url
    c = sr.charged(url)
    sr.set_state(url, c.session, "skipped_closure")
    assert sr.run(url).closure_credited == 1

    async def scenario() -> list[str]:
        holding = asyncio.Event()

        async def later_step() -> list[str]:
            await holding.wait()
            return await _step(url, c.key)

        _, done = await asyncio.gather(
            _writer(
                url,
                c.session,
                _sql(
                    "UPDATE class_sessions SET state = 'confirmed',"
                    " version = version + 1 WHERE id = :id",
                    id=c.session,
                ),
                holding=holding,
                hold=HOLD_SECONDS,
            ),
            later_step(),
        )
        return done

    assert asyncio.run(scenario()) == ["closure_restored"]
    assert sr.run(url).candidates == 0
    assert [Money(r.amount) for r in sr.adjustments(url, c.charge)] == [
        Money("-250.00"),
        Money("250.00"),
    ]
