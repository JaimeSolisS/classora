"""Test-only models on their own declarative base (C9).

They reuse the application's naming convention and mixins, so they behave like
domain models, but ``app.core.models.Base.metadata`` stays empty.
"""

from sqlalchemy import MetaData, String, UniqueConstraint
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
