"""Concurrent series writes (#53 J3; the #47 D3 two-connection pattern).

- **Two extend runs of one series:** B waits on A's locks (the student lock,
  then the series row ``FOR UPDATE``) until A commits; its pre-filter then
  sees A's rows and inserts nothing. No duplicate and no ``IntegrityError``.
- **A series create and a one-off create for the same student:** the
  per-student advisory lock serializes them; exactly one is stored.

Each case runs 5 times, on new references; the clock is frozen so the
series' first weeks are inside the horizon.
"""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import ClassSeriesCreate, SeriesRecurrence
from tests.conftest import frozen_clock
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.test_class_session_create_concurrency import (
    REPEATS,
    Change,
    _references,
    create,
    race,
)

pytestmark = pytest.mark.anyio

MX = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 1, 5, 12, 0, tzinfo=MX)  # Sunday
MONDAY = date(2031, 1, 6)
HORIZON = 12


def series_create(refs: dict[str, uuid.UUID], student: str = "student") -> Change:
    body = ClassSeriesCreate(
        kind="individual",
        student_id=refs[student],
        subject_id=refs["subject"],
        tutor_user_id=refs["tutor"],
        classroom_id=refs["room"],
        starts_on=MONDAY,
        start_time=time(16),
        duration_minutes=60,
        recurrence=SeriesRecurrence(weekdays=[1, 3]),
    )

    async def _create(session: AsyncSession) -> None:
        await scheduling.create_class_series(session, body, horizon_weeks=HORIZON)

    return _create


def extend(series_id: uuid.UUID, now: datetime) -> Change:
    async def _extend(session: AsyncSession) -> None:
        await scheduling.materialize_series(
            session, series_id, now=now, horizon_weeks=HORIZON, mode="extend"
        )

    return _extend


async def _series_of(url: str, student_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await run_sql_async(
        url,
        "SELECT class_series_id FROM class_series_participants WHERE student_id = :id",
        {"id": student_id},
    )
    return [row.class_series_id for row in rows]


async def _rows(url: str, series_id: uuid.UUID) -> list[tuple[datetime, str]]:
    rows = await run_sql_async(
        url,
        "SELECT original_start, state FROM class_sessions WHERE series_id = :id"
        " ORDER BY original_start",
        {"id": series_id},
    )
    return [(row.original_start, row.state) for row in rows]


async def test_two_extend_runs_of_one_series_insert_each_row_once(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            engine = short_lived_engine(seeded_url)
            try:
                async with UnitOfWork(create_sessionmaker(engine)) as uow:
                    await series_create(refs)(uow.session)
            finally:
                await engine.dispose()
            [series_id] = await _series_of(seeded_url, refs["student"])
            later = NOW + timedelta(weeks=2)

            outcomes = await race(
                seeded_url, extend(series_id, later), extend(series_id, later)
            )

            rows = await _rows(seeded_url, series_id)
            assert outcomes == ["ok", "ok"]
            assert len(rows) == 28
            assert len({start for start, _ in rows}) == 28
            assert {state for _, state in rows} == {"offered"}


async def test_a_series_and_a_one_off_for_one_student_never_both_land(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            # The one-off overlaps the series' first Monday, 16:30-17:30.
            one_off_start = datetime.combine(MONDAY, time(16, 30), tzinfo=MX)

            outcomes = await race(
                seeded_url,
                series_create(refs),
                create(refs, "student", "other_tutor", "other_room", one_off_start),
            )

            sessions = await run_sql_async(
                seeded_url,
                "SELECT count(*) AS n FROM class_session_participants p"
                " JOIN class_sessions s ON s.id = p.class_session_id"
                " WHERE p.student_id = :id AND s.series_id IS NULL",
                {"id": refs["student"]},
            )
            assert outcomes == ["ok", "student_overlap"]
            assert len(await _series_of(seeded_url, refs["student"])) == 1
            assert sessions[0].n == 0

            # The other way round: the one-off first, then the series.
            refs = await _references(seeded_url)
            outcomes = await race(
                seeded_url,
                create(refs, "student", "other_tutor", "other_room", one_off_start),
                series_create(refs),
            )

            assert outcomes == ["ok", "student_overlap"]
            assert await _series_of(seeded_url, refs["student"]) == []
