"""``settings`` (#543): the center's business settings, operating hours,
scheduling policy and, in English, the reference names, as the Owner.

At :data:`~.anchor.SIM_SETUP`:

- the business settings (``update_business_settings``): the language's
  center name (the simulation marker), contact email and address, the time
  zone unchanged, both receipt messages, and the operating hours Monday to
  Friday 09:00-20:00 and Saturday 09:00-14:00 (the migration defaults, so
  the service writes no hours row unless they differ);
- a scheduling-policy version effective at ``SIM_SETUP`` with the rules of
  the version in force (the merged defaults);
- in ``en`` (E8), the four education levels and the five default expense
  categories renamed through their services; in ``es`` the reference names
  stay as seeded.
"""

from collections.abc import Iterable
from datetime import date
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core import clock
from app.core.reauth import RecentAuthGuard
from app.modules.administration import service as administration
from app.modules.administration.schemas import (
    BusinessSettingsUpdate,
    OperatingHoursItem,
    SchedulingPolicyCreate,
    SchedulingPolicyRules,
)
from app.modules.administration.settings_models import (
    BusinessSettings,
    OperatingHours,
    SchedulingPolicy,
)
from app.modules.catalog import service as catalog
from app.modules.catalog.models import EducationLevel
from app.modules.catalog.schemas import EducationLevelUpdate
from app.modules.finance import categories
from app.modules.finance.models import ExpenseCategory
from app.modules.finance.schemas import ExpenseCategoryUpdate
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import SIM_SETUP
from tests.factories.simulation.people import principal
from tests.factories.simulation.texts import RECEIPT_MESSAGES
from tests.factories.simulation.timeline import SimulationContext

SETTINGS_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    BusinessSettings,
    OperatingHours,
    SchedulingPolicy,
    EducationLevel,
    ExpenseCategory,
)

OPERATING_HOURS: Final[tuple[tuple[int, str, str], ...]] = (
    *((day, "09:00", "20:00") for day in (1, 2, 3, 4, 5)),
    (6, "09:00", "14:00"),
)
TIME_ZONE_ACTION: Final = "settings.time_zone"


class SettingsModule:
    name = "settings"
    tables = SETTINGS_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        name = await session.scalar(select(BusinessSettings.center_name))
        return name == ctx.plan.center_name

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        plan = ctx.plan
        owner = await principal(session, ctx, plan.owner)
        with seed_actor(session, owner.user_id):
            version = await session.scalar(select(BusinessSettings.version))
            assert version is not None
            await administration.update_business_settings(
                session,
                BusinessSettingsUpdate(
                    version=version,
                    center_name=plan.center_name,
                    contact_email=plan.contact_email,
                    address=plan.address,
                    operating_hours=[
                        OperatingHoursItem(weekday=day, opens_at=s, closes_at=e)
                        for day, s, e in OPERATING_HOURS
                    ],
                    receipt_message_es=RECEIPT_MESSAGES["es"],
                    receipt_message_en=RECEIPT_MESSAGES["en"],
                ),
                guard=RecentAuthGuard(TIME_ZONE_ACTION, clock.now()),
            )
            current = await administration.get_scheduling_policy(session, SIM_SETUP)
            rules = SchedulingPolicyRules.model_validate(
                current, from_attributes=True
            ).model_dump()
            await administration.create_scheduling_policy(
                session,
                SchedulingPolicyCreate(
                    **rules, based_on_id=current.id, effective_from=SIM_SETUP
                ),
            )
            if ctx.language == "en":
                await _rename_reference_names(session, ctx)

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return ()

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        return None

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None


async def _rename_reference_names(
    session: AsyncSession, ctx: SimulationContext
) -> None:
    """E8: the Spanish names the reference seeds and migration 0083 insert."""
    levels = ctx.texts.mapping("education_levels")
    rows = await session.execute(
        select(EducationLevel.id, EducationLevel.seed_key, EducationLevel.version)
    )
    for level_id, seed_key, version in rows.all():
        if seed_key in levels:
            await catalog.update_education_level(
                session,
                level_id,
                EducationLevelUpdate(version=version, name=levels[seed_key]),
            )
    names = ctx.texts.mapping("expense_categories")
    rows = await session.execute(
        select(ExpenseCategory.id, ExpenseCategory.system_key, ExpenseCategory.version)
    )
    for category_id, key, version in rows.all():
        if key in names:
            await categories.change_category(
                session,
                category_id,
                ExpenseCategoryUpdate(version=version, name=names[key]),
            )
