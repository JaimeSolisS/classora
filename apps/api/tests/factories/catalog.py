"""The demo catalog (#299 B): the ``catalog.demo_catalog`` generator.

Example subjects for the four reference levels, three classrooms, the
current school year and one holiday (``D(3)`` of
:func:`~tests.factories.demo_schedule.plan_demo_schedule`), all written
through the catalog services (#299 G1). Fixed ``es_MX`` names, no Faker.
"""

from collections.abc import Callable
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.administration import service as administration
from app.modules.catalog import classrooms as catalog_classrooms
from app.modules.catalog import periods as catalog_periods
from app.modules.catalog import service as catalog
from app.modules.catalog.models import (
    AcademicPeriod,
    Classroom,
    ClassroomAvailabilityWindow,
    Closure,
    EducationLevel,
    Subject,
)
from app.modules.catalog.schemas import (
    AcademicPeriodCreate,
    AvailabilityWindow,
    ClassroomCreate,
    ClosureCreate,
    SubjectCreate,
)
from tests.factories.base import GeneratorSkipped, count_rows
from tests.factories.demo_schedule import (
    AULA_1,
    AULA_2,
    SALA,
    DemoSchedulePlan,
    plan_demo_schedule,
)
from tests.factories.identity import (
    DEMO_ACCOUNT_APP_ENVS,
    DemoPrerequisiteMissingError,
)

DEMO_SUBJECTS: dict[str, tuple[str, ...]] = {
    "Primaria": ("Matemáticas", "Español"),
    "Secundaria": ("Matemáticas", "Inglés", "Ciencias"),
    "Preparatoria": ("Física", "Química"),
    "Universidad": ("Cálculo",),
}
"""The demo subjects of each reference level (#299 B2)."""

WEEKDAYS = (1, 2, 3, 4, 5)
SATURDAY = 6

DEMO_CLASSROOMS: tuple[tuple[str, int, tuple[tuple[int, str, str], ...]], ...] = (
    (AULA_1, 4, ()),
    (AULA_2, 6, tuple((day, "15:00", "20:00") for day in WEEKDAYS)),
    (
        SALA,
        12,
        (
            *((day, "15:00", "20:00") for day in WEEKDAYS),
            (SATURDAY, "09:00", "14:00"),
        ),
    ),
)
"""Name, capacity and weekly windows (ISO weekday, ``HH:MM``, ``HH:MM``) of
each demo classroom (#299 B3); no windows: usable at any time."""

CLOSURE_NAME = "Día de asueto"

DEMO_CATALOG_TABLES = (
    Subject,
    Classroom,
    ClassroomAvailabilityWindow,
    AcademicPeriod,
    Closure,
)
"""The tables whose added rows ``catalog.demo_catalog`` counts (#299 B)."""


def school_year_name(start_year: int) -> str:
    return f"Ciclo escolar {start_year}-{start_year + 1}"


def _settings_app_env() -> str:
    return get_settings().app_env


class DemoCatalogGenerator:
    """``catalog.demo_catalog`` (#299 B): 8 subjects, 3 classrooms with 11
    windows, the school year containing the center's today and one holiday
    (24 rows).

    Writes nothing outside ``APP_ENV=local`` (skipped), and nothing (0 rows)
    once a classroom named ``Aula 1`` exists (in any state). Raises when a
    reference level is missing.
    """

    name = "catalog.demo_catalog"

    def __init__(
        self,
        app_env: Callable[[], str] = _settings_app_env,
        plan: Callable[[date], DemoSchedulePlan] = plan_demo_schedule,
    ) -> None:
        self._app_env = app_env
        self._plan = plan

    async def generate(self, session: AsyncSession) -> int | GeneratorSkipped:
        app_env = self._app_env()
        if app_env not in DEMO_ACCOUNT_APP_ENVS:
            return GeneratorSkipped(f"APP_ENV={app_env}; local only")
        marker = await session.scalar(
            select(Classroom.id).where(Classroom.name == AULA_1)
        )
        if marker is not None:
            return 0
        before = await count_rows(session, DEMO_CATALOG_TABLES)
        plan = self._plan(await administration.center_today(session))

        for level_name, subjects in DEMO_SUBJECTS.items():
            level_id = await session.scalar(
                select(EducationLevel.id).where(EducationLevel.name == level_name)
            )
            if level_id is None:
                raise DemoPrerequisiteMissingError(
                    f"the {level_name} reference level must exist before {self.name}"
                )
            for subject in subjects:
                await catalog.create_subject(
                    session, level_id, SubjectCreate(name=subject)
                )

        for room, capacity, windows in DEMO_CLASSROOMS:
            await catalog_classrooms.create_classroom(
                session,
                ClassroomCreate(
                    name=room,
                    capacity=capacity,
                    availability=[
                        AvailabilityWindow(weekday=day, starts_at=start, ends_at=end)
                        for day, start, end in windows
                    ],
                ),
            )

        year = plan.period_start_year
        await catalog_periods.create_academic_period(
            session,
            AcademicPeriodCreate(
                name=school_year_name(year),
                period_type="school_year",
                starts_on=date(year, 8, 1),
                ends_on=date(year + 1, 7, 31),
            ),
        )
        await catalog_periods.create_closure(
            session,
            ClosureCreate(
                name=CLOSURE_NAME,
                closure_type="holiday",
                starts_on=plan.closure_day,
                ends_on=plan.closure_day,
            ),
        )
        return await count_rows(session, DEMO_CATALOG_TABLES) - before
