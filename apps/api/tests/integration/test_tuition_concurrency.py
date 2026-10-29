"""Tuition plans against concurrent writers (#93 H7; the #276 / #91 F13 /
#92 G6 lock-test pattern; data-model §6).

- a linked plan create and the session step of a group session in its
  window both wait on the family's account lock (held by a third
  connection): after the release exactly one of {plan, session charge}
  exists, whichever queued first;
- the job's plan step holds the plan ``FOR SHARE``: a pause waits, then
  refuses the period that just posted, or succeeds for a later one;
- billing turned off while the job waits on ``billing_settings``: nothing
  is posted.

Each case runs 3 times.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.workflows import session_charges, tuition_charges, tuition_plans
from app.workflows.tuition_plan_schemas import TuitionPauseCreate, TuitionPlanCreate
from tests.conftest import frozen_clock
from tests.integration import enrollment_charge_support as ec
from tests.integration import session_charge_support as sc
from tests.integration import tuition_support as ts
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.lifecycle_support import in_uow
from tests.integration.series_support import owner_cookie, timeline
from tests.integration.test_class_session_create import new_student, new_subject

HOLD_SECONDS = 0.5
REPEATS = 3
ZONE = "America/Mexico_City"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    sc.reset(module_seeded_url)
    ts.reset(module_seeded_url)


async def _in_uow(
    url: str,
    step: Callable[[Any], Awaitable[Any]],
    *,
    holding: asyncio.Event | None = None,
    hold: float = 0.0,
) -> Any:
    """``step(session)`` in its own Unit of Work; with ``holding``, set it
    after the step and keep the transaction open ``hold`` seconds. Returns
    the result, or the exception the step raised."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine), actor_id=None) as uow:
            try:
                result = await step(uow.session)
            except Exception as exc:  # noqa: BLE001 - the race's outcome
                return exc
            if holding is not None:
                holding.set()
                await asyncio.sleep(hold)
        return result
    finally:
        if holding is not None:
            holding.set()
        await engine.dispose()


@pytest.mark.parametrize("plan_first", [False, True])
@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_linked_plan_and_an_in_window_session_charge_never_both_exist(
    module_seeded_url, client, attempt, plan_first
):
    url = module_seeded_url
    tl = timeline(next(ts.YEARS))
    subject = new_subject(url)
    student = new_student(url)
    family = ec.add_family(url, student)
    ec.set_floor(url, ec.floor_of(tl))
    ec.group_session_price(url, subject, "150.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [student], count=8)
    session = ec.sessions_of(url, group_id=group["id"])[2]  # week 2, Monday
    ec.confirm_sessions(url, [session.id])
    enrollment = ec.group_enrollment_id(url, group["id"], student)
    item = ec.price(
        url,
        subject,
        "800.00",
        class_type="group",
        pricing_model="per_period",
        period_frequency="monthly",
    )
    account = in_uow(url, lambda s: billing.ensure_billing_account(s, family))
    body = TuitionPlanCreate(
        student_id=student,
        service_price_id=item,
        group_enrollment_id=enrollment,
        starts_on=tl.monday,
    )

    async def _create(session: Any) -> Any:
        return await tuition_plans.create_tuition_plan(session, body)

    async def _charge(session: Any) -> Any:
        return await session_charges.post_one(session, session_id, student, zone=ZONE)

    session_id = session.id

    async def _race() -> list[Any]:
        engine = short_lived_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("SELECT id FROM billing_accounts WHERE id = :a FOR UPDATE"),
                    {"a": account},
                )
                first, second = (_create, _charge) if plan_first else (_charge, _create)
                one = asyncio.create_task(_in_uow(url, first))
                await asyncio.sleep(HOLD_SECONDS)
                two = asyncio.create_task(_in_uow(url, second))
                await asyncio.sleep(HOLD_SECONDS)
            return [await one, await two]
        finally:
            await engine.dispose()

    with frozen_clock(tl.now):
        outcomes = asyncio.run(_race())

    plans = run_sql(
        url,
        "SELECT id FROM tuition_plans WHERE group_enrollment_id = :e",
        {"e": enrollment},
    )
    charges = ec.charges_of(url, student, "session")
    assert (len(plans), len(charges)) in ((1, 0), (0, 1)), outcomes
    if plan_first:
        assert len(plans) == 1
        assert outcomes[1] == "tuition_priced"
    else:
        assert len(charges) == 1
        assert outcomes[0] == "posted"
        assert isinstance(outcomes[1], billing.TuitionPlanSessionsChargedError)


@pytest.mark.parametrize("later", [False, True])
@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_pause_waits_for_the_job_step(module_seeded_url, attempt, later):
    url = module_seeded_url
    start = date(2035 + attempt, 3, 1)
    w = ts.world(url)
    plan = ts.plan(url, w, starts_on=start)
    ts.set_floor(url, date(2035, 1, 1))
    pause_on = date(start.year, 4, 1) if later else start

    async def _post(session: Any) -> Any:
        return await tuition_charges.post_plan(
            session, plan, zone=ZONE, today=start, first=start
        )

    async def _pause(session: Any) -> Any:
        return await tuition_plans.add_tuition_pause(
            session, plan, TuitionPauseCreate(version=1, starts_on=pause_on)
        )

    async def _race() -> list[Any]:
        holding = asyncio.Event()
        job = asyncio.create_task(
            _in_uow(url, _post, holding=holding, hold=HOLD_SECONDS)
        )
        await holding.wait()
        pause = asyncio.create_task(_in_uow(url, _pause))
        return [await job, await pause]

    with frozen_clock(ts.local(start)):
        counts, paused = asyncio.run(_race())

    assert counts["posted"] == 1
    assert ts.starts(url, plan) == [start]
    if later:
        assert paused.pauses[0].starts_on == pause_on
    else:
        assert isinstance(paused, billing.TuitionPauseInvalidError)


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_billing_turned_off_while_the_job_waits_posts_nothing(
    module_seeded_url, attempt
):
    url = module_seeded_url
    w = ts.world(url)
    plan = ts.plan(url, w, starts_on=date(2038, 5, 1))
    ts.set_floor(url, date(2038, 1, 1))

    async def _race() -> Any:
        engine = short_lived_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT id FROM billing_settings FOR UPDATE"))
                await conn.execute(
                    text(
                        "UPDATE billing_settings SET billing_starts_on = NULL,"
                        " version = version + 1"
                    )
                )
                run = asyncio.create_task(
                    tuition_charges.run_post_tuition_charges(
                        settings_for(url), now=ts.local(date(2038, 5, 2))
                    )
                )
                await asyncio.sleep(HOLD_SECONDS)
            return await run
        finally:
            await engine.dispose()

    result = asyncio.run(_race())

    assert result.posted == 0 and result.failed == 0
    assert ts.charges(url, plan) == []
