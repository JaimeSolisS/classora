"""Price catalog tables (#89; ``_docs/data-model.md`` §3.4, §3.5, §5.2;
ADR-0006 "Price catalog").

- ``service_prices``: one catalog item, keyed by ``(class_type,
  pricing_model, subject_id, education_level_id, duration_minutes,
  cohort_id, group_id)``. ``education_level_id`` and ``duration_minutes``
  ``NULL`` mean "any"; ``cohort_id``/``group_id`` scope an item to one
  cohort or group. The key is unique with ``NULLS NOT DISTINCT`` except for
  ``package`` rows (identified by their product, #94). The key never changes
  once written: a different key is a new item. Audited and versioned.
- ``price_versions``: the effective-dated prices of an item. Each runs from
  ``effective_from`` until the next one's (``UNIQUE (service_price_id,
  effective_from)``); a ``NULL`` amount means "not offered from this date".
  Audited, no ``version``: the app never edits a version.

**Guard** (created by migration 0060, #89 E2): the trigger
``price_versions_no_referenced_change`` refuses ``UPDATE`` and ``DELETE``
of a version for which ``price_version_is_referenced(id)`` is true
(SQLSTATE 42501), and ``price_versions_no_truncate`` refuses every
``TRUNCATE``. In #89 the predicate is ``SELECT false``; #90 (charges) and
#94 (packages) replace it. Every foreign key is ``ON DELETE RESTRICT``.
"""

import uuid
from datetime import date
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin
from app.core.money import Money, MoneyType

CLASS_TYPES: Final = ("individual", "group", "course")
PRICING_MODELS: Final = ("per_session", "full_course", "per_period", "package")
ALLOWED_MODELS: Final[dict[str, tuple[str, ...]]] = {
    "individual": ("per_session", "per_period", "package"),
    "group": ("per_session", "full_course", "per_period"),
    "course": ("full_course",),
}
"""The pricing models each class type allows (#89 E5)."""

DURATION_MIN: Final = 15
DURATION_MAX: Final = 480
"""The ``duration_minutes`` range (the ``class_sessions`` range, #89 E5)."""

CATALOG_KEY_INDEX: Final = "uq_service_prices_catalog_key"
VERSION_UNIQUE: Final = "uq_price_versions_service_price_id_effective_from"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


_MODEL_ALLOWED: Final = " OR ".join(
    f"(class_type = '{class_type}' AND {_in('pricing_model', models)})"
    for class_type, models in ALLOWED_MODELS.items()
)


class ServicePrice(Audited, VersionedMixin, Model):
    """A catalog item (#89 A3; data-model §3.4)."""

    __tablename__ = "service_prices"
    __table_args__ = (
        CheckConstraint(_in("class_type", CLASS_TYPES), name="class_type_valid"),
        CheckConstraint(
            _in("pricing_model", PRICING_MODELS), name="pricing_model_valid"
        ),
        CheckConstraint(_MODEL_ALLOWED, name="pricing_model_allowed"),
        CheckConstraint(
            "NOT (cohort_id IS NOT NULL AND group_id IS NOT NULL)", name="one_scope"
        ),
        CheckConstraint(
            "cohort_id IS NULL OR class_type = 'course'", name="cohort_scope"
        ),
        CheckConstraint("group_id IS NULL OR class_type = 'group'", name="group_scope"),
        CheckConstraint(
            "pricing_model <> 'package' OR class_type = 'individual'",
            name="package_individual",
        ),
        CheckConstraint(
            "subject_id IS NOT NULL OR pricing_model = 'package'",
            name="subject_required",
        ),
        CheckConstraint(
            "duration_minutes IS NULL OR (pricing_model = 'per_session'"
            f" AND duration_minutes BETWEEN {DURATION_MIN} AND {DURATION_MAX})",
            name="duration_valid",
        ),
        CheckConstraint(
            "(cohort_id IS NULL AND group_id IS NULL)"
            " OR (education_level_id IS NULL AND duration_minutes IS NULL)",
            name="scoped_any_level_duration",
        ),
        Index(
            CATALOG_KEY_INDEX,
            "class_type",
            "pricing_model",
            "subject_id",
            "education_level_id",
            "duration_minutes",
            "cohort_id",
            "group_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("pricing_model <> 'package'"),
        ),
    )

    class_type: Mapped[str] = mapped_column(String(16))
    pricing_model: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=True,
    )
    education_level_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("education_levels.id", ondelete="RESTRICT"),
        nullable=True,
    )
    duration_minutes: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    cohort_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_cohorts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_groups.id", ondelete="RESTRICT"),
        nullable=True,
    )


class PriceVersion(Audited, Model):
    """One effective-dated price of an item (#89 A4; data-model §3.5)."""

    __tablename__ = "price_versions"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        UniqueConstraint("service_price_id", "effective_from", name=VERSION_UNIQUE),
    )

    service_price_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_prices.id", ondelete="RESTRICT")
    )
    effective_from: Mapped[date] = mapped_column(Date)
    amount: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
