"""Concurrent creates of individual sessions (#48 G3; ADR-0005 D1), with two
connections (the #47 D3 pattern).

Transaction A creates a session and keeps its transaction open for a moment;
transaction B starts once A's rows are flushed and uncommitted.

- **Same student:** B waits on the per-student advisory lock until A
  commits, then its check sees A's session: ``student_overlap``. Without the
  lock B would read before A commits, and both would be stored.
- **Same tutor:** B's pre-check or the ``EXCLUDE`` backstop refuses it:
  ``tutor_overlap``.

Each case runs 5 times, on new references.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import ClassSessionCreate
from app.modules.staff.models import TutorAvailabilityWindow, TutorQualification
from tests.factories.families import persist_student
from tests.factories.identity import persist_user
from tests.factories.scheduling import persist_classroom, persist_subject
from tests.integration.conftest import run_sql_async, short_lived_engine

pytestmark = pytest.mark.anyio

Change = Callable[[AsyncSession], Awaitable[Any]]

HOLD_SECONDS = 0.5
REPEATS = 5
START = datetime(2031, 1, 8, 10, 0, tzinfo=ZoneInfo("America/Mexico_City"))


async def _outcome(engine: Any, change: Change, after_change: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow.session)
            if after_change is not None:
                await after_change()
    except scheduling.SchedulingConflictError as exc:
        return ",".join(conflict["code"] for conflict in exc.conflicts or [])
    return "ok"


async def race(url: str, first: Change, second: Change) -> list[str]:
    """Outcomes of ``first`` (A) and ``second`` (B, started once A's create
    is flushed and still uncommitted)."""
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
        return await _outcome(engine_b, second)

    try:
        return list(await asyncio.gather(run_a(), run_b()))
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


async def _tutor(session: AsyncSession, subject_id: uuid.UUID) -> uuid.UUID:
    """An active tutor, free all week and qualified for the subject."""
    user = await persist_user(session, roles=("tutor",))
    session.add_all(
        TutorAvailabilityWindow(
            tutor_user_id=user.id, weekday=day, starts_at=time(0), ends_at=time(23, 59)
        )
        for day in range(1, 8)
    )
    session.add(
        TutorQualification(
            tutor_user_id=user.id,
            subject_id=subject_id,
            status="approved",
            decided_at=START - timedelta(days=400),
            decided_by_user_id=user.id,
        )
    )
    await session.flush()
    return user.id


async def _references(url: str) -> dict[str, uuid.UUID]:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            session = uow.session
            subject = (await persist_subject(session)).id
            return {
                "subject": subject,
                "student": (await persist_student(session, contacts=0)).id,
                "other_student": (await persist_student(session, contacts=0)).id,
                "tutor": await _tutor(session, subject),
                "other_tutor": await _tutor(session, subject),
                "room": (await persist_classroom(session)).id,
                "other_room": (await persist_classroom(session)).id,
            }
    finally:
        await engine.dispose()


def create(
    refs: dict[str, uuid.UUID], student: str, tutor: str, room: str, starts_at: datetime
) -> Change:
    body = ClassSessionCreate(
        kind="individual",
        student_id=refs[student],
        subject_id=refs["subject"],
        tutor_user_id=refs[tutor],
        classroom_id=refs[room],
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=60),
    )

    async def _create(session: AsyncSession) -> None:
        # No override, so the actor (#49) is never stored.
        await scheduling.create_class_session(session, body, actor_id=refs["tutor"])

    return _create


async def stored_for_student(url: str, student_id: uuid.UUID) -> int:
    rows = await run_sql_async(
        url,
        "SELECT count(*) AS n FROM class_session_participants WHERE student_id = :id",
        {"id": student_id},
    )
    return int(rows[0].n)


async def stored_for_tutor(url: str, tutor_id: uuid.UUID) -> int:
    rows = await run_sql_async(
        url,
        "SELECT count(*) AS n FROM class_sessions WHERE tutor_user_id = :id",
        {"id": tutor_id},
    )
    return int(rows[0].n)


async def test_one_student_gets_exactly_one_of_two_overlapping_sessions(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)

        outcomes = await race(
            seeded_url,
            create(refs, "student", "tutor", "room", START),
            create(
                refs,
                "student",
                "other_tutor",
                "other_room",
                START + timedelta(minutes=30),
            ),
        )

        assert outcomes == ["ok", "student_overlap"]
        assert await stored_for_student(seeded_url, refs["student"]) == 1


async def test_one_tutor_gets_exactly_one_of_two_overlapping_sessions(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)

        outcomes = await race(
            seeded_url,
            create(refs, "student", "tutor", "room", START),
            create(
                refs,
                "other_student",
                "tutor",
                "other_room",
                START + timedelta(minutes=30),
            ),
        )

        assert outcomes == ["ok", "tutor_overlap"]
        assert await stored_for_tutor(seeded_url, refs["tutor"]) == 1
