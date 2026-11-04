"""Finance tables (#114; ADR-0017 E12, E15; data-model §12.1, §12.2).

``expense_categories`` and ``vendors`` are the expense configuration. Both are
``Audited`` with no mask: neither holds an amount (#114 E6). A name is unique
ignoring case (the ``lower(name)`` index), so an insert or a rename goes
through a savepoint that maps the index to a ``409`` (the ``Audited`` rule: no
``ON CONFLICT``). Neither table has a delete: ``is_active`` retires a row, and
a trigger refuses ``TRUNCATE`` (migration ``0083``).

A category's ``system_key`` marks one of the five rows the migration seeds.
While a row holds it, clients show the catalog label of the key in the viewer's
language; a rename clears it, and nothing ever sets it again (#114 E3).
"""

from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin

CATEGORY_NAME_MAX: Final = 100
VENDOR_NAME_MAX: Final = 200
VENDOR_CONTACT_MAX: Final = 500
SYSTEM_KEY_MAX: Final = 20

SYSTEM_KEYS: Final = ("rent", "utilities", "supplies", "equipment", "maintenance")
"""The seeded categories' keys, in seed order (#114 E3)."""

CATEGORY_NAME_KEY: Final = "uq_expense_categories_lower_name"
VENDOR_NAME_KEY: Final = "uq_vendors_lower_name"


def _trimmed(column: str, maximum: int) -> str:
    return (
        f"char_length({column}) BETWEEN 1 AND {maximum} AND {column} = btrim({column})"
    )


def _active_column() -> Mapped[bool]:
    return mapped_column(Boolean, default=True, server_default=text("true"))


class ExpenseCategory(Audited, VersionedMixin, Model):
    """An expense category (data-model §12.1). Reports group by its ``id``, so a
    rename never moves a past expense."""

    __tablename__ = "expense_categories"
    __table_args__ = (
        CheckConstraint(_trimmed("name", CATEGORY_NAME_MAX), name="name_trimmed"),
        CheckConstraint(
            "system_key IN (" + ", ".join(f"'{key}'" for key in SYSTEM_KEYS) + ")",
            name="system_key_valid",
        ),
        UniqueConstraint("system_key"),
        Index(CATEGORY_NAME_KEY, func.lower(text("name")), unique=True),
    )

    name: Mapped[str] = mapped_column(String(CATEGORY_NAME_MAX))
    system_key: Mapped[str | None] = mapped_column(String(SYSTEM_KEY_MAX))
    is_active: Mapped[bool] = _active_column()


class Vendor(Audited, VersionedMixin, Model):
    """A vendor (data-model §12.2). ``contact`` is free text: a phone, an email
    or a person, shown to ``expense.configure`` holders only."""

    __tablename__ = "vendors"
    __table_args__ = (
        CheckConstraint(_trimmed("name", VENDOR_NAME_MAX), name="name_trimmed"),
        CheckConstraint(
            _trimmed("contact", VENDOR_CONTACT_MAX), name="contact_trimmed"
        ),
        Index(VENDOR_NAME_KEY, func.lower(text("name")), unique=True),
    )

    name: Mapped[str] = mapped_column(String(VENDOR_NAME_MAX))
    contact: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = _active_column()
