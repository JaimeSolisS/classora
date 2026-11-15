"""Job walk (#103 E3; spec W7.2–W7.4): every registered Procrastinate task
whose name starts with ``billing:`` (read from the app's task registry)
gets, on a fresh world copy each, with three items to process:

- **rerun:** two serial runs; the second writes no ledger, package or
  reminder row;
- **concurrent:** two runs at once on separate engines; every expected row
  exactly once;
- **retry after failure:** the per-item step raises once on the second item
  (after the first item committed), then a rerun completes; every expected
  row exactly once, nothing duplicated;

and then the whole catalogue. A task without an entry in :data:`CASES`
fails the walk, naming it: a new ``billing:`` job adds a case here (its
by-hand ``run_*`` entry point and its per-item step)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.modules.billing import service as billing_service
from app.modules.scheduling.schemas import GroupEnrollmentCreate
from app.workflows import (
    charge_reminders,
    enrollment_charges,
    group_enrollment,
    package_expiry,
    packages,
    schedule_reconciliation,
    session_charges,
    tuition_charges,
    tuition_plans,
)
from app.workflows.package_schemas import PackagePurchase
from app.workflows.tuition_plan_schemas import TuitionPlanCreate
from tests.conftest import frozen_clock
from tests.factories import billing as billing_factories
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.billing_invariants import harness
from tests.integration.billing_invariants import world as worlds
from tests.integration.conftest import run_sql, settings_for
from tests.integration.lifecycle_support import in_uow

STUDENTS = ("A1", "B1", "C1")
LEDGER_ROWS = (
    "SELECT (SELECT COUNT(*) FROM charges) + (SELECT COUNT(*) FROM charge_adjustments)"
    " + (SELECT COUNT(*) FROM package_credit_transactions)"
    " + (SELECT COUNT(*) FROM charge_reminders)"
    " + (SELECT COUNT(*) FROM session_cancellation_decisions)"
    " + (SELECT COUNT(*) FROM charge_duration_repricings)"
)


def billing_tasks() -> list[str]:
    settings = settings_for(
        "postgresql://placeholder:placeholder@127.0.0.1:1/placeholder"
    )
    app = jobs.create_job_app(jobs.worker_connector(settings), app_env="prod")
    return sorted(name for name in app.tasks if name.startswith("billing:"))


type Run = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True)
class Case:
    setup: Callable[[str, worlds.World], None]
    run: Run
    expected_sql: str
    """Counts the rows the job must write exactly once."""
    expected: int
    step: tuple[Any, str]
    """``(module, name)`` of the per-item step the retry case breaks."""


def _actor(url: str, world: worlds.World, call: Callable[[AsyncSession], Any]) -> Any:
    return in_uow(url, call, actor_id=world.owner.id)


def _sessions(
    url: str, world: worlds.World, *, subject: str = "IND"
) -> list[uuid.UUID]:
    """One confirmed individual session per student in :data:`STUDENTS`."""

    async def _make(session: AsyncSession) -> list[uuid.UUID]:
        out = []
        for index, label in enumerate(STUDENTS):
            day = worlds.TODAY + timedelta(days=index + 1)
            made = await persist_class_session(
                session,
                subject_id=world.subjects[subject],
                kind="individual",
                state="confirmed",
                starts_at=worlds.local(day, 9),
                ends_at=worlds.local(day, 10),
            )
            await add_participant(session, made, world.students[label])
            out.append(made.id)
        return out

    made: list[uuid.UUID] = _actor(url, world, _make)
    return made


def _session_setup(url: str, world: worlds.World) -> None:
    _sessions(url, world)


def _enrollment_setup(url: str, world: worlds.World) -> None:
    """Three G1 enrollments made with billing off (the hook posts nothing),
    then billing on: the sweeper owes three fees."""
    run_sql(url, "UPDATE billing_settings SET billing_starts_on = NULL")
    for label in STUDENTS:
        enrollment = GroupEnrollmentCreate(student_id=world.students[label])

        async def _enroll(
            s: AsyncSession, body: GroupEnrollmentCreate = enrollment
        ) -> Any:
            return await group_enrollment.enroll_student(
                s, world.groups["G1"], body, actor_id=world.owner.id
            )

        _actor(url, world, _enroll)
    run_sql(
        url, "UPDATE billing_settings SET billing_starts_on = :d", {"d": worlds.FLOOR}
    )


TUITION_NOW = worlds.local(worlds.TODAY + timedelta(days=62), 12)
"""Mid-March: three monthly periods (January, February, March) per plan."""


def _tuition_setup(url: str, world: worlds.World) -> None:
    for label in STUDENTS:
        body = TuitionPlanCreate(
            student_id=world.students[label],
            service_price_id=world.tuition_item,
            starts_on=worlds.FLOOR,
        )

        async def _create(s: AsyncSession, body: TuitionPlanCreate = body) -> Any:
            return await tuition_plans.create_tuition_plan(s, body)

        _actor(url, world, _create)


EXPIRY_NOW = worlds.local(
    worlds.TODAY + timedelta(days=worlds.PACKAGE_VALIDITY_DAYS + 5), 12
)


def _expiry_setup(url: str, world: worlds.World) -> None:
    """Three packages, each with one covered session never closed out (the
    expiry job consumes it as a fallback, then expires the rest)."""
    for label in STUDENTS:
        purchase = PackagePurchase(
            student_id=world.students[label], package_product_id=world.product
        )

        async def _buy(s: AsyncSession, body: PackagePurchase = purchase) -> Any:
            return await packages.purchase_package(s, body)

        _actor(url, world, _buy)
    _sessions(url, world, subject="PKG")


def _reconcile_setup(url: str, world: worlds.World) -> None:
    """Three charged sessions, then cancelled by the center."""
    ids = _sessions(url, world)
    asyncio.run(session_charges.run_post_session_charges(settings_for(url)))
    run_sql(
        url,
        "UPDATE class_sessions SET state = 'cancelled', cancelled_at = :at,"
        " cancellation_reason = 'Cierre', version = version + 1 WHERE id = ANY(:ids)",
        {"ids": ids, "at": worlds.NOW},
    )


REMINDER_NOW = worlds.local(worlds.TODAY + timedelta(days=5), 9)


def _reminder_setup(url: str, world: worlds.World) -> None:
    """One overdue manual charge per family."""

    async def _make(session: AsyncSession) -> None:
        for label in STUDENTS:
            await billing_factories.charge(
                session,
                world.students[label],
                "100.00",
                service_date=worlds.TODAY - timedelta(days=10),
                due=worlds.TODAY - timedelta(days=2),
            )

    _actor(url, world, _make)


def _runner(func: Callable[..., Awaitable[Any]], now: datetime | None = None) -> Run:
    async def run(url: str) -> Any:
        return await func(settings_for(url), now=now)

    return run


CASES: dict[str, Case] = {
    jobs.SESSION_CHARGES_TASK: Case(
        _session_setup,
        _runner(session_charges.run_post_session_charges),
        "SELECT COUNT(*) FROM charges WHERE kind = 'session'",
        3,
        (session_charges, "post_one"),
    ),
    jobs.ENROLLMENT_CHARGES_TASK: Case(
        _enrollment_setup,
        _runner(enrollment_charges.run_post_enrollment_charges),
        "SELECT COUNT(*) FROM charges WHERE kind = 'group_enrollment'",
        3,
        (enrollment_charges, "post_one"),
    ),
    jobs.TUITION_CHARGES_TASK: Case(
        _tuition_setup,
        _runner(tuition_charges.run_post_tuition_charges, TUITION_NOW),
        "SELECT COUNT(*) FROM charges WHERE kind = 'tuition_period'",
        9,
        (tuition_charges, "post_plan"),
    ),
    jobs.PACKAGE_EXPIRY_TASK: Case(
        _expiry_setup,
        _runner(package_expiry.run_expire_packages, EXPIRY_NOW),
        "SELECT COUNT(*) FROM package_credit_transactions"
        " WHERE kind IN ('expire', 'consume')",
        6,
        (billing_service, "expire_package"),
    ),
    jobs.SCHEDULE_RECONCILIATION_TASK: Case(
        _reconcile_setup,
        _runner(schedule_reconciliation.run_reconcile_session_charges),
        "SELECT (SELECT COUNT(*) FROM session_cancellation_decisions)"
        " + (SELECT COUNT(*)"
        " FROM charge_adjustments WHERE kind = 'cancellation_credit')",
        6,
        (schedule_reconciliation, "reconcile_one"),
    ),
    jobs.CHARGE_REMINDERS_TASK: Case(
        _reminder_setup,
        _runner(charge_reminders.run_send_charge_reminders, REMINDER_NOW),
        "SELECT COUNT(*) FROM charge_reminders",
        3,
        (charge_reminders, "remind_account"),
    ),
}


def test_billing_invariant_every_billing_job_has_a_case() -> None:
    tasks = billing_tasks()
    assert tasks, "no billing: task is registered"
    missing = [task for task in tasks if task not in CASES]
    assert missing == [], f"billing: jobs without a #103 E3 case: {missing}"
    assert sorted(CASES) == tasks, "a case for a task that is no longer registered"


def _count(url: str, sql: str) -> int:
    [row] = run_sql(url, sql)
    return int(row[0])


@pytest.fixture
def frozen() -> Iterator[None]:
    with frozen_clock(worlds.NOW.astimezone(UTC)):
        yield


def _failures(result: Any) -> int:
    return int(getattr(result, "failed", 0))


@pytest.mark.parametrize("mode", ["rerun", "concurrent", "retry"])
@pytest.mark.parametrize("task", billing_tasks())
def test_billing_invariant_job_runs_once(
    task: str,
    mode: str,
    frozen: None,
    world_template: worlds.Template,
    world_copy: Callable[[], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = CASES.get(task)
    assert case is not None, f"no #103 E3 case for {task}"
    url = world_copy()
    world = world_template.world
    case.setup(url, world)

    if mode == "rerun":
        first = asyncio.run(case.run(url))
        assert _failures(first) == 0, first
        assert _count(url, case.expected_sql) == case.expected, first
        written = _count(url, LEDGER_ROWS)
        second = asyncio.run(case.run(url))
        assert _failures(second) == 0, second
        assert _count(url, LEDGER_ROWS) == written, (first, second)
    elif mode == "concurrent":

        async def _both() -> list[Any]:
            return list(await asyncio.gather(case.run(url), case.run(url)))

        results = asyncio.run(_both())
        assert all(_failures(r) == 0 for r in results), results
        assert _count(url, case.expected_sql) == case.expected, results
    else:
        module, name = case.step
        original = getattr(module, name)
        calls = {"n": 0}

        async def flaky(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("#103 E3: the second item fails once")
            return await original(*args, **kwargs)

        monkeypatch.setattr(module, name, flaky)
        broken = asyncio.run(case.run(url))
        assert _failures(broken) >= 1, broken
        assert _count(url, case.expected_sql) < case.expected, broken
        monkeypatch.setattr(module, name, original)
        healed = asyncio.run(case.run(url))
        assert _failures(healed) == 0, healed
        assert _count(url, case.expected_sql) == case.expected, (broken, healed)
        written = _count(url, LEDGER_ROWS)
        again = asyncio.run(case.run(url))
        assert _count(url, LEDGER_ROWS) == written, again
    harness.assert_catalogue(url, worlds.TODAY, what=f"after {task} {mode}")
