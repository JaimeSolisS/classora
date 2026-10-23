"""Concurrent session requests (#59 H4; S5, C2 steps 5, 8, 9, 11), with two
connections (the #47 / #48 pattern).

Transaction A creates a request and keeps its transaction open for a
moment; transaction B starts once A's rows are flushed and uncommitted, and
waits on the per-student advisory lock until A commits. Its checks then see
A's request:

- **Two overlapping requests for one student:** one ``201``, one ``409
  session_request_overlap`` (the ``EXCLUDE`` constraint is the backstop).
- **Four pending, two concurrent creates:** one ``201``, one ``409
  session_request_limit_reached``. Without the lock both would read four
  and both would be stored.

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

from app.core.authz import Principal
from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.uow import UnitOfWork
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.request_schemas import SessionRequestCreate
from app.modules.staff.models import TutorAvailabilityWindow, TutorQualification
from tests.factories.base import persist
from tests.factories.families import GuardianFactory, link_guardian, persist_student
from tests.factories.identity import persist_user
from tests.factories.scheduling import persist_subject
from tests.integration.conftest import run_sql_async, short_lived_engine

pytestmark = pytest.mark.anyio

Change = Callable[[AsyncSession], Awaitable[Any]]

HOLD_SECONDS = 0.5
REPEATS = 5
MX = ZoneInfo("America/Mexico_City")
START = datetime(2031, 1, 8, 10, 0, tzinfo=MX)  # a Wednesday


async def _outcome(engine: Any, change: Change, after_change: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow.session)
            if after_change is not None:
                await after_change()
    except AppError as exc:
        return exc.code
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


async def _references(url: str) -> dict[str, Any]:
    """A guardian linked to a minor child, a subject and a tutor free all
    week and qualified for it."""
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            session = uow.session
            subject = (await persist_subject(session)).id
            tutor = await persist_user(session, roles=("tutor",))
            session.add_all(
                TutorAvailabilityWindow(
                    tutor_user_id=tutor.id,
                    weekday=day,
                    starts_at=time(0),
                    ends_at=time(23, 59),
                )
                for day in range(1, 8)
            )
            session.add(
                TutorQualification(
                    tutor_user_id=tutor.id,
                    subject_id=subject,
                    status="approved",
                    decided_at=START - timedelta(days=400),
                    decided_by_user_id=tutor.id,
                )
            )
            user = await persist_user(session, roles=("guardian",))
            student = await persist_student(session, contacts=0)
            guardian = await persist(session, GuardianFactory, user_id=user.id)
            await link_guardian(session, student, guardian)
            return {
                "subject": subject,
                "student": student.id,
                "principal": Principal(
                    user_id=user.id,
                    roles=frozenset({"guardian"}),
                    permissions=frozenset(),
                ),
            }
    finally:
        await engine.dispose()


def create(refs: dict[str, Any], starts_at: datetime) -> Change:
    body = SessionRequestCreate(
        student_id=refs["student"],
        subject_id=refs["subject"],
        starts_at=starts_at,
        duration_minutes=60,
    )

    async def _create(session: AsyncSession) -> None:
        await scheduling.create_session_request(session, refs["principal"], body)

    return _create


async def pending_for(url: str, student_id: uuid.UUID) -> int:
    rows = await run_sql_async(
        url,
        "SELECT count(*) AS n FROM session_requests"
        " WHERE student_id = :id AND status = 'pending'",
        {"id": student_id},
    )
    return int(rows[0].n)


async def test_one_of_two_overlapping_requests_is_stored(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)

        outcomes = await race(
            seeded_url,
            create(refs, START),
            create(refs, START + timedelta(minutes=30)),
        )

        assert outcomes == ["ok", "session_request_overlap"]
        assert await pending_for(seeded_url, refs["student"]) == 1


async def test_the_pending_cap_holds_under_concurrency(seeded_url):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        engine = short_lived_engine(seeded_url)
        try:
            for week in range(1, 5):
                async with UnitOfWork(create_sessionmaker(engine)) as uow:
                    await create(refs, START + timedelta(days=7 * week))(uow.session)
        finally:
            await engine.dispose()
        assert scheduling.MAX_PENDING_REQUESTS_PER_STUDENT == 5

        outcomes = await race(
            seeded_url,
            create(refs, START),
            create(refs, START + timedelta(hours=2)),
        )

        assert outcomes == ["ok", "session_request_limit_reached"]
        assert await pending_for(seeded_url, refs["student"]) == 5
        rows = await run_sql_async(
            seeded_url,
            "SELECT count(*) AS n FROM approval_requests"
            " WHERE request_type = 'scheduling.session_request'"
            " AND subject_id IN"
            " (SELECT id FROM session_requests WHERE student_id = :s)",
            {"s": refs["student"]},
        )
        assert int(rows[0].n) == 5
