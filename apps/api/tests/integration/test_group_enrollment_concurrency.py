"""Concurrent group writes (#56 J3, F1, C2; the #47 D3 two-connection
pattern): transaction A makes its change and keeps its transaction open for
a moment; B starts once A's rows are flushed and uncommitted.

- **The last seat:** the group row ``FOR UPDATE`` makes B wait until A
  commits; B then counts A's enrollment: ``group_capacity_exceeded``.
- **One student twice:** B waits on the student lock, then sees the active
  enrollment: ``already_enrolled``.
- **An enrollment and a one-off create** for one student: the per-student
  lock; exactly one lands.
- **An enrollment and the nightly extension:** no deadlock and no
  ``IntegrityError``; the new occurrences have the student exactly once
  (the extension re-reads the participants under the locks and retries,
  C2).
- **An enrollment and a withdrawal** of two students: both land.

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
from app.modules.scheduling import service as scheduling
from app.modules.scheduling import tasks
from app.modules.scheduling.group_lock import SeriesParticipantsChangedError
from app.modules.scheduling.schemas import (
    GroupCreate,
    GroupEnrollmentCreate,
    GroupWithdrawal,
    SeriesRecurrence,
)
from tests.conftest import frozen_clock
from tests.integration.conftest import run_sql_async, settings_for, short_lived_engine
from tests.integration.test_class_session_create_concurrency import (
    HOLD_SECONDS,
    REPEATS,
    _references,
    create,
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


async def race(
    url: str, first: Change, second: Callable[[Any], Awaitable[str]]
) -> list[str]:
    """``first`` (A, held open after its change) and ``second`` (B, given its
    own engine, started once A's change is flushed)."""
    engine_a, engine_b = short_lived_engine(url), short_lived_engine(url)
    changed = asyncio.Event()

    async def hold() -> None:
        changed.set()
        await asyncio.sleep(HOLD_SECONDS)

    async def run_a() -> str:
        try:
            return await _outcome(engine_a, first, hold)
        finally:
            changed.set()

    async def run_b() -> str:
        await changed.wait()
        return await second(engine_b)

    try:
        return list(await asyncio.gather(run_a(), run_b()))
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


def in_b(change: Change) -> Callable[[Any], Awaitable[str]]:
    async def _run(engine: Any) -> str:
        return await _outcome(engine, change)

    return _run


async def _group(
    url: str, refs: dict[str, uuid.UUID], students: list[str], max_students: int
) -> uuid.UUID:
    body = GroupCreate(
        name=f"Grupo {uuid.uuid4().hex[:10]}",
        subject_id=refs["subject"],
        max_students=max_students,
        classroom_id=refs["room"],
        starts_on=MONDAY,
        start_time=time(16),
        duration_minutes=60,
        recurrence=SeriesRecurrence(weekdays=[1, 3]),
        student_ids=[refs[s] for s in students],
    )
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            created = await scheduling.create_group(
                uow.session, body, horizon_weeks=HORIZON, actor_id=refs["tutor"]
            )
            return created.group_id
    finally:
        await engine.dispose()


def enroll(refs: dict[str, uuid.UUID], group_id: uuid.UUID, student: str) -> Change:
    async def _enroll(session: AsyncSession) -> None:
        await scheduling.enroll_in_group(
            session,
            group_id,
            GroupEnrollmentCreate(student_id=refs[student]),
            actor_id=refs["tutor"],
        )

    return _enroll


def withdraw(
    refs: dict[str, uuid.UUID], group_id: uuid.UUID, enrollment_id: uuid.UUID
) -> Change:
    async def _withdraw(session: AsyncSession) -> None:
        await scheduling.withdraw_from_group(
            session,
            group_id,
            enrollment_id,
            GroupWithdrawal(version=1),
            actor_id=refs["tutor"],
        )

    return _withdraw


async def _active(url: str, group_id: uuid.UUID) -> list[Any]:
    return await run_sql_async(
        url,
        "SELECT id, student_id FROM group_enrollments"
        " WHERE group_id = :id AND withdrawn_at IS NULL",
        {"id": group_id},
    )


async def _rows(url: str, group_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    found: dict[uuid.UUID, list[uuid.UUID]] = {}
    for row in await run_sql_async(
        url,
        "SELECT s.id, p.student_id FROM class_sessions s"
        " LEFT JOIN class_session_participants p ON p.class_session_id = s.id"
        " WHERE s.group_id = :id",
        {"id": group_id},
    ):
        found.setdefault(row.id, [])
        if row.student_id is not None:
            found[row.id].append(row.student_id)
    return found


async def test_two_students_race_for_the_last_seat(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, [], max_students=1)

            outcomes = await race(
                seeded_url,
                enroll(refs, group_id, "student"),
                in_b(enroll(refs, group_id, "other_student")),
            )

            assert outcomes == ["ok", "group_capacity_exceeded"]
            assert len(await _active(seeded_url, group_id)) == 1


async def test_one_student_enrolled_twice_at_once(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, [], max_students=5)

            outcomes = await race(
                seeded_url,
                enroll(refs, group_id, "student"),
                in_b(enroll(refs, group_id, "student")),
            )

            assert outcomes == ["ok", "already_enrolled"]
            assert len(await _active(seeded_url, group_id)) == 1


async def test_an_enrollment_and_a_one_off_for_one_student(seeded_url):
    one_off_start = datetime.combine(MONDAY, time(16, 30), tzinfo=MX)
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, [], max_students=5)
            one_off = create(
                refs, "student", "other_tutor", "other_room", one_off_start
            )

            first = await race(
                seeded_url, enroll(refs, group_id, "student"), in_b(one_off)
            )

            assert first == ["ok", "student_overlap"]

            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, [], max_students=5)
            one_off = create(
                refs, "student", "other_tutor", "other_room", one_off_start
            )
            second = await race(
                seeded_url, one_off, in_b(enroll(refs, group_id, "student"))
            )

            assert second == ["ok", "student_overlap"]
            assert await _active(seeded_url, group_id) == []


async def _series_id(url: str, group_id: uuid.UUID) -> uuid.UUID:
    [row] = await run_sql_async(
        url, "SELECT id FROM class_series WHERE group_id = :id", {"id": group_id}
    )
    series_id: uuid.UUID = row.id
    return series_id


async def test_an_enrollment_during_the_nightly_extension(seeded_url):
    later = NOW + timedelta(weeks=2)
    settings = settings_for(seeded_url)
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            # The extension first, then the enrollment (B waits on the group).
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, ["other_student"], 5)
            series_id = await _series_id(seeded_url, group_id)

            async def extend(session: AsyncSession, series_id=series_id) -> None:
                await scheduling.materialize_series(
                    session, series_id, now=later, horizon_weeks=HORIZON, mode="extend"
                )

            outcomes = await race(
                seeded_url, extend, in_b(enroll(refs, group_id, "student"))
            )

            rows = await _rows(seeded_url, group_id)
            assert outcomes == ["ok", "ok"]
            assert len(rows) == 28
            assert all(
                students.count(refs["student"]) == 1 for students in rows.values()
            )

            # The enrollment first, then the extension: it read the
            # participants before the enrollment committed, re-reads them
            # under the group lock and is retried (C2); once through the
            # extension alone, once through the job's per-series unit.
            for through_job in (False, True):
                refs = await _references(seeded_url)
                group_id = await _group(seeded_url, refs, ["other_student"], 5)
                series_id = await _series_id(seeded_url, group_id)
                before = set(await _rows(seeded_url, group_id))
                job = _job if through_job else _extension_with_retry

                outcomes = await race(
                    seeded_url,
                    enroll(refs, group_id, "student"),
                    job(series_id, later, settings),
                )

                rows = await _rows(seeded_url, group_id)
                new_rows = [ids for row_id, ids in rows.items() if row_id not in before]
                assert outcomes == ["ok", "ok" if through_job else "retried"]
                assert len(new_rows) == 4
                assert all(
                    sorted(ids) == sorted([refs["student"], refs["other_student"]])
                    for ids in new_rows
                )


def _extension_with_retry(
    series_id: uuid.UUID, now: datetime, settings: Any
) -> Callable[[Any], Awaitable[str]]:
    """``materialize_series(mode="extend")`` alone, retried once on
    :class:`SeriesParticipantsChangedError`: ``"retried"`` when it was."""

    async def _run(engine: Any) -> str:
        for attempt in range(2):
            try:
                async with UnitOfWork(create_sessionmaker(engine)) as uow:
                    await scheduling.materialize_series(
                        uow.session,
                        series_id,
                        now=now,
                        horizon_weeks=HORIZON,
                        mode="extend",
                    )
            except SeriesParticipantsChangedError:
                continue
            return "ok" if attempt == 0 else "retried"
        return "failed"

    return _run


def _job(
    series_id: uuid.UUID, now: datetime, settings: Any
) -> Callable[[Any], Awaitable[str]]:
    """The nightly job's unit for one series (closure reconcile, then the
    extension, with its C2 retry)."""

    async def _run(engine: Any) -> str:
        await tasks._materialize_one(  # noqa: SLF001 - the C2 retry
            create_sessionmaker(engine), series_id, now=now, settings=settings
        )
        return "ok"

    return _run


async def test_an_enrollment_and_a_withdrawal_of_two_students(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, ["other_student"], 5)
            [current] = await _active(seeded_url, group_id)

            outcomes = await race(
                seeded_url,
                enroll(refs, group_id, "student"),
                in_b(withdraw(refs, group_id, current.id)),
            )

            rows = await _rows(seeded_url, group_id)
            assert outcomes == ["ok", "ok"]
            assert [r.student_id for r in await _active(seeded_url, group_id)] == [
                refs["student"]
            ]
            assert all(students == [refs["student"]] for students in rows.values())
