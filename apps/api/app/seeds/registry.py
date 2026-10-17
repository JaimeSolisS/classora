"""The ordered list of reference seeds applied by ``make seed``.

Domain tasks register their reference data by appending one object to
:data:`REFERENCE_SEEDS`, in dependency order (a seed comes after the seeds its
rows refer to). The first users are roles and permission sets (M2-02) and
education levels (M2-14). Each seed's table comes from its own migration.

Rules for every seed:

- Values are fixed literals in code: no Faker, no randomness, no current date
  and nothing environment-specific, so every environment gets identical data.
- It writes only through :func:`app.seeds.upsert.upsert_rows` (usually as an
  :class:`UpsertSeed`), never with hand-written SQL, and never commits.
- It never creates user accounts or credentials.
- Names are unique (a unit test checks this).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

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


REFERENCE_SEEDS: tuple[ReferenceSeed, ...] = ()
"""Every reference seed, in the order they are applied (empty until M2-02)."""
