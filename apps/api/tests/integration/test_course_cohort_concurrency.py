"""Concurrent cohort writes (#65 G2, C3; the #47 D3 two-connection
pattern): transaction A makes its change and keeps its transaction open for
a moment; B starts once A's change is flushed and uncommitted.

- **One name twice** (different case): one ``201``, one ``409
  cohort_name_taken`` (the unique index waits for A).
- **Two cohorts in one room** at the same times: one lands, the other is
  ``room_overlap`` (the room ``EXCLUDE`` constraint waits for A).
- **A ``PATCH`` during the extension** of the cohort's series: no deadlock,
  both land.
- **The cohort lock is really taken** by the extension: while A holds the
  cohort row ``FOR UPDATE``, B's extension finishes only after A commits.
- **A template ``PUT`` during a cohort create**: the ``PUT`` waits for the
  create's ``FOR SHARE`` on the template, and the cohort keeps the version
  it read.

Each case runs 5 times, on new references.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.uow import UnitOfWork
from app.modules.courses import service as courses
from app.modules.courses.schemas import CohortUpdate, CourseTemplateReplace
from app.modules.scheduling import service as scheduling
from app.workflows import cohorts
from app.workflows.cohort_schemas import CohortCreate, CohortRecurrence
from tests.conftest import frozen_clock
from tests.factories.courses import persist_course_template
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.test_class_session_create_concurrency import (
    HOLD_SECONDS,
    REPEATS,
    _references,
)

pytestmark = pytest.mark.anyio

MX = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 1, 5, 12, 0, tzinfo=MX)  # Sunday
MONDAY = date(2031, 1, 6)
HORIZON = 12

Change = Callable[[AsyncSession], Awaitable[Any]]


async def _outcome(engine: Any, change: Change, after: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow.session)
            if after is not None:
                await after()
    except scheduling.SchedulingConflictError as exc:
        return ",".join(conflict["code"] for conflict in exc.conflicts or [])
    except AppError as exc:
        return exc.code
    return "ok"


async def race(url: str, first: Change, second: Change) -> tuple[list[str], float]:
    """``first`` (A, held open after its change) and ``second`` (B, started
    once A's change is flushed); the outcomes and how long B finished after
    A committed (negative: before)."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    changed = asyncio.Event()
    loop = asyncio.get_running_loop()
    ends: dict[str, float] = {}

    async def hold() -> None:
        changed.set()
        await asyncio.sleep(HOLD_SECONDS)

    async def run_a() -> str:
        try:
            return await _outcome(engine_a, first, hold)
        finally:
            ends["a"] = loop.time()
            changed.set()

    async def run_b() -> str:
        await changed.wait()
        try:
            return await _outcome(engine_b, second)
        finally:
            ends["b"] = loop.time()

    try:
        outcomes = list(await asyncio.gather(run_a(), run_b()))
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
    return outcomes, ends["b"] - ends["a"]


async def _template(url: str, subject: uuid.UUID, session_count: int = 10) -> uuid.UUID:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            template = await persist_course_template(
                uow.session,
                subject_id=subject,
                session_count=session_count,
                session_minutes=60,
            )
            return template.id
    finally:
        await engine.dispose()


def create(
    refs: dict[str, uuid.UUID],
    template_id: uuid.UUID,
    *,
    name: str | None = None,
    room: bool = False,
    weekdays: tuple[int, ...] = (1, 3),
) -> Change:
    body = CohortCreate(
        name=name or f"Cohorte {uuid.uuid4().hex[:10]}",
        template_id=template_id,
        subject_id=refs["subject"],
        max_students=12,
        classroom_id=refs["room"] if room else None,
        starts_on=MONDAY,
        start_time=time(16),
        recurrence=CohortRecurrence(weekdays=list(weekdays)),
    )

    async def _create(session: AsyncSession) -> None:
        await cohorts.create_cohort(
            session, body, horizon_weeks=HORIZON, actor_id=refs["tutor"]
        )

    return _create


async def _cohort(url: str, change: Change) -> None:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow.session)
    finally:
        await engine.dispose()


async def _cohort_of_template(url: str, template_id: uuid.UUID) -> Any:
    [row] = await run_sql_async(
        url,
        "SELECT c.id, c.version, c.template_snapshot_id, s.id AS series_id,"
        " t.template_version"
        " FROM course_cohorts c JOIN class_series s ON s.cohort_id = c.id"
        " JOIN course_template_snapshots t ON t.id = c.template_snapshot_id"
        " WHERE c.template_id = :id",
        {"id": template_id},
    )
    return row


def extend(series_id: uuid.UUID, now: datetime) -> Change:
    async def _extend(session: AsyncSession) -> None:
        await scheduling.materialize_series(
            session, series_id, now=now, horizon_weeks=HORIZON, mode="extend"
        )

    return _extend


async def test_one_name_in_two_cases_at_once(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            template_id = await _template(seeded_url, refs["subject"])
            name = f"Cohorte {uuid.uuid4().hex[:10]}"

            outcomes, _ = await race(
                seeded_url,
                create(refs, template_id, name=name),
                create(refs, template_id, name=name.upper()),
            )

            assert outcomes == ["ok", "cohort_name_taken"]
            rows = await run_sql_async(
                seeded_url,
                "SELECT id FROM course_cohorts WHERE template_id = :id",
                {"id": template_id},
            )
            assert len(rows) == 1


async def test_two_cohorts_in_one_room_at_once(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            template_id = await _template(seeded_url, refs["subject"])

            outcomes, _ = await race(
                seeded_url,
                create(refs, template_id, room=True),
                create(refs, template_id, room=True),
            )

            assert outcomes[0] == "ok"
            assert set(outcomes[1].split(",")) == {"room_overlap"}
            rows = await run_sql_async(
                seeded_url,
                "SELECT id FROM course_cohorts WHERE template_id = :id",
                {"id": template_id},
            )
            assert len(rows) == 1


async def test_a_patch_during_the_extension(seeded_url):
    later = NOW + timedelta(weeks=2)
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            template_id = await _template(seeded_url, refs["subject"], 40)
            await _cohort(seeded_url, create(refs, template_id, weekdays=(1,)))
            cohort = await _cohort_of_template(seeded_url, template_id)

            async def patch(session: AsyncSession, cohort_id=cohort.id) -> None:
                await courses.update_cohort(
                    session, cohort_id, CohortUpdate(version=1, max_students=20)
                )

            first, _ = await race(seeded_url, extend(cohort.series_id, later), patch)
            assert first == ["ok", "ok"]

            refs = await _references(seeded_url)
            template_id = await _template(seeded_url, refs["subject"], 40)
            await _cohort(seeded_url, create(refs, template_id, weekdays=(1,)))
            cohort = await _cohort_of_template(seeded_url, template_id)

            async def patch_first(session: AsyncSession, cohort_id=cohort.id) -> None:
                await courses.update_cohort(
                    session, cohort_id, CohortUpdate(version=1, max_students=20)
                )

            second, _ = await race(
                seeded_url, patch_first, extend(cohort.series_id, later)
            )
            assert second == ["ok", "ok"]
            [row] = await run_sql_async(
                seeded_url,
                "SELECT count(*) AS n FROM class_sessions WHERE cohort_id = :id",
                {"id": cohort.id},
            )
            assert row.n == 14  # 12 weeks at create, 2 more two weeks later


async def test_the_extension_waits_for_the_cohort_lock(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            template_id = await _template(seeded_url, refs["subject"], 40)
            await _cohort(seeded_url, create(refs, template_id, weekdays=(1,)))
            cohort = await _cohort_of_template(seeded_url, template_id)

            async def hold_cohort(session: AsyncSession, cohort_id=cohort.id) -> None:
                assert await courses.lock_cohort(session, cohort_id) is not None

            # An extension with nothing new to insert: a new occurrence's
            # foreign key to the cohort would wait for A's row lock anyway,
            # so only the explicit cohort lock makes this one wait.
            outcomes, lag = await race(
                seeded_url, hold_cohort, extend(cohort.series_id, NOW)
            )

            assert outcomes == ["ok", "ok"]
            # B finished only after A committed (it waited on the row lock).
            assert lag >= 0


async def test_a_template_put_during_a_cohort_create(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            template_id = await _template(seeded_url, refs["subject"])

            async def put(
                session: AsyncSession, template_id=template_id, subject=refs["subject"]
            ) -> None:
                await courses.replace_course_template(
                    session,
                    template_id,
                    CourseTemplateReplace.model_validate(
                        {
                            "version": 1,
                            "name": f"Curso {uuid.uuid4().hex[:10]}",
                            "subject_ids": [str(subject)],
                            "session_count": 10,
                            "session_minutes": 60,
                            "modules": [{"title": "Nuevo", "is_required": True}],
                            "assessments": [],
                        }
                    ),
                )

            outcomes, lag = await race(seeded_url, create(refs, template_id), put)

            assert outcomes == ["ok", "ok"]
            assert lag >= 0  # the PUT waited for the create to commit
            cohort = await _cohort_of_template(seeded_url, template_id)
            assert cohort.template_version == 1
            [template] = await run_sql_async(
                seeded_url,
                "SELECT version FROM course_templates WHERE id = :id",
                {"id": template_id},
            )
            assert template.version == 2
