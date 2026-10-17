"""Declarative base, shared columns and optimistic locking (spec §10, §16).

Domain models subclass :class:`Model`, which gives them:

- ``id``: a ``UUID`` primary key. New rows get a UUIDv7, from the ORM
  (``uuid.uuid7()``) and from the server default (``uuidv7()``, PostgreSQL 18;
  ADR-0001), so raw-SQL inserts get v7 keys too.
- ``created_at`` and ``updated_at``: ``TIMESTAMPTZ NOT NULL`` set by the
  database (``now()``); ``updated_at`` is set again on every ORM update
  (ADR-0004).

Models that need optimistic concurrency also mix in :class:`VersionedMixin`
(architecture §4.4). A stale update raises :class:`StaleVersionError`.

Constraint and index names follow :data:`NAMING_CONVENTION`, so they never
depend on PostgreSQL defaults. Every model module is imported through
``app.core.model_registry`` so Alembic sees it.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class StaleVersionError(Exception):
    """The row changed since it was read (optimistic version conflict).

    Raised when a versioned update matches no row (another transaction
    committed first) and by :func:`ensure_version` when the client's version
    is out of date. Carries no record data.
    """

    def __init__(self) -> None:
        super().__init__("stale version")


class StandardColumns:
    """``id``, ``created_at`` and ``updated_at`` for every domain model."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid7,
        server_default=text("uuidv7()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @declared_attr.directive
    def __mapper_args__(cls: type[Any]) -> dict[str, Any]:
        # eager_defaults: database-set values (created_at, updated_at) are read
        # back with RETURNING, so they never need a lazy load after a flush.
        args: dict[str, Any] = {"eager_defaults": True}
        if issubclass(cls, VersionedMixin):
            args["version_id_col"] = cls.version
        return args


class VersionedMixin:
    """Opt-in optimistic-lock ``version`` column, starting at 1.

    SQLAlchemy adds ``WHERE version = <loaded>`` to every update and increments
    it by one; an update that matches no row raises ``StaleDataError``, which
    the Unit of Work turns into :class:`StaleVersionError`.
    """

    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )


def ensure_version(entity: VersionedMixin, expected: int) -> None:
    """Raise :class:`StaleVersionError` unless ``entity`` is at ``expected``.

    Call it before changing an entity whose version the client supplied.
    """
    if entity.version != expected:
        raise StaleVersionError()


class Base(DeclarativeBase):
    """The application's declarative base; ``Base.metadata`` is Alembic's target."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Model(StandardColumns, Base):
    """Abstract base of every domain model."""

    __abstract__ = True
