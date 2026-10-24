"""Concurrent cohort enrollment writes (#66 H2, J2; the #47 D3 two-connection
pattern): transaction A makes its change and keeps its transaction open for
a moment; B starts once A's rows are flushed and uncommitted.

- **The last seat:** the cohort row ``FOR UPDATE`` makes B wait until A
  commits; B then counts A's enrollment: ``group_capacity_exceeded``.
- **One student twice:** B waits on the student lock, then sees the active
  enrollment: ``already_enrolled``.
- **An enrollment and a one-off create** for one student at an overlapping
  time: the per-student lock; exactly one lands.
- **An enrollment and the nightly extension** of the cohort's series: no
  deadlock and no ``IntegrityError``; the new occurrences have the student
  exactly once (the extension re-reads the participants under the locks
  and retries, #56 C2).
- **An enrollment and a ``PATCH`` lowering the maximum** to the current
  count: serialized by the cohort row; either the enrollment lands and the
  ``PATCH`` is ``max_students_below_enrolled``, or the other way round.
- **An enrollment and a withdrawal** of two students: both land.
- **The cohort lock in the #54 edits** (the #65 D6 follow-up): while A
  holds the cohort row ``FOR UPDATE``, a series edit and a session
  ``PATCH`` of the cohort finish only after A commits.

Each case runs 5 times, on new references.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.uow import UnitOfWork
from app.modules.courses import service as courses
from app.modules.courses.schemas import CohortUpdate
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import (
    ClassSeriesEdit,
    ClassSessionUpdate,
    SeriesRecurrence,
)
from app.workflows import cohort_enrollment, cohorts
from app.workflows.cohort_schemas import (
    CohortCreate,
    CohortEnrollmentCreate,
    CohortRecurrence,
    CohortWithdrawal,
)
from tests.conftest import frozen_clock
from tests.factories.courses import persist_course_template
from tests.integration.conftest import run_sql_async, settings_for, short_lived_engine
from tests.integration.test_class_session_create_concurrency import (
    HOLD_SECONDS,
    REPEATS,
    _references,
    create,
)
from tests.integration.test_group_enrollment_concurrency import (
    _extension_with_retry,
    _job,
)

pytestmark = pytest.mark.anyio

MX = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 1, 5, 12, 0, tzinfo=MX)  # Sunday
MONDAY = date(2031, 1, 6)
HORIZON = 12

UowChange = Callable[[UnitOfWork], Awaitable[Any]]


async def _outcome(engine: Any, change: UowChange, after: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow)
            if after is not None:
                await after()
    except scheduling.SchedulingConflictError as exc:
        return ",".join(conflict["code"] for conflict in exc.conflicts or [])
    except AppError as exc:
        if exc.code == "validation_error" and exc.errors:
            return ",".join(error["code"] for error in exc.errors)
        return exc.code
    return "ok"


async def race(
    url: str, first: UowChange, second: Callable[[Any], Awaitable[str]]
) -> tuple[list[str], float]:
    """``first`` (A, held open after its change) and ``second`` (B, given
    its own engine, started once A's change is flushed); the outcomes and
    how long B finished after A committed (negative: before)."""
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
            return await second(engine_b)
        finally:
            ends["b"] = loop.time()

    try:
        outcomes = list(await asyncio.gather(run_a(), run_b()))
    finally:
        await engine_a.dispose()
        await engine_b.dispose()
    return outcomes, ends["b"] - ends["a"]


def in_b(change: UowChange) -> Callable[[Any], Awaitable[str]]:
    async def _run(engine: Any) -> str:
        return await _outcome(engine, change)

    return _run


async def _in_uow(url: str, change: UowChange) -> Any:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return await change(uow)
    finally:
        await engine.dispose()


async def _cohort(
    url: str,
    refs: dict[str, uuid.UUID],
    *,
    max_students: int,
    students: tuple[str, ...] = (),
    session_count: int = 10,
    weekdays: tuple[int, ...] = (1, 3),
) -> uuid.UUID:
    """A cohort of a new template (no tutor) in ``refs["room"]``, with
    ``students`` enrolled."""

    async def _make(uow: UnitOfWork) -> uuid.UUID:
        template = await persist_course_template(
            uow.session,
            subject_id=refs["subject"],
            session_count=session_count,
            session_minutes=60,
        )
        body = CohortCreate(
            name=f"Cohorte {uuid.uuid4().hex[:10]}",
            template_id=template.id,
            subject_id=refs["subject"],
            max_students=max_students,
            classroom_id=refs["room"],
            starts_on=MONDAY,
            start_time=time(16),
            recurrence=CohortRecurrence(weekdays=list(weekdays)),
        )
        cohort_id: uuid.UUID = await cohorts.create_cohort(
            uow.session, body, horizon_weeks=HORIZON, actor_id=refs["tutor"]
        )
        return cohort_id

    cohort_id: uuid.UUID = await _in_uow(url, _make)
    for student in students:
        await _in_uow(url, enroll(refs, cohort_id, student))
    return cohort_id


def enroll(refs: dict[str, uuid.UUID], cohort_id: uuid.UUID, student: str) -> UowChange:
    async def _enroll(uow: UnitOfWork) -> None:
        await cohort_enrollment.enroll_in_cohort(
            uow,
            cohort_id,
            CohortEnrollmentCreate(student_id=refs[student]),
            actor_id=refs["tutor"],
        )

    return _enroll


def withdraw(
    refs: dict[str, uuid.UUID], cohort_id: uuid.UUID, enrollment_id: uuid.UUID
) -> UowChange:
    async def _withdraw(uow: UnitOfWork) -> None:
        await cohort_enrollment.withdraw_from_cohort(
            uow,
            cohort_id,
            enrollment_id,
            CohortWithdrawal(version=1),
            actor_id=refs["tutor"],
        )

    return _withdraw


def lower_maximum(cohort_id: uuid.UUID, maximum: int) -> UowChange:
    async def _patch(uow: UnitOfWork) -> None:
        await courses.update_cohort(
            uow.session, cohort_id, CohortUpdate(version=1, max_students=maximum)
        )

    return _patch


def hold_cohort(cohort_id: uuid.UUID) -> UowChange:
    async def _hold(uow: UnitOfWork) -> None:
        assert await courses.lock_cohort(uow.session, cohort_id) is not None

    return _hold


async def _active(url: str, cohort_id: uuid.UUID) -> list[Any]:
    return await run_sql_async(
        url,
        "SELECT id, student_id FROM cohort_enrollments"
        " WHERE cohort_id = :id AND withdrawn_at IS NULL",
        {"id": cohort_id},
    )


async def _rows(url: str, cohort_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    found: dict[uuid.UUID, list[uuid.UUID]] = {}
    for row in await run_sql_async(
        url,
        "SELECT s.id, p.student_id FROM class_sessions s"
        " LEFT JOIN class_session_participants p ON p.class_session_id = s.id"
        " WHERE s.cohort_id = :id",
        {"id": cohort_id},
    ):
        found.setdefault(row.id, [])
        if row.student_id is not None:
            found[row.id].append(row.student_id)
    return found


async def _series(url: str, cohort_id: uuid.UUID) -> Any:
    [row] = await run_sql_async(
        url,
        "SELECT id, version, count FROM class_series WHERE cohort_id = :id",
        {"id": cohort_id},
    )
    return row


async def test_two_students_race_for_the_last_seat(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            cohort_id = await _cohort(seeded_url, refs, max_students=1)

            outcomes, _ = await race(
                seeded_url,
                enroll(refs, cohort_id, "student"),
                in_b(enroll(refs, cohort_id, "other_student")),
            )

            assert outcomes == ["ok", "group_capacity_exceeded"]
            assert len(await _active(seeded_url, cohort_id)) == 1


async def test_one_student_enrolled_twice_at_once(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            cohort_id = await _cohort(seeded_url, refs, max_students=5)

            outcomes, _ = await race(
                seeded_url,
                enroll(refs, cohort_id, "student"),
                in_b(enroll(refs, cohort_id, "student")),
            )

            assert outcomes == ["ok", "already_enrolled"]
            assert len(await _active(seeded_url, cohort_id)) == 1


def _one_off(refs: dict[str, uuid.UUID], starts_at: datetime) -> UowChange:
    change = create(refs, "student", "other_tutor", "other_room", starts_at)

    async def _create(uow: UnitOfWork) -> None:
        await change(uow.session)

    return _create


async def test_an_enrollment_and_a_one_off_for_one_student(seeded_url):
    one_off_start = datetime.combine(MONDAY, time(16, 30), tzinfo=MX)
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            cohort_id = await _cohort(seeded_url, refs, max_students=5)

            first, _ = await race(
                seeded_url,
                enroll(refs, cohort_id, "student"),
                in_b(_one_off(refs, one_off_start)),
            )

            assert first == ["ok", "student_overlap"]

            refs = await _references(seeded_url)
            cohort_id = await _cohort(seeded_url, refs, max_students=5)
            second, _ = await race(
                seeded_url,
                _one_off(refs, one_off_start),
                in_b(enroll(refs, cohort_id, "student")),
            )

            assert second == ["ok", "student_overlap"]
            assert await _active(seeded_url, cohort_id) == []


async def test_an_enrollment_during_the_nightly_extension(seeded_url):
    later = NOW + timedelta(weeks=2)
    settings = settings_for(seeded_url)
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            # The extension first, then the enrollment (B waits on the cohort).
            refs = await _references(seeded_url)
            cohort_id = await _cohort(
                seeded_url,
                refs,
                max_students=5,
                students=("other_student",),
                session_count=40,
            )
            series = await _series(seeded_url, cohort_id)

            async def extend(uow: UnitOfWork, series_id=series.id) -> None:
                await scheduling.materialize_series(
                    uow.session,
                    series_id,
                    now=later,
                    horizon_weeks=HORIZON,
                    mode="extend",
                )

            outcomes, _ = await race(
                seeded_url, extend, in_b(enroll(refs, cohort_id, "student"))
            )

            rows = await _rows(seeded_url, cohort_id)
            assert outcomes == ["ok", "ok"]
            assert len(rows) == 28
            assert all(
                students.count(refs["student"]) == 1 for students in rows.values()
            )

            # The enrollment first, then the extension: it read the
            # participants before the enrollment committed, re-reads them
            # under the cohort lock and is retried (#56 C2); once through
            # the extension alone, once through the job's per-series unit.
            for through_job in (False, True):
                refs = await _references(seeded_url)
                cohort_id = await _cohort(
                    seeded_url,
                    refs,
                    max_students=5,
                    students=("other_student",),
                    session_count=40,
                )
                series = await _series(seeded_url, cohort_id)
                before = set(await _rows(seeded_url, cohort_id))
                job = _job if through_job else _extension_with_retry

                outcomes, _ = await race(
                    seeded_url,
                    enroll(refs, cohort_id, "student"),
                    job(series.id, later, settings),
                )

                rows = await _rows(seeded_url, cohort_id)
                new_rows = [ids for row_id, ids in rows.items() if row_id not in before]
                assert outcomes == ["ok", "ok" if through_job else "retried"]
                assert len(new_rows) == 4
                assert all(
                    sorted(ids) == sorted([refs["student"], refs["other_student"]])
                    for ids in new_rows
                )


async def test_an_enrollment_and_a_patch_lowering_the_maximum(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            # The enrollment first: the PATCH counts it.
            refs = await _references(seeded_url)
            cohort_id = await _cohort(
                seeded_url, refs, max_students=2, students=("other_student",)
            )

            first, _ = await race(
                seeded_url,
                enroll(refs, cohort_id, "student"),
                in_b(lower_maximum(cohort_id, 1)),
            )

            assert first == ["ok", "max_students_below_enrolled"]
            assert len(await _active(seeded_url, cohort_id)) == 2

            # The PATCH first: the enrollment sees the lower maximum.
            refs = await _references(seeded_url)
            cohort_id = await _cohort(
                seeded_url, refs, max_students=2, students=("other_student",)
            )

            second, _ = await race(
                seeded_url,
                lower_maximum(cohort_id, 1),
                in_b(enroll(refs, cohort_id, "student")),
            )

            assert second == ["ok", "group_capacity_exceeded"]
            assert len(await _active(seeded_url, cohort_id)) == 1


async def test_an_enrollment_and_a_withdrawal_of_two_students(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            cohort_id = await _cohort(
                seeded_url, refs, max_students=5, students=("other_student",)
            )
            [current] = await _active(seeded_url, cohort_id)

            outcomes, _ = await race(
                seeded_url,
                enroll(refs, cohort_id, "student"),
                in_b(withdraw(refs, cohort_id, current.id)),
            )

            rows = await _rows(seeded_url, cohort_id)
            assert outcomes == ["ok", "ok"]
            assert [r.student_id for r in await _active(seeded_url, cohort_id)] == [
                refs["student"]
            ]
            assert rows
            assert all(students == [refs["student"]] for students in rows.values())


# --- The cohort lock in the #54 edits (orchestrator scope note on #66) -------------


async def test_a_series_edit_waits_for_the_cohort_lock(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            cohort_id = await _cohort(
                seeded_url, refs, max_students=5, students=("student",)
            )
            series = await _series(seeded_url, cohort_id)

            async def edit(uow: UnitOfWork, series=series, refs=refs) -> None:
                # Only the room changes: the rows are updated in place and
                # nothing new references the cohort, so only the explicit
                # cohort lock makes B wait.
                await scheduling.edit_class_series(
                    uow.session,
                    series.id,
                    ClassSeriesEdit(
                        mode="all",
                        from_date=None,
                        version=series.version,
                        start_time=time(16),
                        duration_minutes=60,
                        classroom_id=refs["other_room"],
                        recurrence=SeriesRecurrence(
                            weekdays=[1, 3], count=series.count
                        ),
                    ),
                    horizon_weeks=HORIZON,
                    actor_id=refs["tutor"],
                )

            outcomes, lag = await race(seeded_url, hold_cohort(cohort_id), in_b(edit))

            assert outcomes == ["ok", "ok"]
            # B finished only after A committed (it waited on the cohort row).
            assert lag >= 0


async def test_a_session_patch_waits_for_the_cohort_lock(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            cohort_id = await _cohort(
                seeded_url, refs, max_students=5, students=("student",)
            )
            [row] = await run_sql_async(
                seeded_url,
                "SELECT id, version, starts_at, ends_at FROM class_sessions"
                " WHERE cohort_id = :id ORDER BY starts_at DESC LIMIT 1",
                {"id": cohort_id},
            )

            async def move(uow: UnitOfWork, row=row, refs=refs) -> None:
                await scheduling.update_class_session(
                    uow.session,
                    row.id,
                    ClassSessionUpdate(
                        version=row.version,
                        starts_at=row.starts_at + timedelta(hours=1),
                        ends_at=row.ends_at + timedelta(hours=1),
                    ),
                    actor_id=refs["tutor"],
                )

            outcomes, lag = await race(seeded_url, hold_cohort(cohort_id), in_b(move))

            assert outcomes == ["ok", "ok"]
            assert lag >= 0
