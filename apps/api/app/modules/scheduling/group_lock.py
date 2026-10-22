"""The group row lock (#56 F1; ADR-0005 D1 "group capacity: the group row
``FOR UPDATE``").

**Rule: every writer that changes a group occurrence's participants or room
takes the group row ``FOR UPDATE`` after ``lock_students`` and the center
zone ``FOR SHARE``, and before the series row** (the enrollments and
withdrawals, the group materialization and closure reconcile, #54's series
edits and ``PATCH``; #55 and #58 follow it for any such change). The full
order is **students → zone → group → series → sessions → offers**, so no
two writers wait on each other in a cycle.

A writer that read a group series' participants before its locks re-reads
them once it holds the series row (an enrollment may have committed in
between): the nightly job retries the series (:class:`SeriesParticipantsChangedError`,
#56 C2); the series edits and ``PATCH`` rely on the series and session
``version``, which every enrollment change bumps.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scheduling.models import ClassGroup


class SeriesParticipantsChangedError(Exception):
    """A group series' participants changed between the participant read and
    the series lock (#56 C2): the caller rolls its unit back and retries."""


async def lock_group(session: AsyncSession, group_id: uuid.UUID) -> ClassGroup | None:
    """The group row ``FOR UPDATE`` (re-read), or ``None`` when unknown."""
    group: ClassGroup | None = await session.scalar(
        select(ClassGroup)
        .where(ClassGroup.id == group_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return group
