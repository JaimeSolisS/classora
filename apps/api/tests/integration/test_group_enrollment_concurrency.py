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
- **An enrollment and the approve of a ``conflict`` occurrence** (#276 A):
  the approve read the participants before the enrollment committed, waits
  on the series row and re-reads them: ``version_conflict``. A second
  approve checks the new student: ``student_overlap``.
- **The group lock of a series edit and a ``PATCH``** (#276 C2): a raw
  transaction holds the group row (``FOR NO KEY UPDATE``); the edit and the
  ``PATCH`` finish only after it is released. (A writer-vs-writer race
  can't show this: the series row serializes them anyway, #56 QA O2.)

Each case runs 5 times, on new references.
"""

import asyncio
import time as monotonic_clock
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.models import StaleVersionError
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.scheduling import service as scheduling
from app.modules.scheduling import tasks
from app.modules.scheduling.group_lock import SeriesParticipantsChangedError
from app.modules.scheduling.materialize import OCCURRENCE_CONFLICT_TYPE
from app.modules.scheduling.schemas import (
    ClassSeriesEdit,
    ClassSessionUpdate,
    GroupCreate,
    GroupEnrollmentCreate,
    GroupWithdrawal,
    SeriesRecurrence,
)
from app.modules.scheduling.service import OCCURRENCE_CONFLICT_HANDLER
from tests.conftest import frozen_clock
from tests.factories.scheduling import add_participant, persist_class_session
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
    except StaleVersionError:
        return "version_conflict"
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


# --- #276 A: an enrollment and the approve of a conflict occurrence ---------------

WEEK_13 = datetime.combine(MONDAY + timedelta(weeks=12), time(16), tzinfo=MX)
"""The week-13 Monday occurrence: created by the extension one week later."""

APPROVER = Principal(
    user_id=uuid.uuid4(),
    roles=frozenset({"owner"}),
    permissions=frozenset({"schedule.manage"}),
)


async def _in_uow(url: str, change: Change) -> Any:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            return await change(uow.session)
    finally:
        await engine.dispose()


async def _conflict_group(url: str) -> dict[str, Any]:
    """A group (no tutor, ``other_student``, a 10-seat room) whose week-13
    Monday is a ``conflict`` row with a pending request, through a confirmed
    blocker in the group's room (no participant involved); the blocker is
    then cancelled. ``student`` (not enrolled) has a confirmed one-off at
    that time only."""
    refs = await _references(url)
    group_id = await _group(url, refs, ["other_student"], max_students=5)
    series_id = await _series_id(url, group_id)

    async def _sessions(session: AsyncSession) -> uuid.UUID:
        blocker = await persist_class_session(
            session,
            state="confirmed",
            classroom_id=refs["room"],
            starts_at=WEEK_13,
            ends_at=WEEK_13 + timedelta(minutes=60),
            scheduled_minutes=60,
        )
        one_off = await persist_class_session(
            session,
            state="confirmed",
            starts_at=WEEK_13,
            ends_at=WEEK_13 + timedelta(minutes=60),
            scheduled_minutes=60,
        )
        await add_participant(session, one_off, refs["student"])
        return blocker.id

    blocker = await _in_uow(url, _sessions)

    async def _extend(session: AsyncSession) -> None:
        await scheduling.materialize_series(
            session,
            series_id,
            now=NOW + timedelta(weeks=1),
            horizon_weeks=HORIZON,
            mode="extend",
        )

    await _in_uow(url, _extend)
    await run_sql_async(
        url,
        "UPDATE class_sessions SET state = 'cancelled', cancelled_at = now(),"
        " cancellation_reason = 'Cancelada' WHERE id = :id",
        {"id": blocker},
    )
    [row] = await run_sql_async(
        url,
        "SELECT s.id, r.id AS request_id FROM class_sessions s"
        " JOIN approval_requests r ON r.subject_id = s.id"
        " WHERE s.group_id = :id AND s.state = 'conflict'",
        {"id": group_id},
    )
    return {
        "refs": refs,
        "group_id": group_id,
        "session_id": row.id,
        "request_id": row.request_id,
    }


def approve(ctx: dict[str, Any]) -> Change:
    """``OCCURRENCE_CONFLICT_HANDLER.approve`` called directly (the handler
    is registered by the app's wiring, which these tests don't run)."""
    view = administration.ApprovalRequestView(
        id=ctx["request_id"],
        request_type=OCCURRENCE_CONFLICT_TYPE,
        subject_id=ctx["session_id"],
        requester_user_id=None,
        status="pending",
    )

    async def _approve(session: AsyncSession) -> None:
        await OCCURRENCE_CONFLICT_HANDLER.approve(session, view, APPROVER)

    return _approve


async def _conflict_state(url: str, ctx: dict[str, Any]) -> tuple[str, str, set[Any]]:
    [row] = await run_sql_async(
        url,
        "SELECT s.state, r.status FROM class_sessions s"
        " JOIN approval_requests r ON r.subject_id = s.id WHERE s.id = :id",
        {"id": ctx["session_id"]},
    )
    students = {
        r.student_id
        for r in await run_sql_async(
            url,
            "SELECT student_id FROM class_session_participants"
            " WHERE class_session_id = :id",
            {"id": ctx["session_id"]},
        )
    }
    return row.state, row.status, students


async def test_an_enrollment_and_the_approve_of_a_conflict_occurrence(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            ctx = await _conflict_group(seeded_url)
            refs = ctx["refs"]
            everyone = {refs["student"], refs["other_student"]}

            outcomes = await race(
                seeded_url,
                enroll(refs, ctx["group_id"], "student"),
                in_b(approve(ctx)),
            )

            assert outcomes == ["ok", "version_conflict"]
            assert await _conflict_state(seeded_url, ctx) == (
                "conflict",
                "pending",
                everyone,
            )

            # A2: the next approve checks the current participants.
            engine = short_lived_engine(seeded_url)
            try:
                with pytest.raises(scheduling.SchedulingConflictError) as raised:
                    async with UnitOfWork(create_sessionmaker(engine)) as uow:
                        await approve(ctx)(uow.session)
            finally:
                await engine.dispose()
            assert [
                (c["code"], c["details"]["student_id"])
                for c in raised.value.conflicts or []
            ] == [("student_overlap", str(refs["student"]))]
            assert await _conflict_state(seeded_url, ctx) == (
                "conflict",
                "pending",
                everyone,
            )


# --- #276 C2: series edits and PATCH wait on the group row ------------------------

GROUP_HOLD_SECONDS = 1.5
"""How long A holds the group row: well above an unblocked edit's run time."""


async def _hold_group(
    url: str, group_id: uuid.UUID, held: asyncio.Event, released: list[float]
) -> None:
    """A raw transaction: the group row ``FOR NO KEY UPDATE``, held, then
    committed; ``released`` gets the monotonic time just before the commit.

    ``NO KEY``: it blocks ``lock_group``'s ``FOR UPDATE`` but not the
    ``FOR KEY SHARE`` of the foreign-key checks, so a split's new series and
    rows (``group_id``) don't wait on it and only the group lock is
    measured (with ``FOR UPDATE`` the edit waits even without it)."""
    engine = short_lived_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT id FROM class_groups WHERE id = :id FOR NO KEY UPDATE"),
                {"id": group_id},
            )
            held.set()
            await asyncio.sleep(GROUP_HOLD_SECONDS)
            released.append(monotonic_clock.monotonic())
            await conn.commit()
    finally:
        held.set()
        await engine.dispose()


async def _after_group_hold(
    url: str, group_id: uuid.UUID, change: Change
) -> tuple[str, float, float]:
    """B's outcome, the time A released the group and the time B returned."""
    held = asyncio.Event()
    released: list[float] = []
    engine = short_lived_engine(url)

    async def run_b() -> tuple[str, float]:
        await held.wait()
        outcome = await _outcome(engine, change)
        return outcome, monotonic_clock.monotonic()

    try:
        _, (outcome, finished) = await asyncio.gather(
            _hold_group(url, group_id, held, released), run_b()
        )
    finally:
        await engine.dispose()
    return outcome, released[0], finished


def edit_series(
    series_id: uuid.UUID, version: int, refs: dict[str, uuid.UUID]
) -> Change:
    body = ClassSeriesEdit(
        mode="following",
        from_date=MONDAY + timedelta(weeks=2),
        version=version,
        start_time=time(17),
        duration_minutes=60,
        classroom_id=refs["room"],
        recurrence=SeriesRecurrence(weekdays=[1, 3]),
    )

    async def _edit(session: AsyncSession) -> None:
        await scheduling.edit_class_series(
            session, series_id, body, horizon_weeks=HORIZON, actor_id=refs["tutor"]
        )

    return _edit


def patch_occurrence(row: Any, actor: uuid.UUID) -> Change:
    body = ClassSessionUpdate(
        version=row.version,
        starts_at=row.starts_at + timedelta(hours=1),
        ends_at=row.ends_at + timedelta(hours=1),
    )

    async def _patch(session: AsyncSession) -> None:
        await scheduling.update_class_session(session, row.id, body, actor_id=actor)

    return _patch


async def test_a_series_edit_waits_on_the_group_row(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, ["student"], max_students=5)
            [series] = await run_sql_async(
                seeded_url,
                "SELECT id, version FROM class_series WHERE group_id = :id",
                {"id": group_id},
            )

            outcome, released, finished = await _after_group_hold(
                seeded_url,
                group_id,
                edit_series(series.id, series.version, refs),
            )

            assert outcome == "ok"
            assert finished > released


async def test_a_patch_of_a_group_occurrence_waits_on_the_group_row(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, ["student"], max_students=5)
            row = (
                await run_sql_async(
                    seeded_url,
                    "SELECT id, version, starts_at, ends_at FROM class_sessions"
                    " WHERE group_id = :id ORDER BY starts_at LIMIT 1 OFFSET 2",
                    {"id": group_id},
                )
            )[0]

            outcome, released, finished = await _after_group_hold(
                seeded_url, group_id, patch_occurrence(row, refs["tutor"])
            )

            assert outcome == "ok"
            assert finished > released
