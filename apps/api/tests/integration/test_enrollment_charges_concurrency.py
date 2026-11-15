"""Enrollment charges against concurrent writers (#92 G6; the #276 / #91
F13 lock-test pattern; data-model §6).

- two enrollment sweeps on two engines over the same periods post each fee
  once;
- a group withdrawal holding the group and its sessions ``FOR UPDATE``
  makes the session step wait, which then posts nothing; the other way
  round, the withdrawal waits for the session step and credits the charge
  it posted;
- the fee step of a period and the session step of one of its sessions
  both wait on the family's account lock: exactly one kind of charge (J2);
- the sweeper's step holds the cohort ``FOR SHARE``: a concurrent
  enrollment waits, then posts its own fee once;
- billing turned off while the hook waits on ``billing_settings``: no fee.

Each case runs 3 times.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.billing import service as billing
from app.modules.scheduling.schemas import GroupWithdrawal
from app.workflows import (
    cohort_enrollment,
    enrollment_charges,
    group_enrollment,
    session_charges,
)
from app.workflows.cohort_schemas import CohortEnrollmentCreate
from tests.conftest import frozen_clock
from tests.integration import enrollment_charge_support as ec
from tests.integration.auth_support import auth_client, create_user
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


@pytest.fixture(scope="module")
def actor(module_seeded_url: str) -> uuid.UUID:
    return create_user(module_seeded_url, roles=("owner",)).id


async def _in_uow(
    url: str,
    step: Callable[[Any], Awaitable[Any]],
    *,
    actor_id: uuid.UUID | None = None,
    holding: asyncio.Event | None = None,
    hold: float = 0.0,
) -> tuple[Any, float]:
    """``step(uow)`` in its own Unit of Work; with ``holding``, set it after
    the step and keep the transaction open ``hold`` seconds. Returns the
    result and the instant just before the commit."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine), actor_id=actor_id) as uow:
            result = await step(uow)
            if holding is not None:
                holding.set()
                await asyncio.sleep(hold)
            committed_at = time.monotonic()
        return result, committed_at
    finally:
        if holding is not None:
            holding.set()
        await engine.dispose()


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_two_sweeps_post_each_fee_once(module_seeded_url, client, attempt):
    url = module_seeded_url
    tl = timeline()
    subject = new_subject(url)
    learners = [new_student(url) for _ in range(10)]
    for student in learners:
        ec.add_family(url, student)
    ec.set_floor(url, None)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        ec.cohort_fee(url, subject, "500.00", cohort_id=uuid.UUID(cohort["id"]))
        periods = [
            ec.enroll_cohort(client, cookie, cohort["id"], s)["id"] for s in learners
        ]
    ec.set_floor(url, ec.floor_of(tl))

    async def _both() -> list[Any]:
        settings = settings_for(url)
        return list(
            await asyncio.gather(
                enrollment_charges.run_post_enrollment_charges(settings),
                enrollment_charges.run_post_enrollment_charges(settings),
            )
        )

    first, second = asyncio.run(_both())

    assert first.posted + second.posted == 10
    assert first.failed == second.failed == 0
    assert all(len(ec.fees(url, cohort_enrollment=p)) == 1 for p in periods)


def _per_session_group(url: str, client: Any, tl: Any) -> dict[str, Any]:
    ec.set_floor(url, ec.floor_of(tl))
    subject = new_subject(url)
    student = new_student(url)
    ec.add_family(url, student)
    ec.group_session_price(url, subject, "120.00")
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = ec.make_group(client, cookie, subject, tl.monday, [student])
    sessions = ec.sessions_of(url, group_id=group["id"])
    ec.confirm_sessions(url, [sessions[1].id])
    period = ec.group_enrollment_id(url, group["id"], student)
    return {
        "group": uuid.UUID(group["id"]),
        "student": student,
        "session": sessions[1].id,
        "period": period,
    }


def _withdraw(g: dict[str, Any], actor: uuid.UUID) -> Callable[[Any], Awaitable[Any]]:
    async def _step(uow: Any) -> Any:
        return await group_enrollment.withdraw_student(
            uow.session,
            g["group"],
            g["period"],
            GroupWithdrawal(version=1),
            actor_id=actor,
        )

    return _step


def _session_step(g: dict[str, Any]) -> Callable[[Any], Awaitable[Any]]:
    async def _step(uow: Any) -> Any:
        return await session_charges.post_one(
            uow.session, g["session"], g["student"], zone=ZONE
        )

    return _step


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_withdrawal_holding_the_sessions_wins(
    module_seeded_url, client, actor, attempt
):
    url = module_seeded_url
    tl = timeline()
    g = _per_session_group(url, client, tl)

    async def _race() -> tuple[Any, float, float]:
        holding = asyncio.Event()

        async def run_b() -> tuple[Any, float]:
            await holding.wait()
            outcome, _ = await _in_uow(url, _session_step(g))
            return outcome, time.monotonic()

        (_, committed_at), (outcome, finished_at) = await asyncio.gather(
            _in_uow(url, _withdraw(g, actor), holding=holding, hold=HOLD_SECONDS),
            run_b(),
        )
        return outcome, committed_at, finished_at

    with frozen_clock(tl.at(tl.monday, "18:00")):
        outcome, committed_at, finished_at = asyncio.run(_race())

    assert outcome == "not_billable"
    assert finished_at >= committed_at
    assert ec.charges_of(url, g["student"], "session") == []


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_withdrawal_waits_for_the_session_step_and_credits_it(
    module_seeded_url, client, actor, attempt
):
    url = module_seeded_url
    tl = timeline()
    g = _per_session_group(url, client, tl)

    async def _race() -> tuple[Any, float, float]:
        holding = asyncio.Event()

        async def run_a() -> float:
            await holding.wait()
            await _in_uow(url, _withdraw(g, actor))
            return time.monotonic()

        (outcome, committed_at), finished_at = await asyncio.gather(
            _in_uow(url, _session_step(g), holding=holding, hold=HOLD_SECONDS),
            run_a(),
        )
        return outcome, committed_at, finished_at

    with frozen_clock(tl.at(tl.monday, "18:00")):
        outcome, committed_at, finished_at = asyncio.run(_race())

    assert outcome == "posted"
    assert finished_at >= committed_at
    [charge] = ec.charges_of(url, g["student"], "session")
    [credit] = ec.adjustments_of(url, charge.id)
    assert credit.kind == "enrollment_removal"
    assert ec.net_of(url, charge.id) == "0.00"


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_the_fee_and_a_session_charge_of_one_period_never_both_post(
    module_seeded_url, client, attempt
):
    url = module_seeded_url
    tl = timeline()
    g = _per_session_group(url, client, tl)
    [family] = run_sql(
        url,
        "SELECT family_id FROM family_students WHERE student_id = :s",
        {"s": g["student"]},
    )
    account = in_uow(url, lambda s: billing.ensure_billing_account(s, family.family_id))
    [subject] = run_sql(
        url, "SELECT subject_id FROM class_groups WHERE id = :g", {"g": g["group"]}
    )

    async def _race() -> list[Any]:
        engine = short_lived_engine(url)
        try:
            async with engine.begin() as conn:
                # C: the family's account row, held.
                await conn.execute(
                    text("SELECT id FROM billing_accounts WHERE id = :a FOR UPDATE"),
                    {"a": account},
                )
                session_step = asyncio.create_task(_in_uow(url, _session_step(g)))
                await asyncio.sleep(HOLD_SECONDS)  # classified, waiting on C
                # A back-dated flat fee appears (#89 E3 allows it).
                await asyncio.to_thread(ec.group_fee, url, subject.subject_id, "800.00")
                fee_step = asyncio.create_task(
                    _in_uow(
                        url,
                        lambda uow: enrollment_charges.post_one(
                            uow.session, "group_enrollment", g["period"]
                        ),
                    )
                )
                await asyncio.sleep(HOLD_SECONDS)
            return [(await session_step)[0], (await fee_step)[0]]
        finally:
            await engine.dispose()

    outcomes = asyncio.run(_race())

    sessions = ec.charges_of(url, g["student"], "session")
    fees = ec.charges_of(url, g["student"], "group_enrollment")
    assert (len(sessions), len(fees)) in ((1, 0), (0, 1)), outcomes
    # The session step queued first on the lock: its re-check (E3) sees the
    # fee-priced period and posts nothing; the fee step posts.
    assert outcomes == ["enrollment_priced", "posted"]


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_a_concurrent_enrollment_waits_for_the_sweeper_step(
    module_seeded_url, client, actor, attempt
):
    url = module_seeded_url
    tl = timeline()
    subject = new_subject(url)
    first, second = new_student(url), new_student(url)
    ec.add_family(url, first)
    ec.add_family(url, second)
    ec.set_floor(url, None)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
        cohort_id = uuid.UUID(cohort["id"])
        ec.cohort_fee(url, subject, "500.00", cohort_id=cohort_id)
        waiting = ec.enroll_cohort(client, cookie, cohort["id"], first)
    ec.set_floor(url, ec.floor_of(tl))

    async def _enroll(uow: Any) -> Any:
        return await cohort_enrollment.enroll_in_cohort(
            uow, cohort_id, CohortEnrollmentCreate(student_id=second), actor_id=actor
        )

    async def _race() -> tuple[Any, float, Any, float]:
        holding = asyncio.Event()

        async def run_a() -> tuple[Any, float]:
            await holding.wait()
            change, _ = await _in_uow(url, _enroll, actor_id=actor)
            return change, time.monotonic()

        (outcome, committed_at), (change, finished_at) = await asyncio.gather(
            _in_uow(
                url,
                lambda uow: enrollment_charges.post_one(
                    uow.session, "cohort_enrollment", uuid.UUID(waiting["id"])
                ),
                holding=holding,
                hold=HOLD_SECONDS,
            ),
            run_a(),
        )
        return outcome, committed_at, change, finished_at

    with frozen_clock(tl.now + timedelta(minutes=5)):
        outcome, committed_at, change, finished_at = asyncio.run(_race())

    assert outcome == "posted"
    assert finished_at >= committed_at
    assert len(ec.fees(url, cohort_enrollment=waiting["id"])) == 1
    assert len(ec.fees(url, cohort_enrollment=change.enrollment_id)) == 1


@pytest.mark.parametrize("attempt", range(REPEATS))
def test_billing_turned_off_while_the_hook_waits_posts_nothing(
    module_seeded_url, client, actor, attempt
):
    url = module_seeded_url
    tl = timeline()
    subject = new_subject(url)
    student = new_student(url)
    ec.add_family(url, student)
    ec.set_floor(url, ec.floor_of(tl))
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = ec.make_cohort(client, cookie, subject, tl.monday)
    cohort_id = uuid.UUID(cohort["id"])
    ec.cohort_fee(url, subject, "500.00", cohort_id=cohort_id)

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
                hook = asyncio.create_task(
                    _in_uow(
                        url,
                        lambda uow: cohort_enrollment.enroll_in_cohort(
                            uow,
                            cohort_id,
                            CohortEnrollmentCreate(student_id=student),
                            actor_id=actor,
                        ),
                        actor_id=actor,
                    )
                )
                await asyncio.sleep(HOLD_SECONDS)
            change, _ = await hook
            return change
        finally:
            await engine.dispose()

    with frozen_clock(tl.now + timedelta(minutes=5)):
        change = asyncio.run(_race())

    assert ec.fees(url, cohort_enrollment=change.enrollment_id) == []
