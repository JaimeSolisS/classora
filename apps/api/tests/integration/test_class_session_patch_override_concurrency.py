"""Two moves of one session with one version, one of them with an override
(#58 G4; the #47 D3 two-connection pattern of
:mod:`~tests.integration.test_class_series_edit_concurrency`).

Transaction A flushes its move and keeps its transaction open for a moment;
transaction B starts then. Both take the student lock, then the row ``FOR
UPDATE``, so B waits for A's commit and then fails its version check:
exactly one ``200`` and one ``409 version_conflict``, whichever carries the
override, and at most one override row (none when the override loses).

Each order runs 5 times, on new references.
"""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import (
    ClassSessionCreate,
    ClassSessionUpdate,
    SessionOverride,
)
from tests.integration.conftest import run_sql_async
from tests.integration.test_class_series_edit_concurrency import (
    _in_uow,
    patch_change,
    race,
)
from tests.integration.test_class_session_create_concurrency import (
    REPEATS,
    _references,
)

pytestmark = pytest.mark.anyio

MX = ZoneInfo("America/Mexico_City")
MONDAY = date(2031, 3, 3)
SUNDAY = MONDAY + timedelta(days=6)


def at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=MX)


async def _one_off(url: str, refs: dict[str, uuid.UUID]) -> uuid.UUID:
    body = ClassSessionCreate(
        kind="individual",
        student_id=refs["student"],
        subject_id=refs["subject"],
        starts_at=at(MONDAY, "10:00"),
        ends_at=at(MONDAY, "11:00"),
    )

    async def _create(session: AsyncSession) -> uuid.UUID:
        created = await scheduling.create_class_session(
            session, body, actor_id=refs["tutor"]
        )
        return created.id

    created: uuid.UUID = await _in_uow(url, _create)
    return created


@pytest.mark.parametrize("first", ["override", "plain"])
async def test_two_moves_with_one_version_one_overridden(seeded_url, first):
    for _ in range(REPEATS):
        refs = await _references(seeded_url)
        session_id = await _one_off(seeded_url, refs)
        overridden = ClassSessionUpdate(
            version=1,
            starts_at=at(SUNDAY, "10:00"),
            ends_at=at(SUNDAY, "11:00"),
            override=SessionOverride(
                codes=["outside_operating_hours"], reason="La familia lo pidió"
            ),
        )
        plain = ClassSessionUpdate(
            version=1, starts_at=at(MONDAY, "12:00"), ends_at=at(MONDAY, "13:00")
        )
        changes = {
            "override": patch_change(session_id, overridden, refs["tutor"]),
            "plain": patch_change(session_id, plain, refs["tutor"]),
        }
        second = "plain" if first == "override" else "override"

        outcomes = await race(seeded_url, changes[first], changes[second])

        assert outcomes == ["ok", "version_conflict"]
        [row] = await run_sql_async(
            seeded_url,
            "SELECT s.version, s.starts_at,"
            " (SELECT count(*) FROM class_session_overrides o"
            "  WHERE o.class_session_id = s.id) AS overrides"
            " FROM class_sessions s WHERE s.id = :id",
            {"id": session_id},
        )
        assert row.version == 2
        if first == "override":
            assert (row.starts_at, row.overrides) == (at(SUNDAY, "10:00"), 1)
        else:
            assert (row.starts_at, row.overrides) == (at(MONDAY, "12:00"), 0)
