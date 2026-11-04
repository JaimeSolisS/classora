"""Which templates block a category or vendor deactivation (#116 E2; ADR-0017
E15).

A template is **live** while it can still post: ``is_active`` and not ended
(``ends_on`` is ``NULL`` or ``next_period_start <= ends_on``). A paused or
ended template doesn't block. The deactivation of a category or vendor reads
the live templates using it **after** taking its own row ``FOR UPDATE``, with
no lock on the templates; every template write that leaves a template live
reads its category and vendor ``FOR SHARE`` (``usable_*``), so whichever
commits first is seen by the other (data-model §15).

Kept apart from :mod:`~app.modules.finance.templates` so that
:mod:`~app.modules.finance.categories` and :mod:`~app.modules.finance.vendors`
can import it without an import cycle.
"""

import uuid

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.modules.finance.models import ExpenseTemplate


def live() -> ColumnElement[bool]:
    """A template that can still post: active and not ended."""
    return ExpenseTemplate.is_active.is_(True) & or_(
        ExpenseTemplate.ends_on.is_(None),
        ExpenseTemplate.next_period_start <= ExpenseTemplate.ends_on,
    )


async def live_template_count(
    session: AsyncSession,
    column: InstrumentedAttribute[uuid.UUID] | InstrumentedAttribute[uuid.UUID | None],
    row_id: uuid.UUID,
) -> int:
    """How many live templates have ``column = row_id``. One statement, no
    lock."""
    count = await session.scalar(
        select(func.count(ExpenseTemplate.id)).where(column == row_id, live())
    )
    return int(count or 0)
