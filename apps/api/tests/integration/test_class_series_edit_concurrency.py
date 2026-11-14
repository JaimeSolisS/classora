"""Concurrent edits (#54 E4; the #47 D3 two-connection pattern).

Transaction A runs and, once its writes are flushed, keeps its transaction
open; transaction B starts then. A commits only once B waits on a lock A holds
(``pg_stat_activity``), so B always runs its reads and writes against A's
uncommitted work, however slow the machine (#336). A fixed hold made that a
timing bet: on a loaded runner B sometimes reached its conflict reads after A
had committed and answered a different, also correct, 409.

- **An edit and the nightly extension of the same series:** both take the
  student lock, then the series row ``FOR UPDATE`` (re-read,
  ``populate_existing``, C4), so B waits for A's commit and sees its rule.
  Whatever the order, no date is held twice, nothing raises an
  ``IntegrityError``, and the rows equal a serial run's.
- **Two edits with the same version:** one ``200``, one ``409
  version_conflict``.
- **A ``PATCH`` of an occurrence and a one-off create for the same student
  and time:** the student lock serializes them; exactly one is stored.

Each case runs 5 times, on new references; the clock is frozen.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.db import create_sessionmaker
from app.core.models import StaleVersionError
from app.core.uow import UnitOfWork
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import (
    ClassSeriesCreate,
    ClassSeriesEdit,
    ClassSessionCreate,
    ClassSessionUpdate,
    SeriesRecurrence,
)
from tests.conftest import frozen_clock
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.test_class_session_create_concurrency import (
    REPEATS,
    _references,
)

pytestmark = pytest.mark.anyio

Change = Callable[[AsyncSession], Awaitable[Any]]

MX = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 1, 5, 12, 0, tzinfo=MX)  # Sunday
MONDAY = date(2031, 1, 6)
WEEK_3 = MONDAY + timedelta(weeks=2)
# How long A waits for B to block on its lock before the race fails (#336).
# Generous: B normally blocks within 0.1 s; hitting it fails the test.
LOCK_WAIT_CAP_SECONDS = 30.0
POLL_SECONDS = 0.02


async def _outcome(engine: Any, change: Change, after_change: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow.session)
            if after_change is not None:
                await after_change()
    except scheduling.SchedulingConflictError as exc:
        return ",".join(conflict["code"] for conflict in exc.conflicts or [])
    except StaleVersionError:
        return "version_conflict"
    return "ok"


async def waiting_on_a_lock(probe: AsyncEngine, b_done: asyncio.Event) -> None:
    """Returns once another backend of this test's database waits on a lock
    (B on A's: only A, B and this probe use the database). Fails when B
    finished without waiting, or after :data:`LOCK_WAIT_CAP_SECONDS`."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LOCK_WAIT_CAP_SECONDS
    async with probe.connect() as conn:
        while True:
            waiting = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database()"
                    " AND pid <> pg_backend_pid()"
                    " AND wait_event_type = 'Lock'"
                )
            )
            # pg_stat_activity is read once per transaction: end it, so the
            # next poll sees fresh rows.
            await conn.commit()
            if waiting:
                return
            if b_done.is_set():
                raise AssertionError("B finished without waiting on A's lock")
            if loop.time() >= deadline:
                raise AssertionError(
                    f"B never waited on A's lock within {LOCK_WAIT_CAP_SECONDS} s"
                )
            await asyncio.sleep(POLL_SECONDS)


async def race(url: str, first: Change, second: Change) -> list[str]:
    """Outcomes of ``first`` (A) and ``second`` (B, started once A's writes
    are flushed and still uncommitted). A commits once B waits on its lock."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    probe = short_lived_engine(url)
    changed, b_done = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        changed.set()
        await waiting_on_a_lock(probe, b_done)

    async def run_a() -> str:
        try:
            return await _outcome(engine_a, first, hold)
        finally:
            changed.set()

    async def run_b() -> str:
        await changed.wait()
        try:
            return await _outcome(engine_b, second)
        finally:
            b_done.set()

    try:
        return list(await asyncio.gather(run_a(), run_b()))
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
        await probe.dispose()


async def _in_uow(url: str, change: Change) -> Any:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return await change(uow.session)
    finally:
        await engine.dispose()


async def _new_series(
    url: str, refs: dict[str, uuid.UUID], *, horizon: int
) -> uuid.UUID:
    """An open-ended Monday/Wednesday 16:00 series without a tutor, its rows
    created up to ``horizon`` weeks."""
    body = ClassSeriesCreate(
        kind="individual",
        student_id=refs["student"],
        subject_id=refs["subject"],
        classroom_id=refs["room"],
        starts_on=MONDAY,
        start_time=time(16),
        duration_minutes=60,
        recurrence=SeriesRecurrence(weekdays=[1, 3]),
    )

    async def _create(session: AsyncSession) -> uuid.UUID:
        created = await scheduling.create_class_series(
            session, body, horizon_weeks=horizon, actor_id=refs["tutor"]
        )
        return created.id

    created: uuid.UUID = await _in_uow(url, _create)
    return created


def edit(series_id: uuid.UUID, actor: uuid.UUID, *, version: int = 1) -> Change:
    body = ClassSeriesEdit(
        mode="following",
        from_date=WEEK_3,
        version=version,
        start_time=time(17),
        duration_minutes=60,
        classroom_id=None,
        recurrence=SeriesRecurrence(weekdays=[1, 3]),
    )

    async def _edit(session: AsyncSession) -> None:
        await scheduling.edit_class_series(
            session, series_id, body, horizon_weeks=12, actor_id=actor
        )

    return _edit


def extend(series_id: uuid.UUID) -> Change:
    async def _extend(session: AsyncSession) -> None:
        await scheduling.materialize_series(
            session, series_id, now=NOW, horizon_weeks=12, mode="extend"
        )

    return _extend


def patch_change(
    row_id: uuid.UUID, body: ClassSessionUpdate, actor: uuid.UUID
) -> Change:
    async def _patch(session: AsyncSession) -> None:
        await scheduling.update_class_session(session, row_id, body, actor_id=actor)

    return _patch


def create_change(body: ClassSessionCreate, actor: uuid.UUID) -> Change:
    async def _create(session: AsyncSession) -> None:
        await scheduling.create_class_session(session, body, actor_id=actor)

    return _create


async def _rows(url: str, student_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """The student's occurrence rows, comparable across reference sets:
    (whether the row is on the series created first, rule instant, start,
    state)."""
    rows = await run_sql_async(
        url,
        "SELECT s.original_start, s.starts_at, s.state,"
        " c.split_from_series_id IS NULL AS first_series"
        " FROM class_sessions s JOIN class_series c ON c.id = s.series_id"
        " JOIN class_session_participants p ON p.class_session_id = s.id"
        " WHERE p.student_id = :id ORDER BY s.original_start, s.state",
        {"id": student_id},
    )
    return [(r.first_series, r.original_start, r.starts_at, r.state) for r in rows]


async def _serial(url: str, first: str) -> list[tuple[Any, ...]]:
    refs = await _references(url)
    series_id = await _new_series(url, refs, horizon=4)
    steps = [edit(series_id, refs["tutor"]), extend(series_id)]
    if first == "extend":
        steps.reverse()
    for step in steps:
        await _in_uow(url, step)
    return await _rows(url, refs["student"])


async def test_an_edit_and_the_extension_of_one_series(seeded_url):
    with frozen_clock(NOW):
        expected = await _serial(seeded_url, "edit")
        assert expected == await _serial(seeded_url, "extend")
        starts = [start for _, start, _, _ in expected]
        assert len(starts) == len(set(starts))
        for first in ("edit", "extend"):
            for _ in range(REPEATS):
                refs = await _references(seeded_url)
                series_id = await _new_series(seeded_url, refs, horizon=4)
                changes = {
                    "edit": edit(series_id, refs["tutor"]),
                    "extend": extend(series_id),
                }
                second = "extend" if first == "edit" else "edit"

                outcomes = await race(seeded_url, changes[first], changes[second])

                assert outcomes == ["ok", "ok"]
                assert await _rows(seeded_url, refs["student"]) == expected


async def test_two_edits_with_one_version(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            series_id = await _new_series(seeded_url, refs, horizon=12)

            outcomes = await race(
                seeded_url,
                edit(series_id, refs["tutor"]),
                edit(series_id, refs["tutor"]),
            )

            assert outcomes == ["ok", "version_conflict"]


async def test_a_patch_and_a_one_off_create_for_one_student(seeded_url):
    start = datetime.combine(MONDAY + timedelta(weeks=1), time(18), tzinfo=MX)
    with frozen_clock(NOW):
        for order in ("patch_first", "create_first"):
            for _ in range(REPEATS):
                refs = await _references(seeded_url)
                series_id = await _new_series(seeded_url, refs, horizon=12)
                [row] = await run_sql_async(
                    seeded_url,
                    "SELECT id, version FROM class_sessions WHERE series_id = :id"
                    " AND original_start = :at",
                    {
                        "id": series_id,
                        "at": datetime.combine(start.date(), time(16), tzinfo=MX),
                    },
                )
                update = ClassSessionUpdate(
                    version=row.version,
                    starts_at=start,
                    ends_at=start + timedelta(hours=1),
                )
                one_off = ClassSessionCreate(
                    kind="individual",
                    student_id=refs["student"],
                    subject_id=refs["subject"],
                    starts_at=start,
                    ends_at=start + timedelta(hours=1),
                )

                changes = (
                    patch_change(row.id, update, refs["tutor"]),
                    create_change(one_off, refs["tutor"]),
                )
                pair = changes if order == "patch_first" else changes[::-1]
                outcomes = await race(seeded_url, *pair)

                assert outcomes == ["ok", "student_overlap"]
                stored = await run_sql_async(
                    seeded_url,
                    "SELECT count(*) AS n FROM class_sessions s"
                    " JOIN class_session_participants p ON p.class_session_id = s.id"
                    " WHERE p.student_id = :id AND s.starts_at = :at",
                    {"id": refs["student"], "at": start},
                )
                assert stored[0].n == 1
