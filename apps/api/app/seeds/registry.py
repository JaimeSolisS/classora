"""The ordered list of reference seeds applied by ``make seed``.

Domain tasks register their reference data by appending one object to
:data:`REFERENCE_SEEDS`, in dependency order (a seed comes after the seeds its
rows refer to). The first users are roles and permission sets (M2-02) and
education levels (M2-14). Each seed's table comes from its own migration.

Rules for every seed:

- Values are fixed literals in code: no Faker, no randomness, no current date
  and nothing environment-specific, so every environment gets identical data.
  Seeds whose values are code constants (the identity catalog) build their rows
  from those constants, never from copied literals.
- It writes only through :func:`app.seeds.upsert.upsert_rows` (usually as an
  :class:`UpsertSeed`), never with hand-written SQL, and never commits.
  **Second exception** (the first is the ``role_permissions`` deletes below):
  admin-editable, audited reference data is inserted
  once through the ORM and never updated
  (:class:`app.seeds.education_levels.InsertOnlySeed`, first used by
  ``catalog.education_levels``, M2-14). ``upsert_rows`` would undo an
  administrator's rename or deactivation on the next run, and its Core insert
  would skip the audit trail (#22); the ORM insert writes a ``create`` event
  per row (actor ``NULL``), and a row whose key or name already exists is
  left alone.
- It never deletes rows. **One exception:** ``identity.role_permissions``
  (:mod:`app.seeds.role_permissions`) also deletes the pairs of the seeded
  roles that ``ROLE_PERMISSIONS`` no longer lists, so the database's role
  permission sets always equal the code (ADR-0007). It deletes only from
  ``role_permissions``; ``roles`` and ``permissions`` rows are never deleted.
- It never creates user accounts or credentials.
- Names are unique (a unit test checks this).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import Permission, Role
from app.modules.identity.permissions import PERMISSIONS, ROLE_CODES, ROLE_PERMISSIONS
from app.seeds.education_levels import EDUCATION_LEVELS_SEED
from app.seeds.role_permissions import RolePermissionsSeed
from app.seeds.upsert import UpsertResult, upsert_rows


class ReferenceSeed(Protocol):
    """A named, idempotent writer of reference rows."""

    @property
    def name(self) -> str: ...

    async def apply(self, session: AsyncSession) -> UpsertResult: ...


@dataclass(frozen=True)
class UpsertSeed:
    """A reference seed for one table: fixed ``rows`` upserted on ``key``."""

    name: str
    model: type[Any]
    rows: Sequence[Mapping[str, Any]]
    key: Sequence[str] = ("code",)

    async def apply(self, session: AsyncSession) -> UpsertResult:
        return await upsert_rows(session, self.model, self.rows, key=self.key)


REFERENCE_SEEDS: tuple[ReferenceSeed, ...] = (
    # Identity catalog (M2-02, ADR-0007), from app.modules.identity.permissions.
    UpsertSeed(
        name="identity.permissions",
        model=Permission,
        rows=tuple(
            {"code": permission.code, "delegable": permission.delegable}
            for permission in PERMISSIONS
        ),
    ),
    UpsertSeed(
        name="identity.roles",
        model=Role,
        rows=tuple({"code": code} for code in ROLE_CODES),
    ),
    RolePermissionsSeed(name="identity.role_permissions", mapping=ROLE_PERMISSIONS),
    # Example education levels (M2-14): inserted once, never updated.
    EDUCATION_LEVELS_SEED,
)
"""Every reference seed, in the order they are applied."""
