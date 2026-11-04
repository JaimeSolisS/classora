"""Finance tables (#114; ADR-0017 E12, E15; data-model §12.1, §12.2).

``expense_categories`` and ``vendors`` are the expense configuration. Both are
``Audited`` with no mask: neither holds an amount (#114 E6). A name is unique
ignoring case (the ``lower(name)`` index), so an insert or a rename goes
through a savepoint that maps the index to a ``409`` (the ``Audited`` rule: no
``ON CONFLICT``). Neither table has a delete: ``is_active`` retires a row, and
a trigger refuses ``TRUNCATE`` (migration ``0083``).

``expenses``, ``expense_payments``, ``expense_payment_reversals`` and
``expense_adjustments`` (#115; data-model §12.3-§12.6) are append-only: a
trigger pair refuses ``UPDATE``, ``DELETE`` and ``TRUNCATE`` (migration
``0084``). An expense is corrected only by an adjustment (a void is a full
negative one), a payment only by its reversal (ADR-0017 E16). Their amounts and
staff-only texts are masked in the audit log (#115 E7): Administrators hold
``audit.view`` and see no expense amounts (ADR-0017 E12).

A category's ``system_key`` marks one of the five rows the migration seeds.
While a row holds it, clients show the catalog label of the key in the viewer's
language; a rename clears it, and nothing ever sets it again (#114 E3).
"""

import uuid
from datetime import date
from typing import ClassVar, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin
from app.core.money import Money, MoneyType

CATEGORY_NAME_MAX: Final = 100
VENDOR_NAME_MAX: Final = 200
VENDOR_CONTACT_MAX: Final = 500
SYSTEM_KEY_MAX: Final = 20

SYSTEM_KEYS: Final = ("rent", "utilities", "supplies", "equipment", "maintenance")
"""The seeded categories' keys, in seed order (#114 E3)."""

CATEGORY_NAME_KEY: Final = "uq_expense_categories_lower_name"
VENDOR_NAME_KEY: Final = "uq_vendors_lower_name"

DESCRIPTION_MAX: Final = 500
REFERENCE_MAX: Final = 100
NOTE_MAX: Final = 1000
REASON_MAX: Final = 1000
METHOD_CODE_MAX: Final = 32
"""``payment_methods.code`` is ``VARCHAR(32)`` (billing)."""

EXPENSE_PAYMENT_REVERSAL_KEY: Final = "uq_expense_payment_reversals_expense_payment_id"
EXPENSE_PAYMENT_REVERSAL_FK: Final = "fk_expense_payment_reversals_payment"
"""Named: the convention's name is beyond PostgreSQL's 63 characters."""


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


def _restrict(target: str, name: str | None = None) -> ForeignKey:
    return ForeignKey(target, ondelete="RESTRICT", name=name)


class Expense(Audited, Model):
    """A one-time operating expense (data-model §12.3; ADR-0017 E16; #115).
    Append-only (migration ``0084``): the amount is corrected by an
    :class:`ExpenseAdjustment`, anything else by a void and a new expense
    (#115 E3). ``net``, ``paid``, ``open`` and the status are derived, never
    stored (data-model §13.2).

    ``created_by_user_id`` is ``NULL`` only for the recurring job (#116).
    ``amount``, ``description``, ``receipt_reference`` and ``note`` are masked
    in the audit log (#115 E7)."""

    __tablename__ = "expenses"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset(
        {"amount", "description", "receipt_reference", "note"}
    )
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            _trimmed("description", DESCRIPTION_MAX), name="description_trimmed"
        ),
        CheckConstraint(
            _trimmed("receipt_reference", REFERENCE_MAX),
            name="receipt_reference_trimmed",
        ),
        CheckConstraint(_trimmed("note", NOTE_MAX), name="note_trimmed"),
        CheckConstraint("due_on >= incurred_on", name="due_on_after_incurred_on"),
        Index("ix_expenses_due_on", "due_on"),
        Index("ix_expenses_incurred_on", "incurred_on"),
        Index("ix_expenses_category_id_incurred_on", "category_id", "incurred_on"),
        Index("ix_expenses_vendor_id", "vendor_id"),
    )

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _restrict("expense_categories.id")
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _restrict("vendors.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[Money] = mapped_column(MoneyType())
    incurred_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date)
    receipt_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), _restrict("users.id"), nullable=True
    )


class ExpensePayment(Audited, Model):
    """A payment of an expense, partial ones included (data-model §12.4;
    #115). Append-only: a mistake is reversed (:class:`ExpensePaymentReversal`),
    never edited. ``amount``, ``reference`` and ``note`` are masked in the audit
    log (#115 E7)."""

    __tablename__ = "expense_payments"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset(
        {"amount", "reference", "note"}
    )
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(_trimmed("reference", REFERENCE_MAX), name="reference_trimmed"),
        CheckConstraint(_trimmed("note", NOTE_MAX), name="note_trimmed"),
        Index("ix_expense_payments_expense_id", "expense_id"),
        Index("ix_expense_payments_paid_on", "paid_on"),
    )

    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _restrict("expenses.id")
    )
    paid_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Money] = mapped_column(MoneyType())
    method_code: Mapped[str] = mapped_column(
        String(METHOD_CODE_MAX), _restrict("payment_methods.code")
    )
    reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _restrict("users.id")
    )


class ExpensePaymentReversal(Audited, Model):
    """The reversal of a mistaken expense payment (data-model §12.5; ADR-0017
    E12; #115). At most one per payment (the UNIQUE). Append-only. ``reason``
    is masked in the audit log (#115 E7)."""

    __tablename__ = "expense_payment_reversals"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"reason"})
    __table_args__ = (
        CheckConstraint(_trimmed("reason", REASON_MAX), name="reason_trimmed"),
        UniqueConstraint("expense_payment_id", name=EXPENSE_PAYMENT_REVERSAL_KEY),
    )

    expense_payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        _restrict("expense_payments.id", EXPENSE_PAYMENT_REVERSAL_FK),
    )
    reason: Mapped[str] = mapped_column(Text)
    reversed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _restrict("users.id")
    )


class ExpenseAdjustment(Audited, Model):
    """A signed correction of an expense's amount, the only one (data-model
    §12.6; ADR-0017 E16; #115). An adjustment of exactly ``-net`` voids the
    expense, for good (#115 E4). Append-only. ``amount`` and ``reason`` are
    masked in the audit log (#115 E7)."""

    __tablename__ = "expense_adjustments"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"amount", "reason"})
    __table_args__ = (
        CheckConstraint("amount <> 0", name="amount_not_zero"),
        CheckConstraint(_trimmed("reason", REASON_MAX), name="reason_trimmed"),
        Index("ix_expense_adjustments_expense_id", "expense_id"),
    )

    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _restrict("expenses.id")
    )
    amount: Mapped[Money] = mapped_column(MoneyType())
    reason: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), _restrict("users.id")
    )
