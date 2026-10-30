"""Billing tables: the price catalog (#89), the ledger core (#90) and the
billing settings (#91; ``_docs/data-model.md`` §3; ADR-0006).

**Price catalog** (#89; data-model §3.4, §3.5, §5.2; ADR-0006 "Price
catalog"):

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
``TRUNCATE``. Migration 0060 created the predicate as ``SELECT false``;
migration 0061 (#90) makes it ``EXISTS`` a charge with that
``price_version_id``, and 0066 (#94) adds packages.

**Ledger core** (#90; data-model §3.1, §3.3, §3.9, §3.10, §3.12–§3.16,
§3.26, §4, §5.1, §6):

- ``billing_accounts`` (mutable config, versioned): one per family
  (``family_id`` UNIQUE), created on first use; ``due_offset_days`` 0–60.
  Every financial mutation locks it ``FOR UPDATE`` as its last lock.
- ``payment_methods`` (mutable reference, versioned): keyed by ``code``,
  seeded ``cash``, ``transfer``, ``other`` by migration 0061 (#90 E2).
- Append-only (no ``version``; migration 0061 creates the
  ``<table>_append_only()`` triggers that refuse ``UPDATE``, ``DELETE`` and
  ``TRUNCATE`` with SQLSTATE 42501): ``charges``, ``charge_adjustments``,
  ``payments``, ``payment_reversals``, ``payment_allocations``,
  ``allocation_releases``, ``refunds``.
- Staged columns (#90 E3): ``charges.tuition_plan_id`` (#93),
  ``charges.package_id`` (#94), ``charges.custom_price_id`` (#95) and
  ``refunds.withdrawal_id`` (#101) are added by those issues, which replace
  the named ``ck_charges_kind`` and ``ck_charges_one_source`` CHECKs.
- Composite foreign keys keep an allocation, its payment and its charge in
  one account (I4), and an adjustment on its charge's account and student
  (I5). The view ``billing_ledger_entries`` is defined only in the
  migration (``balances.LEDGER_VIEW`` reads it, outside the metadata).

**Billing settings** (#91; data-model §3.2): ``billing_settings``, a
singleton (mutable config, versioned) holding the billing start floor
``billing_starts_on`` (``NULL``: the sweepers post nothing).

**Discounts and custom prices** (#95; data-model §3.6-§3.8, §5.2):

- ``custom_prices`` (mutable config, versioned, guarded once referenced): a
  student's or a family's price for one catalog item, from
  ``effective_from`` to ``expires_on`` (inclusive, ``NULL`` = open), never
  overlapping for one target and item (two ``EXCLUDE USING gist``).
  Migration 0063's trigger ``custom_prices_no_referenced_change`` refuses
  every change but ``expires_on`` (and ``version``/``updated_at``) and every
  ``DELETE`` of a row a charge references (SQLSTATE 42501);
  ``custom_prices_no_truncate`` refuses ``TRUNCATE``.
- ``discounts`` (mutable config, versioned, never deleted): a discount,
  scholarship, sibling discount or promotion (``kind``), a percentage or a
  fixed amount, structured ``eligibility``, an ``audience`` (``all`` or
  ``assigned``) and an optional date window.
- ``discount_assignments`` (mutable config, no ``version``): who an
  ``assigned`` discount is for (a student or a family).
- ``charge_discounts`` (append-only, the §5.1 triggers): the discounts
  applied to a charge, snapshotted at posting (kind, name, percent or fixed
  amount, the line amount, the position).
- ``charges.custom_price_id`` (column added by #95): the custom price used;
  at most one of it and ``price_version_id`` (``ck_charges_one_price``), never
  on a ``manual`` charge (``ck_charges_manual_unpriced``).

**Tuition plans** (#93; data-model §3.17, §3.17a):

- ``tuition_plans`` (mutable config, versioned): a student's recurring
  plan, pinned to one ``per_period`` catalog item and bound to the family
  account it bills (K9); optionally linked to a group enrollment (K6).
- ``tuition_plan_pauses`` (mutable config, versioned): period-aligned
  pauses (K4).
- ``charges.tuition_plan_id`` (column added by #93): the source of a
  ``tuition_period`` charge, with the composite foreign key to the plan's
  ``(id, billing_account_id, student_id)``.

**Prepaid packages** (#94; data-model §3.18-§3.20):

- ``package_products`` (mutable config, versioned): a product, priced by the
  versions of its own ``service_prices`` row (``package``, UNIQUE); unit,
  quantity, validity and subject are fixed after creation.
- ``packages`` (append-only): one purchase for one account and one student.
- ``package_credit_transactions`` (append-only): the signed unit movements
  (``grant``, ``consume``, ``expire``, ``reversal``; #101 adds ``refund``).
- ``charges.package_id`` (column added by #94): the source of a
  ``package_purchase`` charge, with the composite foreign key to the
  package's ``(id, billing_account_id, student_id)``.

**Due dates and reminders** (#97; data-model §3.11, §3.27):

- ``charge_due_date_changes`` (append-only, audited): a staff change of a
  posted charge's due date, with the effective date before it
  (``previous_due_date``) and a reason. The latest row (``created_at``,
  ``id``) is the charge's effective due date.
- ``charge_reminders`` (append-only, **not** audited, like
  ``notifications``: an operational log written with ``INSERT … ON
  CONFLICT DO NOTHING``, which the audit guard refuses on audited tables):
  one row per ``(charge_id, stage)`` the reminder job sent.

**Receipts** (#98; data-model §3.21): ``receipts`` (append-only, audited):
one per payment (``payment_id`` UNIQUE), numbered ``R-YYYY-NNNNNN``
(``number`` UNIQUE) from the core ``number_series`` (``app.core.numbering``),
with its language, template version and frozen ``snapshot``
(``billing.receipts.ReceiptSnapshotV1``); the composite foreign key keeps it
on its payment's account (I4).

Every model but ``ChargeReminder`` is ``Audited``; every foreign key is ``ON
DELETE RESTRICT`` except ``tuition_plan_pauses.tuition_plan_id``
(``CASCADE``).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.audit import Audited
from app.core.models import Base, Model, VersionedMixin
from app.core.money import Money, MoneyType
from app.modules.billing.reminders import REMINDER_STAGES

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

PERIOD_FREQUENCIES: Final = ("weekly", "biweekly", "monthly")
"""The period lengths of a ``per_period`` item and a tuition plan (#93 K2,
K3), in list order."""
PERIOD_FREQUENCY_MAX: Final = 16

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
        CheckConstraint(
            "(period_frequency IS NULL"
            f" OR {_in('period_frequency', PERIOD_FREQUENCIES)})"
            " AND ((pricing_model = 'per_period') = (period_frequency IS NOT NULL))",
            name="period_frequency",
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
            "period_frequency",
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
    period_frequency: Mapped[str | None] = mapped_column(
        String(PERIOD_FREQUENCY_MAX), nullable=True
    )
    """``weekly | biweekly | monthly``, set iff ``pricing_model =
    'per_period'`` (#93 K2; part of the catalog key)."""


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


# --- Ledger core (#90) ---------------------------------------------------------------

DUE_OFFSET_MAX: Final = 60
"""``billing_accounts.due_offset_days`` runs from 0 to this (E11)."""

CHARGE_KINDS: Final = (
    "session",
    "group_enrollment",
    "cohort_enrollment",
    "tuition_period",
    "package_purchase",
    "manual",
)
"""The charge kinds whose source exists (#90 E3; ``tuition_period`` since
#93, ``package_purchase`` since #94)."""

ADJUSTMENT_KINDS: Final = (
    "duration_change",
    "cancellation_credit",
    "closure_credit",
    "enrollment_removal",
    "withdrawal",
    "manual",
)
MANUAL: Final = "manual"

SCHEDULED_MINUTES_MIN: Final = 15
SCHEDULED_MINUTES_MAX: Final = 480
"""The ``class_sessions`` duration range, also checked on a session charge."""

DESCRIPTION_MAX: Final = 200
REASON_MAX: Final = 500
SOURCE_KEY_MAX: Final = 200
REFERENCE_MAX: Final = 100
PAYER_NAME_MAX: Final = 200
NOTE_MAX: Final = 500
PAYMENT_METHOD_CODE_MAX: Final = 32
KIND_MAX: Final = 24
ENROLLMENT_CHARGE_KINDS: Final = ("group_enrollment", "cohort_enrollment")
"""The enrollment fee kinds (#92), the only ones with a proration."""
PRORATION_CHECK: Final = (
    "num_nulls(proration_remaining, proration_total) = 2"
    " OR (num_nulls(proration_remaining, proration_total) = 0"
    " AND kind IN ('group_enrollment', 'cohort_enrollment')"
    " AND proration_total >= 1"
    " AND proration_remaining BETWEEN 0 AND proration_total)"
)
"""``ck_charges_proration`` (#92 J8, migration 0064)."""

ACCOUNT_FAMILY_UNIQUE: Final = "uq_billing_accounts_family_id"
ACCOUNT_FAMILY_FK: Final = "fk_billing_accounts_family_id_families"
CHARGE_SESSION_KEY: Final = "uq_charges_session_student"
CHARGE_GROUP_ENROLLMENT_KEY: Final = "uq_charges_group_enrollment"
CHARGE_COHORT_ENROLLMENT_KEY: Final = "uq_charges_cohort_enrollment"
CHARGE_TUITION_PERIOD_KEY: Final = "uq_charges_tuition_period"
CHARGE_TUITION_PLAN_FK: Final = "fk_charges_tuition_plan"
CHARGE_PACKAGE_PURCHASE_KEY: Final = "uq_charges_package_purchase"
CHARGE_PACKAGE_FK: Final = "fk_charges_package"
CHARGE_NATURAL_KEYS: Final = (
    CHARGE_SESSION_KEY,
    CHARGE_GROUP_ENROLLMENT_KEY,
    CHARGE_COHORT_ENROLLMENT_KEY,
    CHARGE_TUITION_PERIOD_KEY,
    CHARGE_PACKAGE_PURCHASE_KEY,
)
"""The named natural-key indexes of ``charges`` (J4): a violation of one of
them means "already posted" (#90 E4)."""
ADJUSTMENT_SOURCE_KEY: Final = "uq_charge_adjustments_source_key"

APPEND_ONLY_TABLES: Final = (
    "charges",
    "charge_adjustments",
    "payments",
    "payment_reversals",
    "payment_allocations",
    "allocation_releases",
    "refunds",
)
"""The tables migration 0061 makes append-only (data-model §5.1)."""


def staff_text_check(column: str, max_length: int) -> str:
    """Staff text: ``NULL``, or trimmed and 1 to ``max_length`` characters
    (#90 A2)."""
    return (
        f"{column} IS NULL OR (length(btrim({column})) BETWEEN 1 AND {max_length}"
        f" AND {column} = btrim({column}))"
    )


ONE_SOURCE: Final = (
    "num_nonnulls(class_session_id, group_enrollment_id, cohort_enrollment_id,"
    " tuition_plan_id, package_id) = CASE WHEN kind = 'manual' THEN 0 ELSE 1 END"
    " AND (kind = 'session') = (class_session_id IS NOT NULL)"
    " AND (kind = 'session') = (scheduled_minutes IS NOT NULL)"
    " AND (kind = 'group_enrollment') = (group_enrollment_id IS NOT NULL)"
    " AND (kind = 'cohort_enrollment') = (cohort_enrollment_id IS NOT NULL)"
    " AND (kind = 'tuition_period') = (tuition_plan_id IS NOT NULL)"
    " AND (kind = 'tuition_period') = (service_period_end IS NOT NULL)"
    " AND (kind = 'package_purchase') = (package_id IS NOT NULL)"
    " AND (kind = 'manual') = (description IS NOT NULL)"
)
"""``ck_charges_one_source`` (#90 A2, E3; #93 A5; #94 A5): exactly the source
of the kind, the minutes of a session charge only, the period end of a
tuition charge only, the description of a manual charge only."""


def _user_fk(**kwargs: Any) -> Any:
    return ForeignKey("users.id", ondelete="RESTRICT", **kwargs)


def _uuid_fk(target: str, *, nullable: bool = False) -> Mapped[Any]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{target}.id", ondelete="RESTRICT"),
        nullable=nullable,
    )


def _actor(*, nullable: bool) -> Mapped[Any]:
    return mapped_column(UUID(as_uuid=True), _user_fk(), nullable=nullable)


class BillingAccount(Audited, VersionedMixin, Model):
    """A family's billing account (#90 A2; data-model §3.1)."""

    __tablename__ = "billing_accounts"
    __table_args__ = (
        CheckConstraint(
            f"due_offset_days BETWEEN 0 AND {DUE_OFFSET_MAX}",
            name="due_offset_days_range",
        ),
        UniqueConstraint("family_id", name=ACCOUNT_FAMILY_UNIQUE),
    )

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("families.id", ondelete="RESTRICT", name=ACCOUNT_FAMILY_FK),
    )
    due_offset_days: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))


PAYMENT_METHOD_NAMESPACE: Final = uuid.UUID("0b9e6f1c-4f7a-5d0e-9a51-6c0d1f6a9e90")
"""The UUIDv5 namespace of :attr:`PaymentMethod.id` (#90 D1)."""


class PaymentMethod(Audited, VersionedMixin, Base):
    """A payment method (#90 A2, E2; data-model §3.3). The key is ``code``
    (data-model §2), so this model has no ``id`` column: :attr:`id` is a
    UUIDv5 of the code, the ``record_id`` of its audit events."""

    __tablename__ = "payment_methods"
    __table_args__ = (
        CheckConstraint("code ~ '^[a-z][a-z0-9_]*$'", name="code_format"),
    )

    code: Mapped[str] = mapped_column(String(PAYMENT_METHOD_CODE_MAX), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
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
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"eager_defaults": True, "version_id_col": cls.version}

    @property
    def id(self) -> uuid.UUID:
        """The audit ``record_id`` of this method (a UUIDv5 of ``code``)."""
        return payment_method_record_id(self.code)


def payment_method_record_id(code: str) -> uuid.UUID:
    """The audit ``record_id`` of the payment method ``code``."""
    return uuid.uuid5(PAYMENT_METHOD_NAMESPACE, code)


class Charge(Audited, Model):
    """What a family owes for one student and one thing (#90 A2; data-model
    §3.9). Append-only."""

    __tablename__ = "charges"
    __table_args__ = (
        CheckConstraint(_in("kind", CHARGE_KINDS), name="kind"),
        CheckConstraint(ONE_SOURCE, name="one_source"),
        CheckConstraint(
            staff_text_check("description", DESCRIPTION_MAX), name="description_valid"
        ),
        CheckConstraint(
            "service_period_end IS NULL OR service_period_end >= service_date",
            name="service_period_end_valid",
        ),
        CheckConstraint(
            "scheduled_minutes IS NULL OR scheduled_minutes BETWEEN"
            f" {SCHEDULED_MINUTES_MIN} AND {SCHEDULED_MINUTES_MAX}",
            name="scheduled_minutes_range",
        ),
        CheckConstraint("list_amount >= 0", name="list_amount_non_negative"),
        CheckConstraint("discount_amount >= 0", name="discount_amount_non_negative"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint(
            "amount = list_amount - discount_amount", name="amount_equation"
        ),
        UniqueConstraint("id", "billing_account_id", name="uq_charges_id_account"),
        UniqueConstraint(
            "id",
            "billing_account_id",
            "student_id",
            name="uq_charges_id_account_student",
        ),
        Index(
            CHARGE_SESSION_KEY,
            "class_session_id",
            "student_id",
            unique=True,
            postgresql_where=text("kind = 'session'"),
        ),
        Index(
            CHARGE_GROUP_ENROLLMENT_KEY,
            "group_enrollment_id",
            unique=True,
            postgresql_where=text("kind = 'group_enrollment'"),
        ),
        Index(
            CHARGE_COHORT_ENROLLMENT_KEY,
            "cohort_enrollment_id",
            unique=True,
            postgresql_where=text("kind = 'cohort_enrollment'"),
        ),
        Index("ix_charges_account_posted_at", "billing_account_id", "posted_at", "id"),
        Index("ix_charges_student_service_date", "student_id", "service_date"),
        Index("ix_charges_account_due_date", "billing_account_id", "due_date"),
        Index(
            "ix_charges_price_version_id",
            "price_version_id",
            postgresql_where=text("price_version_id IS NOT NULL"),
        ),
        CheckConstraint(
            "num_nonnulls(price_version_id, custom_price_id) <= 1", name="one_price"
        ),
        CheckConstraint(
            "kind <> 'manual' OR custom_price_id IS NULL", name="manual_unpriced"
        ),
        Index(
            "ix_charges_custom_price_id",
            "custom_price_id",
            postgresql_where=text("custom_price_id IS NOT NULL"),
        ),
        CheckConstraint(PRORATION_CHECK, name="proration"),
        ForeignKeyConstraint(
            ["tuition_plan_id", "billing_account_id", "student_id"],
            [
                "tuition_plans.id",
                "tuition_plans.billing_account_id",
                "tuition_plans.student_id",
            ],
            name=CHARGE_TUITION_PLAN_FK,
            ondelete="RESTRICT",
        ),
        Index(
            CHARGE_TUITION_PERIOD_KEY,
            "tuition_plan_id",
            "service_date",
            unique=True,
            postgresql_where=text("kind = 'tuition_period'"),
        ),
        ForeignKeyConstraint(
            ["package_id", "billing_account_id", "student_id"],
            [
                "packages.id",
                "packages.billing_account_id",
                "packages.student_id",
            ],
            name=CHARGE_PACKAGE_FK,
            ondelete="RESTRICT",
        ),
        Index(
            CHARGE_PACKAGE_PURCHASE_KEY,
            "package_id",
            unique=True,
            postgresql_where=text("kind = 'package_purchase'"),
        ),
    )

    billing_account_id: Mapped[uuid.UUID] = _uuid_fk("billing_accounts")
    student_id: Mapped[uuid.UUID] = _uuid_fk("students")
    kind: Mapped[str] = mapped_column(String(KIND_MAX))
    class_session_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "class_sessions", nullable=True
    )
    group_enrollment_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "group_enrollments", nullable=True
    )
    cohort_enrollment_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "cohort_enrollments", nullable=True
    )
    tuition_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    """The source of a ``tuition_period`` charge (#93; the composite foreign
    key ``fk_charges_tuition_plan`` keeps it on its plan's account and
    student)."""
    package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    """The source of a ``package_purchase`` charge (#94; the composite foreign
    key ``fk_charges_package`` keeps it on its package's account and
    student)."""
    service_date: Mapped[date] = mapped_column(Date)
    service_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    price_version_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "price_versions", nullable=True
    )
    custom_price_id: Mapped[uuid.UUID | None] = _uuid_fk("custom_prices", nullable=True)
    """The custom price used (#95; ``NULL`` for a catalog-priced or manual
    charge)."""
    education_level_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "education_levels", nullable=True
    )
    scheduled_minutes: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    list_amount: Mapped[Money] = mapped_column(MoneyType())
    discount_amount: Mapped[Money] = mapped_column(
        MoneyType(), server_default=text("0")
    )
    amount: Mapped[Money] = mapped_column(MoneyType())
    due_date: Mapped[date] = mapped_column(Date)
    proration_remaining: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    """The sessions left at enrollment (#92 J3, J8; enrollment fees only)."""
    proration_total: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    """The proration denominator (#92 J3, J8): the cohort snapshot's
    ``session_count`` or a finite group's sessions; ``NULL`` for an
    open-ended group fee and every other kind."""
    description: Mapped[str | None] = mapped_column(
        String(DESCRIPTION_MAX), nullable=True
    )
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    posted_by_user_id: Mapped[uuid.UUID | None] = _actor(nullable=True)


class ChargeAdjustment(Audited, Model):
    """A signed correction against one charge (#90 A2; data-model §3.10).
    Append-only."""

    __tablename__ = "charge_adjustments"
    __table_args__ = (
        CheckConstraint(_in("kind", ADJUSTMENT_KINDS), name="kind"),
        CheckConstraint("amount <> 0", name="amount_non_zero"),
        CheckConstraint(staff_text_check("reason", REASON_MAX), name="reason_valid"),
        CheckConstraint(
            "kind <> 'manual' OR reason IS NOT NULL", name="manual_reason_required"
        ),
        CheckConstraint(
            "kind = 'manual' OR source_key IS NOT NULL", name="source_key_required"
        ),
        ForeignKeyConstraint(
            ["charge_id", "billing_account_id", "student_id"],
            ["charges.id", "charges.billing_account_id", "charges.student_id"],
            name="fk_charge_adjustments_charge_id_charges",
            ondelete="RESTRICT",
        ),
        Index(
            ADJUSTMENT_SOURCE_KEY,
            "source_key",
            unique=True,
            postgresql_where=text("source_key IS NOT NULL"),
        ),
        Index("ix_charge_adjustments_charge_id", "charge_id"),
    )

    charge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    billing_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(KIND_MAX))
    amount: Mapped[Money] = mapped_column(MoneyType())
    reason: Mapped[str | None] = mapped_column(String(REASON_MAX), nullable=True)
    source_key: Mapped[str | None] = mapped_column(
        String(SOURCE_KEY_MAX), nullable=True
    )
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    posted_by_user_id: Mapped[uuid.UUID | None] = _actor(nullable=True)


def _payment_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["payment_id", "billing_account_id"],
        ["payments.id", "payments.billing_account_id"],
        name=f"fk_{table}_payment_id_payments",
        ondelete="RESTRICT",
    )


def _method_fk() -> Any:
    return ForeignKey("payment_methods.code", ondelete="RESTRICT")


class Payment(Audited, Model):
    """An external payment recorded by staff (#90 A2; data-model §3.12).
    Append-only; recorded from #96."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            staff_text_check("reference", REFERENCE_MAX), name="reference_valid"
        ),
        CheckConstraint(
            staff_text_check("payer_name", PAYER_NAME_MAX), name="payer_name_valid"
        ),
        CheckConstraint(staff_text_check("note", NOTE_MAX), name="note_valid"),
        UniqueConstraint("id", "billing_account_id", name="uq_payments_id_account"),
        Index(
            "ix_payments_account_received_on", "billing_account_id", "received_on", "id"
        ),
    )

    billing_account_id: Mapped[uuid.UUID] = _uuid_fk("billing_accounts")
    amount: Mapped[Money] = mapped_column(MoneyType())
    received_on: Mapped[date] = mapped_column(Date)
    payment_method_code: Mapped[str] = mapped_column(
        String(PAYMENT_METHOD_CODE_MAX), _method_fk()
    )
    reference: Mapped[str | None] = mapped_column(String(REFERENCE_MAX), nullable=True)
    payer_name: Mapped[str | None] = mapped_column(
        String(PAYER_NAME_MAX), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(NOTE_MAX), nullable=True)
    recorded_by_user_id: Mapped[uuid.UUID] = _actor(nullable=False)


class PaymentReversal(Audited, Model):
    """The reversal of a payment, at most one per payment (#90 A2;
    data-model §3.13). Append-only; recorded from #100."""

    __tablename__ = "payment_reversals"
    __table_args__ = (
        CheckConstraint(staff_text_check("reason", REASON_MAX), name="reason_valid"),
        UniqueConstraint("payment_id", name="uq_payment_reversals_payment_id"),
        _payment_fk("payment_reversals"),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    billing_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(String(REASON_MAX))
    reversed_by_user_id: Mapped[uuid.UUID] = _actor(nullable=False)


class PaymentAllocation(Audited, Model):
    """Part of a payment applied to one charge of the same account (#90 A2;
    data-model §3.14). Valid while it has no release. Append-only."""

    __tablename__ = "payment_allocations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        _payment_fk("payment_allocations"),
        ForeignKeyConstraint(
            ["charge_id", "billing_account_id"],
            ["charges.id", "charges.billing_account_id"],
            name="fk_payment_allocations_charge_id_charges",
            ondelete="RESTRICT",
        ),
        Index("ix_payment_allocations_charge_id", "charge_id"),
        Index("ix_payment_allocations_payment_id", "payment_id"),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    charge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    billing_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    amount: Mapped[Money] = mapped_column(MoneyType())
    created_by_user_id: Mapped[uuid.UUID | None] = _actor(nullable=True)


class AllocationRelease(Audited, Model):
    """The release of a whole allocation by a reversal or an adjustment
    (#90 A2; data-model §3.15). Append-only."""

    __tablename__ = "allocation_releases"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(payment_reversal_id, charge_adjustment_id) = 1",
            name="one_cause",
        ),
        UniqueConstraint("allocation_id", name="uq_allocation_releases_allocation_id"),
    )

    allocation_id: Mapped[uuid.UUID] = _uuid_fk("payment_allocations")
    payment_reversal_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "payment_reversals", nullable=True
    )
    charge_adjustment_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "charge_adjustments", nullable=True
    )
    released_by_user_id: Mapped[uuid.UUID | None] = _actor(nullable=True)


class Refund(Audited, Model):
    """Money paid back out of a payment's unallocated remainder (#90 A2;
    data-model §3.16 without ``withdrawal_id``, added by #101). Append-only;
    recorded from #100."""

    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            staff_text_check("reference", REFERENCE_MAX), name="reference_valid"
        ),
        CheckConstraint(staff_text_check("reason", REASON_MAX), name="reason_valid"),
        _payment_fk("refunds"),
        Index("ix_refunds_payment_id", "payment_id"),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    billing_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    amount: Mapped[Money] = mapped_column(MoneyType())
    refunded_on: Mapped[date] = mapped_column(Date)
    payment_method_code: Mapped[str] = mapped_column(
        String(PAYMENT_METHOD_CODE_MAX), _method_fk()
    )
    reference: Mapped[str | None] = mapped_column(String(REFERENCE_MAX), nullable=True)
    reason: Mapped[str] = mapped_column(String(REASON_MAX))
    refunded_by_user_id: Mapped[uuid.UUID] = _actor(nullable=False)


# --- Billing settings (#91) ----------------------------------------------------------


class BillingSettings(Audited, VersionedMixin, Model):
    """The one billing settings row (#91 A3; data-model §3.2). Migration
    0062 inserts it with ``billing_starts_on = NULL`` (billing off).
    ``billing_starts_on`` is a center-local date, not an instant: no
    time-zone dependency is registered (#37 E1)."""

    __tablename__ = "billing_settings"
    __table_args__ = (CheckConstraint("singleton", name="singleton_true"),)

    singleton: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), unique=True
    )
    billing_starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)


# --- Discounts and custom prices (#95) -----------------------------------------------

DISCOUNT_KINDS: Final = ("scholarship", "sibling", "discount", "promotion")
"""The discount kinds, in the order they apply (#95 H4)."""
DISCOUNT_AUDIENCES: Final = ("all", "assigned")
DISCOUNT_NAME_MAX: Final = 120
DISCOUNT_NAME_INDEX: Final = "uq_discounts_name_lower"
DISCOUNT_ASSIGNMENT_STUDENT_KEY: Final = "uq_discount_assignments_discount_student"
DISCOUNT_ASSIGNMENT_FAMILY_KEY: Final = "uq_discount_assignments_discount_family"
CUSTOM_PRICE_STUDENT_OVERLAP: Final = "ex_custom_prices_student_overlap"
CUSTOM_PRICE_FAMILY_OVERLAP: Final = "ex_custom_prices_family_overlap"
CUSTOM_PRICE_RANGE_SQL: Final = "daterange(effective_from, expires_on, '[]')"
"""The inclusive validity range of a custom price (``NULL`` end = open)."""


def _percent() -> Numeric[Any]:
    return Numeric(14, 4)


def _overlap(target: Any, where: Any, name: str) -> ExcludeConstraint:
    return ExcludeConstraint(
        (text("service_price_id"), "="),
        (target, "="),
        (text(CUSTOM_PRICE_RANGE_SQL), "&&"),
        name=name,
        using="gist",
        where=where,
    )


class CustomPrice(Audited, VersionedMixin, Model):
    """A student's or a family's price for one catalog item (#95 A2;
    data-model §3.6). Guarded once a charge references it (§5.2)."""

    __tablename__ = "custom_prices"
    __table_args__ = (
        CheckConstraint("num_nonnulls(student_id, family_id) = 1", name="one_target"),
        CheckConstraint(
            "expires_on IS NULL OR expires_on >= effective_from", name="dates"
        ),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        _overlap(
            text("student_id"),
            text("student_id IS NOT NULL"),
            CUSTOM_PRICE_STUDENT_OVERLAP,
        ),
        _overlap(
            text("family_id"),
            text("family_id IS NOT NULL"),
            CUSTOM_PRICE_FAMILY_OVERLAP,
        ),
        Index(
            "ix_custom_prices_student_item",
            "student_id",
            "service_price_id",
            postgresql_where=text("student_id IS NOT NULL"),
        ),
        Index(
            "ix_custom_prices_family_item",
            "family_id",
            "service_price_id",
            postgresql_where=text("family_id IS NOT NULL"),
        ),
    )

    service_price_id: Mapped[uuid.UUID] = _uuid_fk("service_prices")
    student_id: Mapped[uuid.UUID | None] = _uuid_fk("students", nullable=True)
    family_id: Mapped[uuid.UUID | None] = _uuid_fk("families", nullable=True)
    effective_from: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Money] = mapped_column(MoneyType())


class Discount(Audited, VersionedMixin, Model):
    """A reusable discount, scholarship, sibling discount or promotion
    (#95 A3; data-model §3.7). Never deleted: retired with ``is_active``."""

    __tablename__ = "discounts"
    __table_args__ = (
        CheckConstraint(_in("kind", DISCOUNT_KINDS), name="kind_valid"),
        CheckConstraint(_in("audience", DISCOUNT_AUDIENCES), name="audience_valid"),
        CheckConstraint(
            f"length(btrim(name)) BETWEEN 1 AND {DISCOUNT_NAME_MAX}"
            " AND name = btrim(name)",
            name="name_valid",
        ),
        CheckConstraint(
            "percent IS NULL OR (percent > 0 AND percent <= 100)",
            name="percent_range",
        ),
        CheckConstraint("amount IS NULL OR amount > 0", name="amount_positive"),
        CheckConstraint("num_nonnulls(percent, amount) = 1", name="one_value"),
        CheckConstraint(
            "jsonb_typeof(eligibility) = 'object'", name="eligibility_object"
        ),
        CheckConstraint(
            "starts_on IS NULL OR expires_on IS NULL OR starts_on <= expires_on",
            name="dates_order",
        ),
        CheckConstraint(
            "kind <> 'scholarship' OR audience = 'assigned'",
            name="scholarship_assigned",
        ),
        CheckConstraint("kind <> 'sibling' OR audience = 'all'", name="sibling_all"),
        CheckConstraint(
            "kind <> 'promotion' OR (starts_on IS NOT NULL AND expires_on IS NOT NULL)",
            name="promotion_dates",
        ),
        Index(DISCOUNT_NAME_INDEX, func.lower(text("name")), unique=True),
    )

    kind: Mapped[str] = mapped_column(String(16))
    audience: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(DISCOUNT_NAME_MAX))
    percent: Mapped[Decimal | None] = mapped_column(_percent(), nullable=True)
    amount: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    eligibility: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class DiscountAssignment(Audited, Model):
    """A student or a family an ``assigned`` discount is for (#95 A4).
    Deleted by the route (nothing references it)."""

    __tablename__ = "discount_assignments"
    __table_args__ = (
        CheckConstraint("num_nonnulls(student_id, family_id) = 1", name="one_target"),
        Index(
            DISCOUNT_ASSIGNMENT_STUDENT_KEY,
            "discount_id",
            "student_id",
            unique=True,
            postgresql_where=text("student_id IS NOT NULL"),
        ),
        Index(
            DISCOUNT_ASSIGNMENT_FAMILY_KEY,
            "discount_id",
            "family_id",
            unique=True,
            postgresql_where=text("family_id IS NOT NULL"),
        ),
        Index(
            "ix_discount_assignments_student_id",
            "student_id",
            postgresql_where=text("student_id IS NOT NULL"),
        ),
        Index(
            "ix_discount_assignments_family_id",
            "family_id",
            postgresql_where=text("family_id IS NOT NULL"),
        ),
    )

    discount_id: Mapped[uuid.UUID] = _uuid_fk("discounts")
    student_id: Mapped[uuid.UUID | None] = _uuid_fk("students", nullable=True)
    family_id: Mapped[uuid.UUID | None] = _uuid_fk("families", nullable=True)


class ChargeDiscount(Audited, Model):
    """One discount applied to a charge, snapshotted at posting (#95 A5, H8;
    data-model §3.8). Append-only."""

    __tablename__ = "charge_discounts"
    __table_args__ = (
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint(_in("kind", DISCOUNT_KINDS), name="kind_valid"),
        CheckConstraint("num_nonnulls(percent, fixed_amount) = 1", name="one_value"),
        CheckConstraint("amount > 0", name="amount_positive"),
        UniqueConstraint(
            "charge_id", "position", name="uq_charge_discounts_charge_id_position"
        ),
    )

    charge_id: Mapped[uuid.UUID] = _uuid_fk("charges")
    discount_id: Mapped[uuid.UUID] = _uuid_fk("discounts")
    position: Mapped[int] = mapped_column(SmallInteger)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(DISCOUNT_NAME_MAX))
    percent: Mapped[Decimal | None] = mapped_column(_percent(), nullable=True)
    fixed_amount: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    amount: Mapped[Money] = mapped_column(MoneyType())


# --- Tuition plans (#93) -------------------------------------------------------------

TUITION_PLAN_RANGE_SQL: Final = "daterange(starts_on, ends_on, '[]')"
"""The inclusive window of a plan or a pause (``NULL`` end = open)."""
TUITION_PLAN_ENROLLMENT_OVERLAP: Final = "ex_tuition_plans_enrollment_overlap"
TUITION_PLAN_STUDENT_ITEM_OVERLAP: Final = "ex_tuition_plans_student_item_overlap"
TUITION_PAUSE_OVERLAP: Final = "ex_tuition_plan_pauses_overlap"
TUITION_PLAN_COMPOSITE_KEY: Final = "uq_tuition_plans_id_account_student"


class TuitionPlan(Audited, VersionedMixin, Model):
    """A recurring tuition plan (#93 A3; data-model §3.17): one
    ``tuition_period`` charge per period, posted by the daily job.

    The key (``billing_account_id``, ``student_id``, ``service_price_id``,
    ``group_enrollment_id``, ``frequency``, ``starts_on``) never changes
    (migration 0065's trigger ``tuition_plans_key_immutable``, SQLSTATE
    42501); only ``ends_on`` and ``due_offset_days`` do. Two linked plans of
    one enrollment, or two standalone plans of one student and item, never
    overlap (two ``EXCLUDE USING gist``)."""

    __tablename__ = "tuition_plans"
    __table_args__ = (
        CheckConstraint(_in("frequency", PERIOD_FREQUENCIES), name="frequency_valid"),
        CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="dates"),
        CheckConstraint(
            f"due_offset_days BETWEEN 0 AND {DUE_OFFSET_MAX}",
            name="due_offset_days_range",
        ),
        UniqueConstraint(
            "id",
            "billing_account_id",
            "student_id",
            name=TUITION_PLAN_COMPOSITE_KEY,
        ),
        ExcludeConstraint(
            (text("group_enrollment_id"), "="),
            (text(TUITION_PLAN_RANGE_SQL), "&&"),
            name=TUITION_PLAN_ENROLLMENT_OVERLAP,
            using="gist",
            where=text("group_enrollment_id IS NOT NULL"),
        ),
        ExcludeConstraint(
            (text("student_id"), "="),
            (text("service_price_id"), "="),
            (text(TUITION_PLAN_RANGE_SQL), "&&"),
            name=TUITION_PLAN_STUDENT_ITEM_OVERLAP,
            using="gist",
            where=text("group_enrollment_id IS NULL"),
        ),
        Index("ix_tuition_plans_student_id", "student_id"),
        Index("ix_tuition_plans_billing_account_id", "billing_account_id"),
    )

    billing_account_id: Mapped[uuid.UUID] = _uuid_fk("billing_accounts")
    student_id: Mapped[uuid.UUID] = _uuid_fk("students")
    service_price_id: Mapped[uuid.UUID] = _uuid_fk("service_prices")
    group_enrollment_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "group_enrollments", nullable=True
    )
    frequency: Mapped[str] = mapped_column(String(PERIOD_FREQUENCY_MAX))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_offset_days: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))


class TuitionPlanPause(Audited, VersionedMixin, Model):
    """A pause of a tuition plan (#93 A4, K4; data-model §3.17a): from a
    period start to a period end (``NULL`` = until resumed). Pauses of one
    plan never overlap. A period whose start falls inside a pause is never
    charged."""

    __tablename__ = "tuition_plan_pauses"
    __table_args__ = (
        CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="dates"),
        ExcludeConstraint(
            (text("tuition_plan_id"), "="),
            (text(TUITION_PLAN_RANGE_SQL), "&&"),
            name=TUITION_PAUSE_OVERLAP,
            using="gist",
        ),
    )

    tuition_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tuition_plans.id", ondelete="CASCADE")
    )
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)


# --- Prepaid packages (#94) ----------------------------------------------------------

PACKAGE_UNITS: Final = ("sessions", "minutes")
"""A package's unit (#94 A2; ADR-0006 E10)."""
PACKAGE_UNIT_MAX: Final = 8
PACKAGE_NAME_MAX: Final = 120
PACKAGE_TRANSACTION_KINDS: Final = ("grant", "consume", "expire", "reversal")
"""The kinds of ``package_credit_transactions`` (#94 L10: ``refund`` and
``withdrawal_id`` are added by #101, which replaces
``ck_package_credit_transactions_kind``)."""
PACKAGE_APPEND_ONLY_TABLES: Final = ("packages", "package_credit_transactions")
"""The tables migration 0066 makes append-only (data-model §5.1)."""
PACKAGE_COMPOSITE_KEY: Final = "uq_packages_id_account_student"
PACKAGE_GRANT_KEY: Final = "uq_package_credit_transactions_grant"
PACKAGE_EXPIRE_KEY: Final = "uq_package_credit_transactions_expire"
PACKAGE_CONSUME_KEY: Final = "uq_package_credit_transactions_consume"
PACKAGE_REVERSAL_KEY: Final = "uq_package_credit_transactions_reversal"
PACKAGE_REVERSAL_FK: Final = "fk_package_credit_transactions_reverses_transaction_id"


class PackageProduct(Audited, VersionedMixin, Model):
    """A package product (#94 A2; data-model §3.18): its price is the
    versions of its own ``service_prices`` row (``pricing_model =
    'package'``). ``unit``, ``quantity``, ``validity_days`` and
    ``subject_id`` are fixed after creation (#94 L7); only ``name`` and
    ``is_active`` change. Never deleted."""

    __tablename__ = "package_products"
    __table_args__ = (
        CheckConstraint(
            f"length(btrim(name)) BETWEEN 1 AND {PACKAGE_NAME_MAX}"
            " AND name = btrim(name)",
            name="name_valid",
        ),
        CheckConstraint(_in("unit", PACKAGE_UNITS), name="unit_valid"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("validity_days > 0", name="validity_days_positive"),
    )

    service_price_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_prices.id", ondelete="RESTRICT"),
        unique=True,
    )
    name: Mapped[str] = mapped_column(String(PACKAGE_NAME_MAX))
    unit: Mapped[str] = mapped_column(String(PACKAGE_UNIT_MAX))
    quantity: Mapped[int] = mapped_column(Integer)
    validity_days: Mapped[int] = mapped_column(SmallInteger)
    subject_id: Mapped[uuid.UUID | None] = _uuid_fk("subjects", nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Package(Audited, Model):
    """A purchased package (#94 A3; data-model §3.19): one billing account
    and one student, never shared (spec §6). Append-only. Its
    ``package_purchase`` charge is posted in the same transaction."""

    __tablename__ = "packages"
    __table_args__ = (
        CheckConstraint(_in("unit", PACKAGE_UNITS), name="unit_valid"),
        CheckConstraint("units_granted > 0", name="units_granted_positive"),
        CheckConstraint("expires_on > purchased_on", name="dates"),
        UniqueConstraint(
            "id", "billing_account_id", "student_id", name=PACKAGE_COMPOSITE_KEY
        ),
        Index(
            "ix_packages_account_student_expires_on",
            "billing_account_id",
            "student_id",
            "expires_on",
        ),
        Index("ix_packages_price_version_id", "price_version_id"),
    )

    billing_account_id: Mapped[uuid.UUID] = _uuid_fk("billing_accounts")
    student_id: Mapped[uuid.UUID] = _uuid_fk("students")
    package_product_id: Mapped[uuid.UUID] = _uuid_fk("package_products")
    price_version_id: Mapped[uuid.UUID] = _uuid_fk("price_versions")
    unit: Mapped[str] = mapped_column(String(PACKAGE_UNIT_MAX))
    units_granted: Mapped[int] = mapped_column(Integer)
    subject_id: Mapped[uuid.UUID | None] = _uuid_fk("subjects", nullable=True)
    purchased_on: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date] = mapped_column(Date)


class PackageCreditTransaction(Audited, Model):
    """One signed unit movement of a package (#94 A4; data-model §3.20,
    without ``withdrawal_id``, added by #101). Append-only."""

    __tablename__ = "package_credit_transactions"
    __table_args__ = (
        CheckConstraint(_in("kind", PACKAGE_TRANSACTION_KINDS), name="kind"),
        CheckConstraint(
            "(kind = 'grant' AND units > 0)"
            " OR (kind IN ('consume', 'expire') AND units < 0)"
            " OR (kind = 'reversal' AND units <> 0)",
            name="units_sign",
        ),
        CheckConstraint(
            "(class_session_id IS NOT NULL) = (kind = 'consume')",
            name="consume_session",
        ),
        CheckConstraint(
            "(student_id IS NOT NULL) = (kind = 'consume')", name="consume_student"
        ),
        CheckConstraint(
            "(reverses_transaction_id IS NOT NULL) = (kind = 'reversal')",
            name="reversal_target",
        ),
        Index(
            PACKAGE_GRANT_KEY,
            "package_id",
            unique=True,
            postgresql_where=text("kind = 'grant'"),
        ),
        Index(
            PACKAGE_EXPIRE_KEY,
            "package_id",
            unique=True,
            postgresql_where=text("kind = 'expire'"),
        ),
        Index(
            PACKAGE_CONSUME_KEY,
            "package_id",
            "class_session_id",
            unique=True,
            postgresql_where=text("kind = 'consume'"),
        ),
        Index(
            PACKAGE_REVERSAL_KEY,
            "reverses_transaction_id",
            unique=True,
            postgresql_where=text("kind = 'reversal'"),
        ),
        Index("ix_package_credit_transactions_package_id", "package_id"),
        Index(
            "ix_package_credit_transactions_session_student",
            "class_session_id",
            "student_id",
            postgresql_where=text("kind = 'consume'"),
        ),
    )

    package_id: Mapped[uuid.UUID] = _uuid_fk("packages")
    kind: Mapped[str] = mapped_column(String(16))
    units: Mapped[int] = mapped_column(Integer)
    class_session_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "class_sessions", nullable=True
    )
    student_id: Mapped[uuid.UUID | None] = _uuid_fk("students", nullable=True)
    reverses_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "package_credit_transactions.id",
            ondelete="RESTRICT",
            name=PACKAGE_REVERSAL_FK,
        ),
        nullable=True,
    )


# --- Due dates and reminders (#97) ---------------------------------------------------

DUE_DATE_APPEND_ONLY_TABLES: Final = ("charge_due_date_changes", "charge_reminders")
"""The tables migration 0067 makes append-only (data-model §5.1)."""
DUE_DATE_MIN: Final = date(2000, 1, 1)
DUE_DATE_MAX: Final = date(2099, 12, 31)
REMINDER_STAGE_MAX: Final = 24
REMINDER_KEY: Final = "uq_charge_reminders_charge_stage"


class ChargeDueDateChange(Audited, Model):
    """A change of a posted charge's due date (#97 A1; data-model §3.11).
    Append-only. ``previous_due_date`` is the effective due date before the
    change; the latest row by ``(created_at, id)`` is the effective one."""

    __tablename__ = "charge_due_date_changes"
    __table_args__ = (
        CheckConstraint(
            f"due_date BETWEEN '{DUE_DATE_MIN.isoformat()}'"
            f" AND '{DUE_DATE_MAX.isoformat()}'",
            name="due_date_range",
        ),
        CheckConstraint("due_date <> previous_due_date", name="due_date_changed"),
        CheckConstraint(staff_text_check("reason", REASON_MAX), name="reason_valid"),
        ForeignKeyConstraint(
            ["charge_id", "billing_account_id"],
            ["charges.id", "charges.billing_account_id"],
            name="fk_charge_due_date_changes_charge_id_charges",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_charge_due_date_changes_charge_created", "charge_id", "created_at", "id"
        ),
    )

    charge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    billing_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    previous_due_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(REASON_MAX))
    changed_by_user_id: Mapped[uuid.UUID] = _actor(nullable=False)


class ChargeReminder(Model):
    """One reminder stage sent for one charge (#97 A2; data-model §3.27).
    Append-only and **not** audited (N4 (a)). ``due_date`` is the effective
    due date the stage was computed from."""

    __tablename__ = "charge_reminders"
    __table_args__ = (
        CheckConstraint(_in("stage", REMINDER_STAGES), name="stage"),
        UniqueConstraint("charge_id", "stage", name=REMINDER_KEY),
    )

    charge_id: Mapped[uuid.UUID] = _uuid_fk("charges")
    stage: Mapped[str] = mapped_column(String(REMINDER_STAGE_MAX))
    due_date: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


RECEIPT_LANGUAGES: Final = ("es", "en")
"""The receipt languages (ADR-0015; ``app.core.i18n.LANGUAGES``)."""
RECEIPT_NUMBER_MAX: Final = 16
RECEIPT_NUMBER_PATTERN: Final = "^R-[0-9]{4}-[0-9]{6}$"
"""``R-YYYY-NNNNNN`` (#98 A2, B2)."""


class Receipt(Audited, Model):
    """The receipt of one payment, issued in the payment's transaction (#98
    A2; data-model §3.21; ADR-0006 E13). Append-only (migration 0068's
    ``receipts_append_only()`` triggers, SQLSTATE 42501). ``snapshot`` is a
    ``billing.receipts.ReceiptSnapshotV1`` (``"v": 1``); the PDF is built
    from the row alone, plus the payment's reversal (if any) for the banner.
    ``language`` and ``template_version`` never change."""

    __tablename__ = "receipts"
    __table_args__ = (
        CheckConstraint(f"number ~ '{RECEIPT_NUMBER_PATTERN}'", name="number_format"),
        CheckConstraint(_in("language", RECEIPT_LANGUAGES), name="language"),
        CheckConstraint("template_version > 0", name="template_version_positive"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="snapshot_object"),
        UniqueConstraint("number", name="uq_receipts_number"),
        UniqueConstraint("payment_id", name="uq_receipts_payment_id"),
        _payment_fk("receipts"),
        Index(
            "ix_receipts_billing_account_id_issued_at",
            "billing_account_id",
            "issued_at",
        ),
    )

    number: Mapped[str] = mapped_column(String(RECEIPT_NUMBER_MAX))
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    billing_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str] = mapped_column(String(2))
    template_version: Mapped[int] = mapped_column(SmallInteger)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)


# --- Schedule reconciliation (#99) ---------------------------------------------------

CANCELLATION_OUTCOMES: Final = ("credit", "charge")
"""What a cancellation does to a session charge (ADR-0006 E3)."""
CANCELLATION_CLASSIFICATIONS: Final = ("on_time", "late", "no_show", "excused")
"""The #61 classifications, one rule column each."""
CANCELLATION_INITIATORS: Final = ("center", "family", "tutor")
CREDIT_RESULTS: Final = ("credited", "no_charge", "zero_charge", "already_zero")
"""The results of a ``credit`` outcome (#99 D4)."""
CHARGE_RESULTS: Final = ("charge_stands", "charged", "package_consumed")
"""The results of a ``charge`` outcome (#99 D4)."""
CANCELLATION_RESULTS: Final = CREDIT_RESULTS + CHARGE_RESULTS
REPRICING_METHODS: Final = ("original", "catalog", "pro_rata")
"""How a repricing target was found (#99 D6, Q2)."""
OUTCOME_MAX: Final = 8
RESULT_MAX: Final = 16
RECONCILIATION_APPEND_ONLY_TABLES: Final = (
    "session_cancellation_decisions",
    "charge_duration_repricings",
)
"""The tables migration 0069 makes append-only (data-model §5.1)."""
CANCELLATION_DECISION_KEY: Final = "uq_session_cancellation_decisions_key"
REPRICING_KEY: Final = "uq_charge_duration_repricings_charge_version"
RULE_OUTCOME_FIELDS: Final = (
    "excused_outcome",
    "on_time_outcome",
    "late_outcome",
    "no_show_outcome",
)


def _outcome_column() -> Mapped[str]:
    return mapped_column(String(OUTCOME_MAX))


def _named_fk(target: str, name: str, *, nullable: bool = False) -> Mapped[Any]:
    """A ``RESTRICT`` foreign key with an explicit name (the convention's
    would pass PostgreSQL's 63 characters)."""
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{target}.id", ondelete="RESTRICT", name=name),
        nullable=nullable,
    )


class CancellationBillingRule(Audited, Model):
    """One version of the cancellation billing rules (#99 A1; data-model
    §3.22; ADR-0006 E3): the outcome per classification, in force from its
    unique ``effective_from`` until the next one's. **Guarded** (migration
    0069's ``cancellation_billing_rules_guard()``): ``UPDATE`` is always
    refused, ``DELETE`` only for an upcoming version, ``TRUNCATE`` always
    (SQLSTATE 42501). Version 1 (2000-01-01: credit, credit, charge, charge)
    is seeded by the migration."""

    __tablename__ = "cancellation_billing_rules"
    __table_args__ = tuple(
        CheckConstraint(_in(field, CANCELLATION_OUTCOMES), name=field)
        for field in RULE_OUTCOME_FIELDS
    )

    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), unique=True
    )
    excused_outcome: Mapped[str] = _outcome_column()
    on_time_outcome: Mapped[str] = _outcome_column()
    late_outcome: Mapped[str] = _outcome_column()
    no_show_outcome: Mapped[str] = _outcome_column()
    created_by_user_id: Mapped[uuid.UUID | None] = _actor(nullable=True)


class SessionCancellationDecision(Audited, Model):
    """The billing outcome of one cancelled session for one participant
    (#99 A2, Q5): the classification and rule it was decided with, and what
    was posted (``charge_id``/``charge_adjustment_id``). Append-only; at most
    one per ``(class_session_id, student_id)``."""

    __tablename__ = "session_cancellation_decisions"
    __table_args__ = (
        UniqueConstraint(
            "class_session_id", "student_id", name=CANCELLATION_DECISION_KEY
        ),
        CheckConstraint(
            _in("initiated_by", CANCELLATION_INITIATORS), name="initiated_by"
        ),
        CheckConstraint(
            _in("classification", CANCELLATION_CLASSIFICATIONS), name="classification"
        ),
        CheckConstraint(_in("outcome", CANCELLATION_OUTCOMES), name="outcome"),
        CheckConstraint(_in("result", CANCELLATION_RESULTS), name="result"),
        CheckConstraint(
            "(result = 'credited') = (charge_adjustment_id IS NOT NULL)",
            name="credited_iff_adjustment",
        ),
        CheckConstraint(
            f"outcome <> 'credit' OR {_in('result', CREDIT_RESULTS)}",
            name="credit_results",
        ),
        CheckConstraint(
            f"outcome <> 'charge' OR {_in('result', CHARGE_RESULTS)}",
            name="charge_results",
        ),
    )

    class_session_id: Mapped[uuid.UUID] = _named_fk(
        "class_sessions", "fk_session_cancellation_decisions_class_session_id"
    )
    student_id: Mapped[uuid.UUID] = _uuid_fk("students")
    initiated_by: Mapped[str] = mapped_column(String(OUTCOME_MAX))
    change_request_id: Mapped[uuid.UUID | None] = _named_fk(
        "session_change_requests",
        "fk_session_cancellation_decisions_change_request_id",
        nullable=True,
    )
    classification: Mapped[str] = mapped_column(String(OUTCOME_MAX))
    makeup_allowed: Mapped[bool] = mapped_column(Boolean)
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rule_id: Mapped[uuid.UUID] = _named_fk(
        "cancellation_billing_rules", "fk_session_cancellation_decisions_rule_id"
    )
    outcome: Mapped[str] = _outcome_column()
    result: Mapped[str] = mapped_column(String(RESULT_MAX))
    charge_id: Mapped[uuid.UUID | None] = _uuid_fk("charges", nullable=True)
    charge_adjustment_id: Mapped[uuid.UUID | None] = _named_fk(
        "charge_adjustments",
        "fk_session_cancellation_decisions_charge_adjustment_id",
        nullable=True,
    )
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChargeDurationRepricing(Audited, Model):
    """One repricing of a session charge to its session's new duration (#99
    A3, Q2): the minutes before and after, the target amount, the delta
    (``0.00`` allowed: then no adjustment) and how the target was found.
    Append-only; one per ``(charge_id, session_version)``. The latest row
    (``created_at``, ``id``) holds the charge's billed minutes."""

    __tablename__ = "charge_duration_repricings"
    __table_args__ = (
        UniqueConstraint("charge_id", "session_version", name=REPRICING_KEY),
        CheckConstraint(_in("method", REPRICING_METHODS), name="method"),
        CheckConstraint(
            f"scheduled_minutes BETWEEN {SCHEDULED_MINUTES_MIN}"
            f" AND {SCHEDULED_MINUTES_MAX}"
            f" AND previous_minutes BETWEEN {SCHEDULED_MINUTES_MIN}"
            f" AND {SCHEDULED_MINUTES_MAX}",
            name="minutes_range",
        ),
        CheckConstraint(
            "scheduled_minutes <> previous_minutes", name="minutes_changed"
        ),
        CheckConstraint("target_amount >= 0", name="target_non_negative"),
        CheckConstraint(
            "(delta_amount <> 0) = (charge_adjustment_id IS NOT NULL)",
            name="delta_iff_adjustment",
        ),
        Index(
            "ix_charge_duration_repricings_charge_latest",
            "charge_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )

    charge_id: Mapped[uuid.UUID] = _uuid_fk("charges")
    class_session_id: Mapped[uuid.UUID] = _uuid_fk("class_sessions")
    session_version: Mapped[int] = mapped_column(Integer)
    previous_minutes: Mapped[int] = mapped_column(SmallInteger)
    scheduled_minutes: Mapped[int] = mapped_column(SmallInteger)
    target_amount: Mapped[Money] = mapped_column(MoneyType())
    delta_amount: Mapped[Money] = mapped_column(MoneyType())
    method: Mapped[str] = mapped_column(String(OUTCOME_MAX))
    clamped: Mapped[bool] = mapped_column(Boolean)
    charge_adjustment_id: Mapped[uuid.UUID | None] = _named_fk(
        "charge_adjustments",
        "fk_charge_duration_repricings_charge_adjustment_id",
        nullable=True,
    )
