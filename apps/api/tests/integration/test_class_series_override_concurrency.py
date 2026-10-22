"""Concurrent series overrides (#236 I4; the #47 D3 two-connection pattern
of :mod:`~tests.integration.test_class_series_edit_concurrency`).

- **A series create with an override and a one-off create that takes an
  overridden occurrence's tutor slot** (different students, so only the
  tutor ``EXCLUDE`` serializes them): exactly one is stored, and an accepted
  override never covers the new overlap.
- **The conflict-override route and the nightly extension of the same
  series** take the same lock order (students, zone, series): no deadlock,
  both succeed, no row is duplicated.

Each case runs 5 times, in both orders, on new references; the clock is
frozen on a Sunday 12:00 (Mexico City), and Sundays are closed.
"""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.identity.permissions import OWNER
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import (
    ClassSeriesCreate,
    ClassSessionCreate,
    ConflictOverrideCreate,
    OverridePair,
    SeriesOverride,
    SeriesRecurrence,
    SessionOverride,
)
from tests.conftest import frozen_clock
from tests.integration.conftest import run_sql_async
from tests.integration.test_class_series_edit_concurrency import (
    Change,
    _in_uow,
    race,
)
from tests.integration.test_class_session_create_concurrency import (
    REPEATS,
    _references,
)

pytestmark = pytest.mark.anyio

MX = ZoneInfo("America/Mexico_City")
NOW = datetime(2031, 1, 5, 12, 0, tzinfo=MX)  # Sunday
SUNDAY = date(2031, 1, 12)
HOURS = "outside_operating_hours"
REASON = "Domingos acordados"


def sunday(n: int) -> datetime:
    return datetime.combine(SUNDAY + timedelta(weeks=n - 1), time(10), tzinfo=MX)


def series_body(refs: dict[str, uuid.UUID], *, weeks: int | None) -> ClassSeriesCreate:
    return ClassSeriesCreate(
        kind="individual",
        student_id=refs["student"],
        subject_id=refs["subject"],
        tutor_user_id=refs["tutor"],
        starts_on=SUNDAY,
        start_time=time(10),
        duration_minutes=60,
        recurrence=SeriesRecurrence(weekdays=[7], count=weeks),
        override=SeriesOverride(
            pairs=[
                OverridePair(occurrence_start=sunday(n), code=HOURS)
                for n in range(1, 5)
            ],
            reason=REASON,
        ),
    )


def create_series(body: ClassSeriesCreate, actor: uuid.UUID) -> Change:
    async def _create(session: AsyncSession) -> None:
        await scheduling.create_class_series(
            session, body, horizon_weeks=4, actor_id=actor
        )

    return _create


def one_off(refs: dict[str, uuid.UUID], actor: uuid.UUID) -> Change:
    body = ClassSessionCreate(
        kind="individual",
        student_id=refs["other_student"],
        subject_id=refs["subject"],
        tutor_user_id=refs["tutor"],
        starts_at=sunday(2),
        ends_at=sunday(2) + timedelta(hours=1),
        override=SessionOverride(codes=[HOURS], reason=REASON),
    )

    async def _create(session: AsyncSession) -> None:
        await scheduling.create_class_session(session, body, actor_id=actor)

    return _create


async def tutor_rows_at(url: str, tutor: uuid.UUID, at: datetime) -> int:
    rows = await run_sql_async(
        url,
        "SELECT count(*) AS n FROM class_sessions"
        " WHERE tutor_user_id = :t AND starts_at = :at"
        " AND state IN ('offered', 'confirmed')",
        {"t": tutor, "at": at},
    )
    return int(rows[0].n)


async def overrides_at(url: str, tutor: uuid.UUID, at: datetime) -> int:
    rows = await run_sql_async(
        url,
        "SELECT count(*) AS n FROM class_session_overrides o"
        " JOIN class_sessions s ON s.id = o.class_session_id"
        " WHERE s.tutor_user_id = :t AND s.starts_at = :at",
        {"t": tutor, "at": at},
    )
    return int(rows[0].n)


async def test_an_overridden_series_and_a_one_off_on_its_tutor_slot(seeded_url):
    with frozen_clock(NOW):
        for order in ("series_first", "one_off_first"):
            for _ in range(REPEATS):
                refs = await _references(seeded_url)
                actor = refs["tutor"]
                changes = (
                    create_series(series_body(refs, weeks=4), actor),
                    one_off(refs, actor),
                )
                pair = changes if order == "series_first" else changes[::-1]

                outcomes = await race(seeded_url, *pair)

                assert outcomes == ["ok", "tutor_overlap"], (order, outcomes)
                assert await tutor_rows_at(seeded_url, refs["tutor"], sunday(2)) == 1
                # The winner's own override only: never one for the overlap.
                assert await overrides_at(seeded_url, refs["tutor"], sunday(2)) == 1


def owner(user_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=user_id,
        roles=frozenset({OWNER}),
        permissions=frozenset({"schedule.manage", "schedule.override"}),
    )


async def _conflict_row(url: str, refs: dict[str, uuid.UUID]) -> tuple[Any, Any]:
    """An open-ended Sunday series (overridden, 4-week horizon) whose next
    occurrence the job stored as ``conflict``; returns ``(series id, row)``."""
    body = series_body(refs, weeks=None).model_copy(update={"tutor_user_id": None})

    async def _create(session: AsyncSession) -> uuid.UUID:
        created = await scheduling.create_class_series(
            session, body, horizon_weeks=4, actor_id=refs["tutor"]
        )
        return created.id

    series_id: uuid.UUID = await _in_uow(url, _create)

    async def _extend(session: AsyncSession) -> None:
        await scheduling.materialize_series(
            session, series_id, now=NOW, horizon_weeks=5, mode="extend"
        )

    await _in_uow(url, _extend)
    [row] = await run_sql_async(
        url,
        "SELECT id, version FROM class_sessions WHERE series_id = :id"
        " AND state = 'conflict'",
        {"id": series_id},
    )
    return series_id, row


def conflict_override(row: Any, actor: uuid.UUID) -> Change:
    body = ConflictOverrideCreate(
        version=row.version, override=SessionOverride(codes=[HOURS], reason=REASON)
    )

    async def _override(session: AsyncSession) -> None:
        await scheduling.override_conflict_occurrence(
            session, row.id, body, principal=owner(actor)
        )

    return _override


def extend(series_id: uuid.UUID) -> Change:
    async def _extend(session: AsyncSession) -> None:
        await scheduling.materialize_series(
            session, series_id, now=NOW, horizon_weeks=6, mode="extend"
        )

    return _extend


async def test_the_conflict_override_and_the_nightly_extension(seeded_url):
    with frozen_clock(NOW):
        for first in ("override", "extend"):
            for _ in range(REPEATS):
                refs = await _references(seeded_url)
                series_id, row = await _conflict_row(seeded_url, refs)
                changes = {
                    "override": conflict_override(row, refs["tutor"]),
                    "extend": extend(series_id),
                }
                second = "extend" if first == "override" else "override"

                outcomes = await race(seeded_url, changes[first], changes[second])

                assert outcomes == ["ok", "ok"]
                rows = await run_sql_async(
                    seeded_url,
                    "SELECT original_start, state FROM class_sessions"
                    " WHERE series_id = :id ORDER BY original_start",
                    {"id": series_id},
                )
                starts = [r.original_start for r in rows]
                assert len(starts) == len(set(starts)) == 6
                assert [r.state for r in rows] == ["unassigned"] * 5 + ["conflict"]
