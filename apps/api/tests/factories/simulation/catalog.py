"""``catalog`` (#543): subjects, classrooms, the school year and the
closures, through the catalog services, as the Administrator.

At :data:`~.anchor.SIM_SETUP`: the plan's subjects (each at its reference
level), its classrooms (one with weekly windows, the others usable during
operating hours), the academic period and the three Mexican calendar
closures inside the window. On 30 Oct the third classroom is deactivated
(a renovation); HF-01b books nothing in it after that date.
"""

from collections.abc import Iterable
from datetime import date
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

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
    ClassroomUpdate,
    ClosureCreate,
    SubjectCreate,
)
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import ZONE, sim_clock
from tests.factories.simulation.people import principal
from tests.factories.simulation.timeline import SimulationContext

CATALOG_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    Subject,
    Classroom,
    ClassroomAvailabilityWindow,
    AcademicPeriod,
    Closure,
)


class CatalogModule:
    name = "catalog"
    tables = CATALOG_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        name = ctx.plan.academic_period[0]
        found = await session.scalar(
            select(AcademicPeriod.id).where(AcademicPeriod.name == name)
        )
        return found is not None

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        plan = ctx.plan
        admin = await principal(session, ctx, plan.administrator)
        levels = dict(
            (
                await session.execute(
                    select(EducationLevel.seed_key, EducationLevel.id)
                )
            ).all()
        )
        with seed_actor(session, admin.user_id):
            for subject in plan.subjects:
                created = await catalog.create_subject(
                    session, levels[subject.level], SubjectCreate(name=subject.name)
                )
                ctx.ids[f"subject:{subject.key}"] = created.id
            for room in plan.classrooms:
                detail = await catalog_classrooms.create_classroom(
                    session,
                    ClassroomCreate(
                        name=room.name,
                        capacity=room.capacity,
                        availability=[
                            AvailabilityWindow(weekday=day, starts_at=s, ends_at=e)
                            for day, s, e in room.windows
                        ],
                    ),
                )
                ctx.ids[f"classroom:{room.key}"] = detail.id
            name, starts_on, ends_on = plan.academic_period
            await catalog_periods.create_academic_period(
                session,
                AcademicPeriodCreate(
                    name=name,
                    period_type="school_year",
                    starts_on=starts_on,
                    ends_on=ends_on,
                ),
            )
            for closure in plan.closures:
                await catalog_periods.create_closure(
                    session,
                    ClosureCreate(
                        name=closure.name,
                        closure_type="holiday",
                        starts_on=closure.day,
                        ends_on=closure.day,
                    ),
                )

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return {
            room.deactivated_at.astimezone(ZONE).date()
            for room in ctx.plan.classrooms
            if room.deactivated_at is not None
        }

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        admin = await principal(session, ctx, ctx.plan.administrator)
        for room in ctx.plan.classrooms:
            at = room.deactivated_at
            if at is None or at.astimezone(ZONE).date() != day:
                continue
            room_id = ctx.id(f"classroom:{room.key}")
            version = await session.scalar(
                select(Classroom.version).where(Classroom.id == room_id)
            )
            assert version is not None
            with sim_clock(at), seed_actor(session, admin.user_id):
                await catalog_classrooms.update_classroom(
                    session, room_id, ClassroomUpdate(version=version, is_active=False)
                )

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None
