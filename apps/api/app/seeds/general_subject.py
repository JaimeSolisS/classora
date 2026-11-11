"""The ``catalog.general_subject`` reference seed (HF-12, #597 E1 (a), E2 (a)).

The built-in "General" subject, in its own "General" level, which every
deployment gets from ``make seed``:

=====================  ============  ===========  ==============
table                  ``seed_key``  ``name``     ``sort_order``
=====================  ============  ===========  ==============
``education_levels``   ``general``   General      0
``subjects``           ``general``   General      (none)
=====================  ============  ===========  ==============

The level's ``sort_order`` 0 puts it first in every picker that groups
subjects by level ("General › General"). The name is a stored catalog name,
shown as stored in every language (owner decision #32 E1 (a)); es and en are
the same word. Any active tutor may teach the subject without a
qualification, and it can't be deactivated
(:func:`app.modules.catalog.service.general_subject_id`).

**Insert-only, through the ORM**, like :mod:`app.seeds.education_levels`: an
inserted row gets a ``create`` audit event (actor ``NULL``), and an existing
row is never renamed, reordered or reactivated, so an administrator's rename
survives every later ``make seed``.

**Claiming an existing "General" (the one update this seed makes).** The
names are unique ignoring case (levels across all levels, subjects within
their level), so a center that already has a level named "General" (any
case) would refuse a second one. Instead of inserting, the seed **claims**
that row by setting its ``seed_key`` to ``'general'`` when it has none; the
subject is then looked up by name within that level and claimed the same
way. The claim is an ORM update (one ``update`` audit event) and happens at
most once: from then on the row is found by its key. A level named
"General" that already has another ``seed_key`` is used as the subject's
level but keeps its key. A subject named "General" under another level is
not claimed (it isn't the built-in one; the built-in subject is created in
the "General" level next to it). The seed never reactivates a claimed
inactive row.
"""

from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import GENERAL_SEED_KEY, EducationLevel, Subject
from app.seeds.upsert import UpsertResult

GENERAL_NAME: Final = "General"
GENERAL_LEVEL_SORT_ORDER: Final = 0


@dataclass(frozen=True)
class GeneralSubjectSeed:
    """Creates (or claims) the "General" level and subject. Never deletes."""

    name: str = "catalog.general_subject"

    async def apply(self, session: AsyncSession) -> UpsertResult:
        inserted = 0
        updated = 0
        level = await session.scalar(
            select(EducationLevel).where(EducationLevel.seed_key == GENERAL_SEED_KEY)
        )
        if level is None:
            level = await session.scalar(
                select(EducationLevel).where(
                    func.lower(EducationLevel.name) == func.lower(GENERAL_NAME)
                )
            )
            if level is None:
                level = EducationLevel(
                    seed_key=GENERAL_SEED_KEY,
                    name=GENERAL_NAME,
                    sort_order=GENERAL_LEVEL_SORT_ORDER,
                    is_active=True,
                )
                session.add(level)
                await session.flush()
                inserted += 1
            elif level.seed_key is None:
                level.seed_key = GENERAL_SEED_KEY
                updated += 1
        subject = await session.scalar(
            select(Subject).where(Subject.seed_key == GENERAL_SEED_KEY)
        )
        if subject is None:
            subject = await session.scalar(
                select(Subject)
                .where(Subject.education_level_id == level.id)
                .where(func.lower(Subject.name) == func.lower(GENERAL_NAME))
            )
            if subject is None:
                session.add(
                    Subject(
                        education_level_id=level.id,
                        seed_key=GENERAL_SEED_KEY,
                        name=GENERAL_NAME,
                        is_active=True,
                    )
                )
                inserted += 1
            else:
                subject.seed_key = GENERAL_SEED_KEY
                updated += 1
        await session.flush()
        return UpsertResult(inserted=inserted, updated=updated)


GENERAL_SUBJECT_SEED = GeneralSubjectSeed()
