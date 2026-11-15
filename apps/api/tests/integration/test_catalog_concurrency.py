"""Concurrent catalog changes (#32 D3, D5, E6; #38 B5), with two connections.

Transaction A makes its change and then holds its transaction open for a
moment; transaction B starts once A's change is flushed, so B's statements
meet A's uncommitted row (and its locks). A commits, then B finishes. The
end state must hold the rules whatever the order:

- names stay unique ignoring case (the unique index catches what B's
  pre-check can't see yet);
- a level is never inactive while it has an active subject (``FOR UPDATE``
  on deactivation, ``FOR SHARE`` on subject creation);
- active academic periods of one type never overlap (the exclusion
  constraint catches what B's pre-check can't see yet);
- two classroom ``PATCH`` requests from one version give one success and one
  ``version_conflict`` (``FOR UPDATE``), and the windows are those of the
  winner (#39 B7).
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.models import StaleVersionError
from app.core.uow import UnitOfWork
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    AcademicPeriodCreate,
    AcademicPeriodUpdate,
    AvailabilityWindow,
    ClassroomUpdate,
    EducationLevelCreate,
    EducationLevelUpdate,
    SubjectCreate,
)
from tests.integration.catalog_support import insert_level, unique_name
from tests.integration.conftest import run_sql_async, short_lived_engine

pytestmark = pytest.mark.anyio

Change = Callable[[AsyncSession], Awaitable[Any]]

# How long A keeps its transaction open after its change: long enough for B
# to reach its first conflicting statement.
HOLD_SECONDS = 0.5


async def _outcome(engine: Any, change: Change, after_change: Any = None) -> str:
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            await change(uow.session)
            if after_change is not None:
                await after_change()
    except StaleVersionError:
        return "version_conflict"
    except AppError as exc:
        return exc.code
    return "ok"


async def race(url: str, first: Change, second: Change) -> list[str]:
    """Outcomes of ``first`` (transaction A) and ``second`` (transaction B,
    started once A's change is flushed and still uncommitted)."""
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


async def test_two_creates_of_one_name_give_one_level_and_one_409(head_url):
    name = unique_name("Prepa")

    outcomes = await race(
        head_url,
        lambda s: service.create_education_level(s, EducationLevelCreate(name=name)),
        lambda s: service.create_education_level(
            s, EducationLevelCreate(name=name.upper())
        ),
    )

    assert outcomes == ["ok", "name_taken"]
    rows = await run_sql_async(head_url, "SELECT name FROM education_levels")
    assert [row.name for row in rows] == [name]


async def test_two_creates_of_one_subject_name_give_one_subject_and_one_409(head_url):
    level_id = uuid.UUID(await insert_level(head_url, "Secundaria"))

    outcomes = await race(
        head_url,
        lambda s: service.create_subject(s, level_id, SubjectCreate(name="Arte")),
        lambda s: service.create_subject(s, level_id, SubjectCreate(name="ARTE")),
    )

    assert outcomes == ["ok", "name_taken"]
    rows = await run_sql_async(head_url, "SELECT name FROM subjects")
    assert [row.name for row in rows] == ["Arte"]


async def test_two_patches_from_one_version_give_one_200_and_one_409(head_url):
    level_id = uuid.UUID(await insert_level(head_url, "Secundaria"))

    outcomes = await race(
        head_url,
        lambda s: service.update_education_level(
            s, level_id, EducationLevelUpdate(version=1, name="Secundaria A")
        ),
        lambda s: service.update_education_level(
            s, level_id, EducationLevelUpdate(version=1, sort_order=99)
        ),
    )

    assert outcomes == ["ok", "version_conflict"]
    [row] = await run_sql_async(
        head_url, "SELECT name, sort_order, version FROM education_levels"
    )
    assert tuple(row) == ("Secundaria A", 10, 2)


def _deactivate(level_id: uuid.UUID) -> Change:
    return lambda s: service.update_education_level(
        s, level_id, EducationLevelUpdate(version=1, is_active=False)
    )


def _add_subject(level_id: uuid.UUID) -> Change:
    return lambda s: service.create_subject(s, level_id, SubjectCreate(name="Arte"))


@pytest.mark.parametrize("deactivation_first", [True, False])
async def test_deactivation_racing_a_new_subject_never_leaves_it_orphaned(
    head_url, deactivation_first
):
    level_id = uuid.UUID(await insert_level(head_url, "Secundaria"))

    if deactivation_first:
        outcomes = await race(head_url, _deactivate(level_id), _add_subject(level_id))
        assert outcomes == ["ok", "education_level_inactive"]
    else:
        outcomes = await race(head_url, _add_subject(level_id), _deactivate(level_id))
        assert outcomes == ["ok", "education_level_has_active_subjects"]

    [state] = await run_sql_async(
        head_url,
        "SELECT l.is_active AS level_active,"
        " (SELECT count(*) FROM subjects s WHERE s.education_level_id = l.id"
        "  AND s.is_active) AS active_subjects"
        " FROM education_levels l",
    )
    assert not (not state.level_active and state.active_subjects > 0)
    assert (state.level_active, state.active_subjects) == (
        (False, 0) if deactivation_first else (True, 1)
    )


def _school_year(name: str, starts_on: date, ends_on: date) -> Change:
    return lambda s: service.create_academic_period(
        s,
        AcademicPeriodCreate(
            name=name, period_type="school_year", starts_on=starts_on, ends_on=ends_on
        ),
    )


async def test_two_creates_of_overlapping_school_years_give_one_201_and_one_409(
    head_url,
):
    outcomes = await race(
        head_url,
        _school_year("2026-2027", date(2026, 8, 1), date(2027, 7, 15)),
        _school_year("Ciclo 2027", date(2027, 7, 15), date(2028, 7, 14)),
    )

    assert outcomes == ["ok", "period_overlap"]
    rows = await run_sql_async(head_url, "SELECT name FROM academic_periods")
    assert [row.name for row in rows] == ["2026-2027"]


async def test_a_reactivation_racing_an_overlapping_create_gives_one_409(head_url):
    [row] = await run_sql_async(
        head_url,
        "INSERT INTO academic_periods (name, period_type, starts_on, ends_on,"
        " is_active) VALUES ('Viejo', 'school_year', :starts, :ends, false)"
        " RETURNING id",
        {"starts": date(2026, 8, 1), "ends": date(2027, 7, 15)},
    )

    outcomes = await race(
        head_url,
        _school_year("2026-2027", date(2026, 8, 1), date(2027, 7, 15)),
        lambda s: service.update_academic_period(
            s, row.id, AcademicPeriodUpdate(version=1, is_active=True)
        ),
    )

    assert outcomes == ["ok", "period_overlap"]
    active = await run_sql_async(
        head_url, "SELECT name FROM academic_periods WHERE is_active"
    )
    assert [r.name for r in active] == ["2026-2027"]


async def test_two_classroom_patches_from_one_version_give_one_200_and_one_409(
    head_url,
):
    [room] = await run_sql_async(
        head_url,
        "INSERT INTO classrooms (name, capacity) VALUES ('Aula 1', 10) RETURNING id",
    )

    def windows(starts_at: str, ends_at: str) -> ClassroomUpdate:
        return ClassroomUpdate(
            version=1,
            availability=[
                AvailabilityWindow(weekday=1, starts_at=starts_at, ends_at=ends_at)
            ],
        )

    outcomes = await race(
        head_url,
        lambda s: service.update_classroom(s, room.id, windows("09:00", "12:00")),
        lambda s: service.update_classroom(s, room.id, windows("10:00", "13:00")),
    )

    assert outcomes == ["ok", "version_conflict"]
    [state] = await run_sql_async(head_url, "SELECT version FROM classrooms")
    assert state.version == 2
    rows = await run_sql_async(
        head_url,
        "SELECT to_char(starts_at, 'HH24:MI') AS starts_at"
        " FROM classroom_availability_windows",
    )
    assert [r.starts_at for r in rows] == ["09:00"]
