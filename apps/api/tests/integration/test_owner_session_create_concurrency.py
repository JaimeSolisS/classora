"""Concurrent one-off group and course session creates (#592 AC5; the #47 D3
two-connection pattern): transaction A makes its change and keeps its
transaction open for a moment; B starts once A's rows are flushed and
uncommitted.

- **An enrollment, then the create:** B read the roster before A committed,
  waits on the group (or cohort) row and reads it again:
  ``roster_changed``; nothing is stored.
- **The create, then an enrollment:** B waits on the group row; the
  enrollment then adds its student to the new one-off.
- **Two creates sharing a student** at overlapping times: the per-student
  lock; exactly one lands (``student_overlap``).
- **A withdrawal and a create**, in both orders: no deadlock. A withdrawal
  first makes the create ``roster_changed``; a create first is followed by
  the withdrawal, which removes the student from the one-off.

Each case runs 5 times, on new references.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.uow import UnitOfWork
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import CourseSessionCreate, GroupSessionCreate
from tests.conftest import frozen_clock
from tests.integration import test_cohort_enrollment_concurrency as cohorts
from tests.integration.conftest import run_sql_async
from tests.integration.test_class_session_create_concurrency import (
    REPEATS,
    _references,
)
from tests.integration.test_group_enrollment_concurrency import (
    MONDAY,
    MX,
    NOW,
    _active,
    _group,
    enroll,
    in_b,
    race,
    withdraw,
)

pytestmark = pytest.mark.anyio

TUESDAY_16 = datetime.combine(MONDAY + timedelta(days=1), time(16), tzinfo=MX)

Change = Callable[[AsyncSession], Awaitable[Any]]


def one_off(
    refs: dict[str, uuid.UUID],
    group_id: uuid.UUID,
    starts_at: datetime = TUESDAY_16,
    room: str | None = None,
) -> Change:
    body = GroupSessionCreate(
        kind="group",
        group_id=group_id,
        classroom_id=None if room is None else refs[room],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=60),
    )

    async def _create(session: AsyncSession) -> None:
        await scheduling.create_owner_session(session, body, actor_id=refs["tutor"])

    return _create


async def _one_offs(url: str, column: str, owner_id: uuid.UUID) -> dict[Any, set[Any]]:
    found: dict[Any, set[Any]] = {}
    sql = (
        "SELECT s.id, p.student_id FROM class_sessions s"  # noqa: S608 - fixed names
        " LEFT JOIN class_session_participants p ON p.class_session_id = s.id"
        f" WHERE s.{column} = :id AND s.series_id IS NULL"
    )
    for row in await run_sql_async(url, sql, {"id": owner_id}):
        found.setdefault(row.id, set())
        if row.student_id is not None:
            found[row.id].add(row.student_id)
    return found


async def test_an_enrollment_committed_after_the_roster_read_is_roster_changed(
    seeded_url,
):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, ["other_student"], 5)

            outcomes = await race(
                seeded_url,
                enroll(refs, group_id, "student"),
                in_b(one_off(refs, group_id)),
            )

            assert outcomes == ["ok", "roster_changed"]
            assert await _one_offs(seeded_url, "group_id", group_id) == {}


async def test_a_cohort_enrollment_committed_after_the_roster_read_is_roster_changed(
    seeded_url,
):
    with frozen_clock(cohorts.NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            cohort_id = await cohorts._cohort(
                seeded_url, refs, max_students=5, students=("other_student",)
            )
            body = CourseSessionCreate(
                kind="course",
                cohort_id=cohort_id,
                starts_at=TUESDAY_16,
                ends_at=TUESDAY_16 + timedelta(minutes=60),
            )

            async def create(uow: UnitOfWork, body=body, actor=refs["tutor"]) -> None:
                await scheduling.create_owner_session(uow.session, body, actor_id=actor)

            outcomes, _ = await cohorts.race(
                seeded_url,
                cohorts.enroll(refs, cohort_id, "student"),
                cohorts.in_b(create),
            )

            assert outcomes == ["ok", "roster_changed"]
            assert await _one_offs(seeded_url, "cohort_id", cohort_id) == {}


async def test_an_enrollment_after_the_create_joins_the_one_off(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, ["other_student"], 5)

            outcomes = await race(
                seeded_url,
                one_off(refs, group_id),
                in_b(enroll(refs, group_id, "student")),
            )

            assert outcomes == ["ok", "ok"]
            [students] = (await _one_offs(seeded_url, "group_id", group_id)).values()
            assert students == {refs["student"], refs["other_student"]}


async def test_two_overlapping_creates_sharing_a_student(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, ["student"], 5)

            outcomes = await race(
                seeded_url,
                one_off(refs, group_id, room="room"),
                in_b(
                    one_off(
                        refs,
                        group_id,
                        TUESDAY_16 + timedelta(minutes=30),
                        room="other_room",
                    )
                ),
            )

            assert outcomes == ["ok", "student_overlap"]
            assert len(await _one_offs(seeded_url, "group_id", group_id)) == 1


async def test_a_withdrawal_and_a_create_never_deadlock(seeded_url):
    with frozen_clock(NOW):
        for _ in range(REPEATS):
            # The withdrawal first: the create's roster is stale.
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, ["student", "other_student"], 5)
            [period] = [
                row
                for row in await _active(seeded_url, group_id)
                if row.student_id == refs["student"]
            ]

            outcomes = await race(
                seeded_url,
                withdraw(refs, group_id, period.id),
                in_b(one_off(refs, group_id)),
            )

            assert outcomes == ["ok", "roster_changed"]

            # The create first: the withdrawal removes the student from it.
            refs = await _references(seeded_url)
            group_id = await _group(seeded_url, refs, ["student", "other_student"], 5)
            [period] = [
                row
                for row in await _active(seeded_url, group_id)
                if row.student_id == refs["student"]
            ]

            outcomes = await race(
                seeded_url,
                one_off(refs, group_id),
                in_b(withdraw(refs, group_id, period.id)),
            )

            assert outcomes == ["ok", "ok"]
            [students] = (await _one_offs(seeded_url, "group_id", group_id)).values()
            assert students == {refs["other_student"]}
