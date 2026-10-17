"""Test-only models on their own declarative base (C9).

They reuse the application's naming convention and mixins, so they behave like
domain models, but ``app.core.models.Base.metadata`` stays empty.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, MetaData, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.models import NAMING_CONVENTION, StandardColumns, VersionedMixin


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
