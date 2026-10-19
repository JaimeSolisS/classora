"""The ``catalog.education_levels`` reference seed (#32 B).

Four example education levels, fixed literals with Mexican Spanish names
(owner decision #32 E1 (a): one free-text name per record, shown as stored in
every language):

=================  ============  ==========
``seed_key``       ``name``      ``sort_order``
=================  ============  ==========
``elementary``     Primaria      10
``middle_school``  Secundaria    20
``high_school``    Preparatoria  30
``university``     Universidad   40
=================  ============  ==========

**Insert-only, through the ORM.** A row is inserted (``session.add``) only
when no level has its ``seed_key`` **and** no level has its name ignoring
case; otherwise the database is left alone. Existing levels are never
updated or reactivated, so an administrator's rename, reorder or deactivation
survives every later ``make seed`` (which deployments run). Because it goes
through the ORM, #22 records a ``create`` audit event per inserted level with
a ``NULL`` actor; no Core statement touches the audited table. No subjects
are seeded.

This is the second documented exception to "writes only through
``upsert_rows``" (:mod:`app.seeds.registry`); later admin-editable, audited
reference data (#38, #39) reuses :class:`InsertOnlySeed`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import EducationLevel
from app.seeds.upsert import UpsertResult

EDUCATION_LEVEL_ROWS: tuple[Mapping[str, Any], ...] = (
    {"seed_key": "elementary", "name": "Primaria", "sort_order": 10},
    {"seed_key": "middle_school", "name": "Secundaria", "sort_order": 20},
    {"seed_key": "high_school", "name": "Preparatoria", "sort_order": 30},
    {"seed_key": "university", "name": "Universidad", "sort_order": 40},
)


@dataclass(frozen=True)
class InsertOnlySeed:
    """Inserts each row of ``rows`` through the ORM unless a row of ``model``
    already has its ``seed_key`` or its ``name`` ignoring case. Never updates
    or deletes."""

    name: str
    model: type[Any]
    rows: Sequence[Mapping[str, Any]]

    async def apply(self, session: AsyncSession) -> UpsertResult:
        existing = await session.execute(
            select(self.model.seed_key, func.lower(self.model.name))
        )
        keys: set[str] = set()
        names: set[str] = set()
        for seed_key, lower_name in existing:
            if seed_key is not None:
                keys.add(seed_key)
            names.add(lower_name)
        inserted = 0
        for row in self.rows:
            # PostgreSQL's lower(), the function of the unique name index.
            lower_name = str(await session.scalar(select(func.lower(row["name"]))))
            if row["seed_key"] in keys or lower_name in names:
                continue
            session.add(self.model(**row))
            keys.add(row["seed_key"])
            names.add(lower_name)
            inserted += 1
        await session.flush()
        return UpsertResult(inserted=inserted)


EDUCATION_LEVELS_SEED = InsertOnlySeed(
    name="catalog.education_levels",
    model=EducationLevel,
    rows=EDUCATION_LEVEL_ROWS,
)
