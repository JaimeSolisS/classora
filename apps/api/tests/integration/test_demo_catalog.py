"""The demo catalog (#299 B, E2, E5, E6): ``catalog.demo_catalog``."""

import asyncio
from typing import Any

import pytest

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from tests.factories import demo
from tests.factories.base import GeneratorSkipped
from tests.factories.catalog import DemoCatalogGenerator
from tests.factories.demo_schedule import DemoScheduleGenerator
from tests.factories.identity import DemoPrerequisiteMissingError
from tests.factories.scheduling import persist_classroom
from tests.factories.staff import DemoTutorGenerator
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import (
    DEMO_TABLE_COUNTS,
    FIRST_RUN_LINES,
    FROZEN_NOW,
    UntouchedSession,
    count,
    demo_env,
    demo_table_counts,
    migrate,
)

NEW_GENERATORS = [DemoCatalogGenerator, DemoTutorGenerator, DemoScheduleGenerator]


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def test_a_first_run_writes_the_catalog(migrated_url, env_for, clock, capsys):
    clock.set(FROZEN_NOW)  # T = Wednesday 2026-11-04, so D(3) = 2026-11-10
    env_for("local", migrated_url)

    assert demo.main([]) == 0

    assert capsys.readouterr().out.splitlines() == FIRST_RUN_LINES
    subjects = run_sql(
        migrated_url,
        "SELECT l.name AS level, s.name, s.is_active FROM subjects s"
        " JOIN education_levels l ON l.id = s.education_level_id"
        " ORDER BY l.sort_order, s.name",
    )
    assert [(row.level, row.name, row.is_active) for row in subjects] == [
        ("Primaria", "Español", True),
        ("Primaria", "Matemáticas", True),
        ("Secundaria", "Ciencias", True),
        ("Secundaria", "Inglés", True),
        ("Secundaria", "Matemáticas", True),
        ("Preparatoria", "Física", True),
        ("Preparatoria", "Química", True),
        ("Universidad", "Cálculo", True),
    ]
    rooms = run_sql(
        migrated_url,
        "SELECT c.name, c.capacity, c.is_active,"
        " array_agg(w.weekday || ' ' || to_char(w.starts_at, 'HH24:MI') || '-'"
        "  || to_char(w.ends_at, 'HH24:MI') ORDER BY w.weekday, w.starts_at)"
        "  FILTER (WHERE w.id IS NOT NULL) AS windows"
        " FROM classrooms c"
        " LEFT JOIN classroom_availability_windows w ON w.classroom_id = c.id"
        " GROUP BY c.id ORDER BY c.capacity",
    )
    weekdays = [f"{day} 15:00-20:00" for day in range(1, 6)]
    assert [(r.name, r.capacity, r.is_active, r.windows) for r in rooms] == [
        ("Aula 1", 4, True, None),
        ("Aula 2", 6, True, weekdays),
        ("Sala de usos múltiples", 12, True, [*weekdays, "6 09:00-14:00"]),
    ]
    [period] = run_sql(
        migrated_url,
        "SELECT name, period_type, starts_on::text, ends_on::text, is_active"
        " FROM academic_periods",
    )
    assert tuple(period) == (
        "Ciclo escolar 2026-2027",
        "school_year",
        "2026-08-01",
        "2027-07-31",
        True,
    )
    [closure] = run_sql(
        migrated_url,
        "SELECT name, closure_type, starts_on::text, ends_on::text, is_active"
        " FROM closures",
    )
    assert tuple(closure) == (
        "Día de asueto",
        "holiday",
        "2026-11-10",
        "2026-11-10",
        True,
    )
    assert demo_table_counts(migrated_url) == DEMO_TABLE_COUNTS


async def _run(url: str, *steps: Any) -> list[int | GeneratorSkipped]:
    """Run ``steps`` (generators or ``async (session) -> None`` callables) in
    one Unit of Work."""
    engine = short_lived_engine(url)
    results: list[int | GeneratorSkipped] = []
    try:
        async with UnitOfWork(create_sessionmaker(engine)) as uow:
            for step in steps:
                if hasattr(step, "generate"):
                    results.append(await step.generate(uow.session))
                else:
                    await step(uow.session)
    finally:
        await engine.dispose()
    return results


def test_an_existing_aula_1_means_0_rows_and_the_tutor_then_rolls_back(
    migrated_url, env_for
):
    # E6: the marker, then the staff generator misses the demo subjects and
    # the whole run rolls back (nothing written, not even the accounts).
    env_for("local", migrated_url)
    seen: list[int | GeneratorSkipped] = []

    async def add_aula_1(session: Any) -> None:
        room = await persist_classroom(session)
        room.name = "Aula 1"
        room.is_active = False
        await session.flush()

    class Recording:
        def __init__(self, generator: Any) -> None:
            self.generator = generator
            self.name = generator.name

        async def generate(self, session: Any) -> int | GeneratorSkipped:
            result: int | GeneratorSkipped = await self.generator.generate(session)
            seen.append(result)
            return result

    class AddAula1:
        name = "test.aula_1"

        async def generate(self, session: Any) -> int:
            await add_aula_1(session)
            return 1

    generators = [
        *demo.DEMO_GENERATORS[:8],
        AddAula1(),
        Recording(DemoCatalogGenerator()),
        DemoTutorGenerator(),
        DemoScheduleGenerator(),
    ]
    with pytest.raises(DemoPrerequisiteMissingError, match="subjects"):
        demo.main([], generators=generators)

    assert seen == [0]
    assert count(migrated_url, "users") == 0
    assert count(migrated_url, "classrooms") == 0


def test_an_existing_aula_1_alone_is_0_rows(migrated_url):
    async def add_aula_1(session: Any) -> None:
        room = await persist_classroom(session)
        room.name = "Aula 1"
        await session.flush()

    local = DemoCatalogGenerator(app_env=lambda: "local")
    assert asyncio.run(_run(migrated_url, add_aula_1, local)) == [0]
    assert count(migrated_url, "subjects") == 0


@pytest.mark.parametrize("app_env", ["dev", "prod", "staging"])
@pytest.mark.parametrize("generator", NEW_GENERATORS, ids=lambda g: g.name)
def test_a_new_generator_writes_nothing_outside_local(generator, app_env):
    # E5: skipped without touching the session.
    result = asyncio.run(
        generator(app_env=lambda: app_env).generate(UntouchedSession())  # type: ignore[arg-type]
    )

    assert result == GeneratorSkipped(f"APP_ENV={app_env}; local only")
