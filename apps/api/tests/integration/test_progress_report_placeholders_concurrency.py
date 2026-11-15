"""Placeholder and schedule races (#316 D4, F4), with two connections
(``race`` from ``test_assignment_offer_concurrency``): transaction A works
and keeps its transaction open for a moment; B starts once A's change is
flushed, so B waits on A's row lock or unique index entry.

- two runs at once for one schedule: one report, one notification;
- the job vs the tutor's manual create of the same weekly period (both
  orders): exactly one report (the job's ``ON CONFLICT`` skip, or the
  tutor's ``409 progress_report_exists``);
- the job vs a staff deactivation (both orders): the draft is created
  before the deactivation commits, or the job skips the schedule;
- two adds of the same schedule: one ``201``, one ``409``.

Each case runs 5 times, on new rows.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.permissions import ACADEMIC_COORDINATOR, TUTOR
from app.modules.scheduling.models import ClassSession
from app.workflows import progress_report_schedule_tasks as tasks
from app.workflows import progress_report_schedules as workflow
from app.workflows import progress_reports as report_workflow
from app.workflows.progress_report_schedule_schemas import (
    ProgressReportScheduleCreate,
    ProgressReportScheduleUpdate,
)
from app.workflows.progress_report_schemas import ProgressReportCreate
from tests.conftest import frozen_clock
from tests.factories.identity import persist_user
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.conftest import run_sql_async
from tests.integration.report_schedule_support import principal
from tests.integration.test_assignment_offer_concurrency import (
    REPEATS,
    _in_uow,
    race,
)

pytestmark = pytest.mark.anyio

RUN_AT = datetime(2031, 3, 10, 7, 0, tzinfo=UTC)
TODAY = date(2031, 3, 10)
MONDAY = date(2031, 3, 3)

Change = Callable[[AsyncSession], Awaitable[Any]]


@pytest.fixture(autouse=True)
def frozen() -> Any:
    with frozen_clock(RUN_AT) as clock:
        yield clock


async def _setup(url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A tutor teaching a new student, a coordinator and a weekly schedule
    from 3 March: ``(tutor, student, coordinator, schedule)``."""

    async def _make(session: AsyncSession) -> tuple[uuid.UUID, ...]:
        tutor = await persist_user(session, roles=(TUTOR,))
        coordinator = await persist_user(session, roles=(ACADEMIC_COORDINATOR,))
        made: ClassSession = await persist_class_session(
            session,
            state="confirmed",
            tutor_user_id=tutor.id,
            starts_at=RUN_AT - timedelta(days=5),
            ends_at=RUN_AT - timedelta(days=5) + timedelta(hours=1),
            scheduled_minutes=60,
        )
        student = (await add_participant(session, made)).student_id
        return tutor.id, student, coordinator.id

    tutor, student, coordinator = await _in_uow(url, _make)
    [row] = await run_sql_async(
        url,
        "INSERT INTO progress_report_schedules (student_id, author_user_id,"
        " cadence, starts_on, created_by_user_id) VALUES (:s, :a, 'weekly', :d, :c)"
        " RETURNING id",
        {"s": student, "a": tutor, "d": MONDAY, "c": coordinator},
    )
    return tutor, student, coordinator, uuid.UUID(str(row.id))


def _place(schedule: uuid.UUID, outcomes: list[str]) -> Change:
    async def _run(db: AsyncSession) -> None:
        outcomes.append(await tasks.place_one(db, schedule, now=RUN_AT, today=TODAY))

    return _run


def _create_week(tutor: uuid.UUID, student: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await report_workflow.create_report(
            db,
            principal=principal(tutor, TUTOR),
            body=ProgressReportCreate.model_validate(
                {
                    "student_id": str(student),
                    "kind": "weekly",
                    "period_start": MONDAY.isoformat(),
                }
            ),
        )

    return _run


def _deactivate(schedule: uuid.UUID, coordinator: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.update_schedule(
            db,
            schedule_id=schedule,
            body=ProgressReportScheduleUpdate(version=1, is_active=False),
        )

    return _run


async def _reports(url: str, student: uuid.UUID) -> list[Any]:
    return await run_sql_async(
        url,
        "SELECT id, schedule_id FROM progress_reports WHERE student_id = :s",
        {"s": student},
    )


async def _due(url: str, tutor: uuid.UUID) -> int:
    [row] = await run_sql_async(
        url,
        "SELECT count(*) AS n FROM notifications WHERE recipient_user_id = :u"
        " AND notification_type = 'academics.progress_report_due'",
        {"u": tutor},
    )
    return int(row.n)


async def test_two_runs_at_once(seeded_url):
    workflow.register_schedule_notifications()
    for _ in range(REPEATS):
        tutor, student, _, schedule = await _setup(seeded_url)
        placed: list[str] = []

        outcomes = await race(
            seeded_url, _place(schedule, placed), _place(schedule, placed)
        )

        assert outcomes == ["ok", "ok"]
        assert sorted(placed) == ["created", "existing"]
        assert len(await _reports(seeded_url, student)) == 1
        assert await _due(seeded_url, tutor) == 1


@pytest.mark.parametrize("job_first", [True, False])
async def test_the_job_racing_the_tutor(seeded_url, job_first):
    workflow.register_schedule_notifications()
    for _ in range(REPEATS):
        tutor, student, _, schedule = await _setup(seeded_url)
        placed: list[str] = []
        job, manual = _place(schedule, placed), _create_week(tutor, student)

        outcomes = await race(
            seeded_url, *((job, manual) if job_first else (manual, job))
        )

        [report] = await _reports(seeded_url, student)
        if job_first:
            assert outcomes == ["ok", "progress_report_exists"]
            assert placed == ["created"]
            assert report.schedule_id == schedule
            assert await _due(seeded_url, tutor) == 1
        else:
            assert outcomes == ["ok", "ok"]
            assert placed == ["existing"]
            assert report.schedule_id is None
            assert await _due(seeded_url, tutor) == 0


@pytest.mark.parametrize("job_first", [True, False])
async def test_the_job_racing_a_deactivation(seeded_url, job_first):
    workflow.register_schedule_notifications()
    for _ in range(REPEATS):
        tutor, student, coordinator, schedule = await _setup(seeded_url)
        placed: list[str] = []
        job, off = _place(schedule, placed), _deactivate(schedule, coordinator)

        outcomes = await race(seeded_url, *((job, off) if job_first else (off, job)))

        assert outcomes == ["ok", "ok"]
        reports = await _reports(seeded_url, student)
        if job_first:
            # The draft was created before the deactivation committed.
            assert placed == ["created"]
            assert len(reports) == 1
        else:
            assert placed == ["skipped"]
            assert reports == []
            assert await _due(seeded_url, tutor) == 0


def _add(tutor: uuid.UUID, student: uuid.UUID, coordinator: uuid.UUID) -> Change:
    async def _run(db: AsyncSession) -> None:
        await workflow.create_schedule(
            db,
            principal=principal(coordinator),
            student_id=student,
            body=ProgressReportScheduleCreate(author_user_id=tutor, cadence="monthly"),
        )

    return _run


async def test_two_adds_of_one_schedule(seeded_url):
    for _ in range(REPEATS):
        tutor, student, coordinator, _ = await _setup(seeded_url)

        outcomes = await race(
            seeded_url,
            _add(tutor, student, coordinator),
            _add(tutor, student, coordinator),
        )

        assert outcomes == ["ok", "progress_report_schedule_exists"]
        rows = await run_sql_async(
            seeded_url,
            "SELECT count(*) AS n FROM progress_report_schedules"
            " WHERE student_id = :s AND cadence = 'monthly'",
            {"s": student},
        )
        assert rows[0].n == 1
