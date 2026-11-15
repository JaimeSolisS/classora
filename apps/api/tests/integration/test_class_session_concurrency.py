"""Concurrent class-session inserts (#47 D3; ADR-0005 D1), with two connections.

Transaction A inserts a ``confirmed`` session and holds its transaction open
for a moment; transaction B starts once A's row is flushed, so B's insert
meets A's uncommitted row and waits on the exclusion constraint. A commits,
then B fails. Exactly one of two overlapping sessions for one tutor (or one
room) is ever stored. Each case runs 5 times, on new references.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.models import ClassSession
from tests.factories.scheduling import (
    persist_classroom,
    persist_subject,
    persist_tutor_user,
)
from tests.integration.conftest import run_sql_async, short_lived_engine

pytestmark = pytest.mark.anyio

Change = Callable[[AsyncSession], Awaitable[Any]]

# How long A keeps its transaction open after its insert: long enough for B
# to reach its conflicting insert.
HOLD_SECONDS = 0.5
REPEATS = 5
START = datetime(2026, 3, 2, 16, 0, tzinfo=UTC)


async def _outcome(engine: Any, change: Change, after_change: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow.session)
            if after_change is not None:
                await after_change()
    except scheduling.SessionOverlapError as exc:
        return exc.code
    return "ok"


async def race(url: str, first: Change, second: Change) -> list[str]:
    """Outcomes of ``first`` (transaction A) and ``second`` (transaction B,
    started once A's insert is flushed and still uncommitted)."""
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


async def _references(url: str) -> dict[str, uuid.UUID]:
    engine = short_lived_engine(url)
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            session = uow.session
            return {
                "subject": (await persist_subject(session)).id,
                "tutor": await persist_tutor_user(session),
                "other_tutor": await persist_tutor_user(session),
                "room": (await persist_classroom(session)).id,
                "other_room": (await persist_classroom(session)).id,
            }
    finally:
        await engine.dispose()


def insert(
    subject: uuid.UUID,
    tutor: uuid.UUID,
    room: uuid.UUID,
    starts_at: datetime,
) -> Change:
    async def _insert(session: AsyncSession) -> None:
        session.add(
            ClassSession(
                kind="individual",
                state="confirmed",
                subject_id=subject,
                tutor_user_id=tutor,
                classroom_id=room,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=60),
                scheduled_minutes=60,
            )
        )
        await scheduling.flush_class_sessions(session)

    return _insert


async def stored(url: str, column: str, value: uuid.UUID) -> int:
    rows = await run_sql_async(
        url,
        f"SELECT count(*) AS n FROM class_sessions WHERE {column} = :value",  # noqa: S608 (fixed names)
        {"value": value},
    )
    return int(rows[0].n)


async def test_one_tutor_gets_exactly_one_of_two_overlapping_sessions(head_url):
    for _ in range(REPEATS):
        refs = await _references(head_url)

        outcomes = await race(
            head_url,
            insert(refs["subject"], refs["tutor"], refs["room"], START),
            insert(
                refs["subject"],
                refs["tutor"],
                refs["other_room"],
                START + timedelta(minutes=30),
            ),
        )

        assert outcomes == ["ok", "tutor_overlap"]
        assert await stored(head_url, "tutor_user_id", refs["tutor"]) == 1


async def test_one_room_gets_exactly_one_of_two_overlapping_sessions(head_url):
    for _ in range(REPEATS):
        refs = await _references(head_url)

        outcomes = await race(
            head_url,
            insert(refs["subject"], refs["tutor"], refs["room"], START),
            insert(
                refs["subject"],
                refs["other_tutor"],
                refs["room"],
                START + timedelta(minutes=30),
            ),
        )

        assert outcomes == ["ok", "room_overlap"]
        assert await stored(head_url, "classroom_id", refs["room"]) == 1
