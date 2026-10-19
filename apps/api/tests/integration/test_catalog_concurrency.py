"""Concurrent catalog changes (#32 D3, D5, E6), with two connections.

Transaction A makes its change and then holds its transaction open for a
moment; transaction B starts once A's change is flushed, so B's statements
meet A's uncommitted row (and its locks). A commits, then B finishes. The
end state must hold the rules whatever the order:

- names stay unique ignoring case (the unique index catches what B's
  pre-check can't see yet);
- a level is never inactive while it has an active subject (``FOR UPDATE``
  on deactivation, ``FOR SHARE`` on subject creation).
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.errors import AppError
from app.core.models import StaleVersionError
from app.core.uow import UnitOfWork
from app.modules.catalog import service
from app.modules.catalog.schemas import (
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
