"""Test-only models on their own declarative base (C9).

They reuse the application's naming convention and mixins, so they behave like
domain models, but ``app.core.models.Base.metadata`` stays empty.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, MetaData, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import (
    NAMING_CONVENTION,
    SoftDeleteMixin,
    StandardColumns,
    VersionedMixin,
)
from app.core.money import Money, MoneyType


class TestBase(DeclarativeBase):
    __test__ = False  # not a pytest test class
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Widget(StandardColumns, TestBase):
    __tablename__ = "widgets"

    name: Mapped[str] = mapped_column(String(100))


class VersionedWidget(VersionedMixin, StandardColumns, TestBase):
    __tablename__ = "versioned_widgets"

    name: Mapped[str] = mapped_column(String(100))


class DeferredCode(StandardColumns, TestBase):
    """Its unique constraint is checked only at COMMIT (B3 failed-commit case)."""

    __tablename__ = "deferred_codes"
    __table_args__ = (UniqueConstraint("code", deferrable=True, initially="DEFERRED"),)

    code: Mapped[str] = mapped_column(String(20))


class SeedReference(StandardColumns, TestBase):
    """Reference rows for the seed tests (M1-17): natural key ``code``.

    ``note`` is never listed by the test seeds, so the tests can check that a
    seed leaves columns it does not list unchanged.
    """

    __tablename__ = "seed_references"

    code: Mapped[str] = mapped_column(String(40), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(100), nullable=True)


class VersionedSeedReference(VersionedMixin, StandardColumns, TestBase):
    """Like :class:`SeedReference`, with optimistic locking (``version``)."""

    __tablename__ = "versioned_seed_references"

    code: Mapped[str] = mapped_column(String(40), unique=True)
    label: Mapped[str] = mapped_column(String(100))


class SampleProfile(StandardColumns, TestBase):
    """Sample model for the factory tests (ADR-0004 column types)."""

    __tablename__ = "sample_profiles"

    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    starts_on: Mapped[date] = mapped_column(Date)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditedThing(Audited, SoftDeleteMixin, VersionedMixin, StandardColumns, TestBase):
    """The audit capture tests' model (M2-04 C8).

    ``api_token_hash`` is excluded (never in an audit row), ``pin_secret`` is
    masked; the other columns cover the C3 value types.
    """

    __tablename__ = "audited_things"
    __audit_exclude__ = frozenset({"api_token_hash"})
    __audit_mask__ = frozenset({"pin_secret"})

    name: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner_ref: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    api_token_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    pin_secret: Mapped[str | None] = mapped_column(Text, nullable=True)


class MoneyThing(Audited, StandardColumns, TestBase):
    """The money mapping tests' model (M6-02 E2, E3): ``MoneyType`` columns."""

    __tablename__ = "money_things"

    amount: Mapped[Money] = mapped_column(MoneyType())
    refund: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
