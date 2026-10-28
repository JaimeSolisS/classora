"""Billing tables: the price catalog (#89) and the ledger core (#90;
``_docs/data-model.md`` §3; ADR-0006).

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
``price_version_id``, and #94 adds packages.

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

Every model is ``Audited``; every foreign key is ``ON DELETE RESTRICT``.
"""

import uuid
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.audit import Audited
from app.core.models import Base, Model, VersionedMixin
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


# --- Ledger core (#90) ---------------------------------------------------------------

DUE_OFFSET_MAX: Final = 60
"""``billing_accounts.due_offset_days`` runs from 0 to this (E11)."""

CHARGE_KINDS: Final = ("session", "group_enrollment", "cohort_enrollment", "manual")
"""The charge kinds whose source exists (#90 E3): #93 adds ``tuition_period``
and #94 ``package_purchase``, each replacing ``ck_charges_kind`` and
``ck_charges_one_source``."""

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

ACCOUNT_FAMILY_UNIQUE: Final = "uq_billing_accounts_family_id"
ACCOUNT_FAMILY_FK: Final = "fk_billing_accounts_family_id_families"
CHARGE_SESSION_KEY: Final = "uq_charges_session_student"
CHARGE_GROUP_ENROLLMENT_KEY: Final = "uq_charges_group_enrollment"
CHARGE_COHORT_ENROLLMENT_KEY: Final = "uq_charges_cohort_enrollment"
CHARGE_NATURAL_KEYS: Final = (
    CHARGE_SESSION_KEY,
    CHARGE_GROUP_ENROLLMENT_KEY,
    CHARGE_COHORT_ENROLLMENT_KEY,
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
    "num_nonnulls(class_session_id, group_enrollment_id, cohort_enrollment_id)"
    " = CASE WHEN kind = 'manual' THEN 0 ELSE 1 END"
    " AND (kind = 'session') = (class_session_id IS NOT NULL)"
    " AND (kind = 'session') = (scheduled_minutes IS NOT NULL)"
    " AND (kind = 'group_enrollment') = (group_enrollment_id IS NOT NULL)"
    " AND (kind = 'cohort_enrollment') = (cohort_enrollment_id IS NOT NULL)"
    " AND (kind = 'manual') = (description IS NOT NULL)"
)
"""``ck_charges_one_source`` (#90 A2, E3): exactly the source of the kind,
the minutes of a session charge only, the description of a manual charge
only."""


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
    service_date: Mapped[date] = mapped_column(Date)
    service_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    price_version_id: Mapped[uuid.UUID | None] = _uuid_fk(
        "price_versions", nullable=True
    )
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
