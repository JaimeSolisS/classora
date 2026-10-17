"""Upsert by natural key: the one write path for reference seeds.

:func:`upsert_rows` sends one PostgreSQL statement::

    INSERT INTO t (k, a, b) VALUES ...
    ON CONFLICT (k) DO UPDATE SET a = excluded.a, b = excluded.b,
                                  updated_at = now() [, version = t.version + 1]
    WHERE t.a IS DISTINCT FROM excluded.a OR t.b IS DISTINCT FROM excluded.b
    RETURNING (xmax = 0)

- A new natural key is inserted; ``id``, ``created_at`` and ``updated_at``
  come from the column defaults (UUIDv7, ``now()``).
- An existing row is updated only when a seeded column differs. It keeps its
  ``id`` (foreign keys stay valid), gets ``updated_at = now()`` and, for a
  :class:`~app.core.models.VersionedMixin` model, ``version + 1``.
- An unchanged row is not touched at all, so a rerun changes nothing.
- Rows missing from the input are never deleted, and columns the input does not
  list are never changed.

The helper never commits: the caller's Unit of Work does.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Boolean, Index, Table, func, inspect, literal_column, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.schema import PrimaryKeyConstraint, UniqueConstraint

from app.core.models import VersionedMixin

# Columns set by the database or by optimistic locking; seeds never list them.
MANAGED_COLUMNS = frozenset({"id", "created_at", "updated_at", "version"})


@dataclass(frozen=True)
class UpsertResult:
    """How many rows one upsert inserted and updated (unchanged rows: neither)."""

    inserted: int = 0
    updated: int = 0


def _table_of(model: type[Any]) -> Table:
    table = inspect(model).local_table
    if not isinstance(table, Table):
        raise ValueError(f"{model.__name__} is not mapped to a table")
    return table


def _has_unique_key(table: Table, key: Sequence[str]) -> bool:
    wanted = set(key)
    for constraint in table.constraints:
        if (
            isinstance(constraint, (UniqueConstraint, PrimaryKeyConstraint))
            and {column.name for column in constraint.columns} == wanted
        ):
            return True
    for index in table.indexes:
        if isinstance(index, Index) and index.unique and index.expressions:
            names = {getattr(expr, "name", None) for expr in index.expressions}
            if names == wanted:
                return True
    return False


def _validate(
    table: Table, rows: Sequence[Mapping[str, Any]], key: Sequence[str]
) -> list[str]:
    """Check everything before any SQL is sent; return the seeded columns."""
    if not key:
        raise ValueError("key must name at least one column")
    if not _has_unique_key(table, key):
        raise ValueError(
            f"{table.name}: key {tuple(key)} needs a unique constraint or index"
        )
    columns = list(rows[0])
    managed = MANAGED_COLUMNS.intersection(columns)
    if managed:
        raise ValueError(f"{table.name}: seeds must not set {sorted(managed)}")
    unknown = [name for name in columns if name not in table.c]
    if unknown:
        raise ValueError(f"{table.name}: unknown columns {unknown}")
    missing_key = [name for name in key if name not in columns]
    if missing_key:
        raise ValueError(f"{table.name}: rows must include the key {missing_key}")
    seen: set[tuple[Any, ...]] = set()
    for index, row in enumerate(rows):
        if set(row) != set(columns):
            raise ValueError(f"{table.name}: row {index} has different columns")
        natural_key = tuple(row[name] for name in key)
        if natural_key in seen:
            # The message names the position, not the value.
            raise ValueError(f"{table.name}: duplicate natural key in row {index}")
        seen.add(natural_key)
    return columns


async def upsert_rows(
    session: AsyncSession,
    model: type[Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    key: Sequence[str] = ("code",),
) -> UpsertResult:
    """Insert new rows and update changed ones, matched on the natural ``key``.

    ``rows`` all list the same columns, including ``key`` and excluding ``id``,
    ``created_at``, ``updated_at`` and ``version``. ``key`` must have a unique
    constraint or unique index. Raises ``ValueError`` before any SQL for an
    invalid input (for example two rows with the same natural key). An empty
    input does nothing. Never commits.
    """
    materialized = [dict(row) for row in rows]
    if not materialized:
        return UpsertResult()
    table = _table_of(model)
    columns = _validate(table, materialized, key)

    statement = insert(table).values(materialized)
    excluded = statement.excluded
    changing = [name for name in columns if name not in key]
    if changing:
        assignments: dict[str, Any] = {name: excluded[name] for name in changing}
        if "updated_at" in table.c:
            assignments["updated_at"] = func.now()
        if issubclass(model, VersionedMixin):
            assignments["version"] = table.c.version + 1
        statement = statement.on_conflict_do_update(
            index_elements=list(key),
            set_=assignments,
            where=or_(
                *(table.c[name].is_distinct_from(excluded[name]) for name in changing)
            ),
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=list(key))

    # xmax is 0 only for a row this statement inserted; untouched rows are not
    # returned at all.
    result = await session.scalars(
        statement.returning(literal_column("xmax = 0", Boolean))
    )
    flags = [bool(flag) for flag in result]
    inserted = sum(flags)
    return UpsertResult(inserted=inserted, updated=len(flags) - inserted)
