"""Payroll tables (#105, #106, #441, #107, #109; ADR-0017 E1, E2, E4, E6, E8, E9;
data-model §11.1-§11.8, §14.2, §14.3).

``tutor_pay_terms`` holds what applies to a tutor as a whole and
``compensation_rates`` the rate per class kind. Each is a list of versions:
one runs until the next one's ``effective_from``, so the unique key is the
"no overlap" constraint (invariant P1). A row whose values are ``NULL`` means
"not on payroll" or "no rate" from that date.

Both tables are guarded: a row a finalized payroll line uses can't change or
go away, and neither table can be truncated (migration ``0072``). The amounts
are masked in the audit log (#105 E2), because Administrators hold
``audit.view`` and see no pay (ADR-0017 E12).

``payroll_settings`` is the one settings row (the ``billing_settings``
pattern). ``payroll_periods`` is a tutor's chain of periods: the database
refuses an overlap (the ``EXCLUDE``, invariant P2) and a gap (the composite
foreign key onto ``(tutor_user_id, ends_on)``, invariant P3), and a trigger
keeps a finalized row (migration ``0073``). It is not ``Audited``: the job
opens the rows and the status moves carry named events (#110). A returned
period keeps who returned it and why while it is ``open`` (migration ``0079``).

``session_pay_rules`` is the center-wide list of late-cancellation, no-show and
makeup rule versions, guarded by time (migration ``0075``): nothing changes,
and only an upcoming version can go away. ``percent`` and ``amount`` are masked
in the audit log, as above.

``pay_item_types`` and ``pay_items`` are the bonuses and deductions (#109). A
type's ``direction`` never changes (migration ``0078``). An item is guarded the
way a custom price is: once a finalized line uses it, only ``effective_to``
moves. Its ``amount`` and ``description`` are masked in the audit log (#109 E7):
free text can state an amount or a personal reason.

``payroll_lines`` holds one line per source: the four session kinds, one
``salary`` line per piece (#108), one ``pay_item`` line per item and period
(#109) and one ``adjustment`` line per adjustment (#111). Lines are working data
until the period is finalized, so the table isn't ``Audited``, and a trigger
refuses every write to a line of a finalized period (migration ``0076``). Only
``recalculate``, the deletion of a rate, rule, terms version or item, and the
adjustment job write them.

``payroll_adjustments`` is the append-only, audited record of corrections after
lock (#111, migration ``0080``): :class:`PayrollAdjustment`.

``payroll_payments`` and ``payroll_payment_reversals`` log money paid to a tutor
outside the platform against a finalized period, and its reversal (#112,
migration ``0081``): :class:`PayrollPayment`, :class:`PayrollPaymentReversal`.
Both are append-only and audited, with ``amount``, ``reference``, ``note`` and
``reason`` masked.

``payslips`` is the frozen, numbered payslip of a finalized period that has
lines, issued by ``finalize`` (#113, migration ``0082``): :class:`Payslip`.
Append-only and audited, with ``snapshot`` masked.
"""

import uuid
from datetime import date, datetime
from typing import Any, ClassVar, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import Audited
from app.core.models import Model, VersionedMixin
from app.core.money import Money, MoneyType, Rate, RateType

PAY_FREQUENCIES: Final = ("weekly", "biweekly", "monthly")
CLASS_KINDS: Final = ("individual", "group", "course")
RATE_MODELS: Final = ("per_hour", "per_session", "per_course")
PER_COURSE: Final = "per_course"
COURSE: Final = "course"

PRORATION_BASES: Final = ("calendar_days", "weekdays")
PERIOD_STATUSES: Final = ("open", "prepared", "finalized")
PAY_RULE_EVENTS: Final = ("late_cancellation", "no_show", "makeup")
PAY_RULE_MODES: Final = ("none", "percent", "fixed")
LINE_EVENT_KINDS: Final = PAY_RULE_EVENTS
SESSION_LINE_KINDS: Final = ("session", *LINE_EVENT_KINDS)
SALARY: Final = "salary"
PAY_ITEM: Final = "pay_item"
ADJUSTMENT: Final = "adjustment"
LINE_KINDS: Final = (*SESSION_LINE_KINDS, SALARY, PAY_ITEM, ADJUSTMENT)
LATE_APPROVAL: Final = "late_approval"
HOURS_CORRECTION: Final = "hours_correction"
MANUAL: Final = "manual"
CARRY_FORWARD: Final = "carry_forward"
ADJUSTMENT_KINDS: Final = (LATE_APPROVAL, HOURS_CORRECTION, MANUAL, CARRY_FORWARD)
AUTOMATIC_ADJUSTMENT_KINDS: Final = (LATE_APPROVAL, HOURS_CORRECTION)
"""The kinds ``payroll:post_adjustments`` writes, and what "paid so far" adds up
(#111 E2)."""
PAY_ITEM_DIRECTIONS: Final = ("bonus", "deduction")
PAY_ITEM_RECURRENCES: Final = ("one_time", "recurring")

PAY_FREQUENCY_MAX: Final = 10
PERIOD_STATUS_MAX: Final = 10
PRORATION_BASIS_MAX: Final = 20
CLASS_KIND_MAX: Final = 20
RATE_MODEL_MAX: Final = 20
PAY_RULE_EVENT_MAX: Final = 20
PAY_RULE_MODE_MAX: Final = 10
LINE_KIND_MAX: Final = 20
PAY_ITEM_TYPE_NAME_MAX: Final = 100
PAY_ITEM_DIRECTION_MAX: Final = 10
PAY_ITEM_RECURRENCE_MAX: Final = 10
PAY_ITEM_DESCRIPTION_MAX: Final = 500
RETURN_REASON_MAX: Final = 1000
ADJUSTMENT_KIND_MAX: Final = 20
ADJUSTMENT_REASON_MAX: Final = 1000
PAYMENT_REFERENCE_MAX: Final = 100
PAYMENT_NOTE_MAX: Final = 1000
PAYMENT_REVERSAL_REASON_MAX: Final = 1000
PAYMENT_METHOD_CODE_MAX: Final = 32
"""``payment_methods.code`` (billing's ``String(32)``), repeated here because a
module imports no other module's models (ADR-0002)."""

TERMS_KEY: Final = "uq_tutor_pay_terms_tutor_user_id_effective_from"
RATE_KEY: Final = "uq_compensation_rates_tutor_user_id_class_kind_effective_from"
PAY_RULE_KEY: Final = "uq_session_pay_rules_event_effective_from"
PERIOD_START_KEY: Final = "uq_payroll_periods_tutor_user_id_starts_on"
PERIOD_END_KEY: Final = "uq_payroll_periods_tutor_user_id_ends_on"
PERIOD_TARGET_KEY: Final = "uq_payroll_periods_id_tutor_user_id"
PERIOD_OVERLAP: Final = "ex_payroll_periods_tutor_overlap"
PERIOD_RANGE_SQL: Final = "daterange(starts_on, ends_on, '[]')"
"""The inclusive span of a period."""
LINE_SESSION_UNIQUE: Final = "uq_payroll_lines_session_sourced"
LINE_TUTOR_KEY: Final = "uq_payroll_lines_id_tutor"
LINE_SALARY_PIECE: Final = "uq_payroll_lines_salary_piece"
LINE_PAY_ITEM_PERIOD: Final = "uq_payroll_lines_pay_item_period"
PAY_ITEM_TYPE_NAME_KEY: Final = "uq_pay_item_types_lower_name"
PAY_ITEM_TARGET_KEY: Final = "uq_pay_items_id_tutor_user_id"
LINE_ADJUSTMENT_UNIQUE: Final = "uq_payroll_lines_adjustment"
ADJUSTMENT_SOURCE_KEY: Final = "uq_payroll_adjustments_source_key"
ADJUSTMENT_FIRST_PAYMENT: Final = "uq_payroll_adjustments_first_payment"
ADJUSTMENT_TARGET_KEY: Final = "uq_payroll_adjustments_id_target"
PAYMENT_REVERSAL_KEY: Final = "uq_payroll_payment_reversals_payroll_payment_id"
PAYMENT_REVERSAL_FK: Final = "fk_payroll_payment_reversals_payment"
"""Named: the convention's name is beyond PostgreSQL's 63 characters."""
PAYSLIP_NUMBER_PATTERN: Final = "^P-[0-9]{4}-[0-9]{6}$"
PAYSLIP_NUMBER_MAX: Final = 16
PAYSLIP_LANGUAGES: Final = ("es", "en")
PAYSLIP_NUMBER_KEY: Final = "uq_payslips_number"
PAYSLIP_PERIOD_KEY: Final = "uq_payslips_payroll_period_id"
ADJUSTMENT_SNAPSHOT_COLUMNS: Final = (
    "line_kind",
    "service_date",
    "class_kind",
    "minutes",
    "rate_model",
    "rate",
    "compensation_rate_id",
    "session_pay_rule_id",
    "cumulative_sessions",
    "cohort_sessions",
)
"""The pricing snapshot of an adjustment (#111 A1): what it was priced on."""


def _one_of(column: str, values: tuple[str, ...]) -> str:
    listed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({listed})"


class TutorPayTerms(Audited, Model):
    """One version of a tutor's pay frequency and salary (data-model §11.2).

    ``salary_amount`` is per full pay period of this row's frequency.
    ``pay_frequency`` ``NULL`` means "not on payroll from this date".
    """

    __tablename__ = "tutor_pay_terms"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"salary_amount"})
    __table_args__ = (
        CheckConstraint(
            _one_of("pay_frequency", PAY_FREQUENCIES), name="pay_frequency_valid"
        ),
        CheckConstraint("salary_amount > 0", name="salary_positive"),
        CheckConstraint(
            "pay_frequency IS NOT NULL OR salary_amount IS NULL",
            name="salary_needs_frequency",
        ),
        UniqueConstraint("tutor_user_id", "effective_from", name=TERMS_KEY),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    effective_from: Mapped[date] = mapped_column(Date)
    pay_frequency: Mapped[str | None] = mapped_column(
        String(PAY_FREQUENCY_MAX), nullable=True
    )
    salary_amount: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)


class CompensationRate(Audited, Model):
    """One version of a tutor's rate for a class kind (data-model §11.3).

    ``rate_model`` and ``rate`` are both ``NULL`` to mean "no rate from this
    date".
    """

    __tablename__ = "compensation_rates"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"rate"})
    __table_args__ = (
        CheckConstraint(_one_of("class_kind", CLASS_KINDS), name="class_kind_valid"),
        CheckConstraint(_one_of("rate_model", RATE_MODELS), name="rate_model_valid"),
        CheckConstraint("rate >= 0", name="rate_non_negative"),
        CheckConstraint(
            "(rate_model IS NULL) = (rate IS NULL)", name="rate_model_with_rate"
        ),
        CheckConstraint(
            "rate_model <> 'per_course' OR class_kind = 'course'",
            name="per_course_only_course",
        ),
        UniqueConstraint(
            "tutor_user_id", "class_kind", "effective_from", name=RATE_KEY
        ),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    class_kind: Mapped[str] = mapped_column(String(CLASS_KIND_MAX))
    effective_from: Mapped[date] = mapped_column(Date)
    rate_model: Mapped[str | None] = mapped_column(
        String(RATE_MODEL_MAX), nullable=True
    )
    rate: Mapped[Rate | None] = mapped_column(RateType(), nullable=True)


class SessionPayRule(Audited, Model):
    """One version of the rule of a pay event (data-model §11.5).

    ``percent`` is a :class:`~app.core.money.Rate`, because
    ``Rate.to_money(percent=)`` takes one (#107 E2). ``none`` has neither
    value.
    """

    __tablename__ = "session_pay_rules"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"percent", "amount"})
    __table_args__ = (
        CheckConstraint(_one_of("event", PAY_RULE_EVENTS), name="event_valid"),
        CheckConstraint(_one_of("mode", PAY_RULE_MODES), name="mode_valid"),
        CheckConstraint(
            "(mode = 'percent') = (percent IS NOT NULL)", name="percent_with_mode"
        ),
        CheckConstraint(
            "(mode = 'fixed') = (amount IS NOT NULL)", name="amount_with_mode"
        ),
        CheckConstraint("percent BETWEEN 0 AND 1000", name="percent_range"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        UniqueConstraint("event", "effective_from", name=PAY_RULE_KEY),
    )

    event: Mapped[str] = mapped_column(String(PAY_RULE_EVENT_MAX))
    effective_from: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(PAY_RULE_MODE_MAX))
    percent: Mapped[Rate | None] = mapped_column(RateType(), nullable=True)
    amount: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class PayrollSettings(Audited, VersionedMixin, Model):
    """The one payroll settings row (data-model §11.1). Migration ``0073``
    inserts it with ``payroll_starts_on = NULL``: payroll is off and no period
    opens until staff set the date."""

    __tablename__ = "payroll_settings"
    __table_args__ = (
        CheckConstraint("singleton", name="singleton_true"),
        CheckConstraint(
            "EXTRACT(ISODOW FROM biweekly_anchor_on) = 1", name="anchor_monday"
        ),
        CheckConstraint(
            _one_of("salary_proration_basis", PRORATION_BASES),
            name="proration_basis_valid",
        ),
    )

    singleton: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), unique=True
    )
    payroll_starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    biweekly_anchor_on: Mapped[date] = mapped_column(
        Date, server_default=text("'2026-01-05'")
    )
    salary_proration_basis: Mapped[str] = mapped_column(
        String(PRORATION_BASIS_MAX), server_default=text("'calendar_days'")
    )


class PayrollPeriod(Model):
    """One period of one tutor (data-model §11.4). ``previous_ends_on`` is
    generated, so a row can't name a previous period other than the day before
    its start: the foreign key on it is the "no gaps" rule, and only the first
    period of a tutor has none."""

    __tablename__ = "payroll_periods"
    __table_args__ = (
        CheckConstraint("ends_on >= starts_on", name="dates_ordered"),
        CheckConstraint(
            _one_of("pay_frequency", PAY_FREQUENCIES), name="pay_frequency_valid"
        ),
        CheckConstraint(_one_of("status", PERIOD_STATUSES), name="status_valid"),
        CheckConstraint(
            "(status IN ('prepared', 'finalized')"
            " AND prepared_at IS NOT NULL AND prepared_by_user_id IS NOT NULL)"
            " OR (status NOT IN ('prepared', 'finalized')"
            " AND prepared_at IS NULL AND prepared_by_user_id IS NULL)",
            name="prepared_fields",
        ),
        CheckConstraint(
            "(status = 'finalized' AND total_amount IS NOT NULL"
            " AND finalized_at IS NOT NULL AND finalized_by_user_id IS NOT NULL)"
            " OR (status <> 'finalized' AND total_amount IS NULL"
            " AND finalized_at IS NULL AND finalized_by_user_id IS NULL)",
            name="finalized_fields",
        ),
        CheckConstraint("total_amount >= 0", name="total_not_negative"),
        CheckConstraint(
            "(returned_at IS NULL AND returned_by_user_id IS NULL"
            " AND return_reason IS NULL)"
            " OR (status = 'open' AND returned_at IS NOT NULL"
            " AND returned_by_user_id IS NOT NULL AND return_reason IS NOT NULL)",
            name="returned_fields",
        ),
        ExcludeConstraint(
            (text("tutor_user_id"), "="),
            (text(PERIOD_RANGE_SQL), "&&"),
            name=PERIOD_OVERLAP,
            using="gist",
        ),
        UniqueConstraint("tutor_user_id", "starts_on", name=PERIOD_START_KEY),
        UniqueConstraint("tutor_user_id", "ends_on", name=PERIOD_END_KEY),
        UniqueConstraint("id", "tutor_user_id", name=PERIOD_TARGET_KEY),
        ForeignKeyConstraint(
            ["tutor_user_id", "previous_ends_on"],
            ["payroll_periods.tutor_user_id", "payroll_periods.ends_on"],
            name="fk_payroll_periods_previous_period",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_payroll_periods_first_period",
            "tutor_user_id",
            unique=True,
            postgresql_where=text("is_first_period"),
        ),
        Index("ix_payroll_periods_status_ends_on", "status", "ends_on"),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    pay_frequency: Mapped[str] = mapped_column(String(PAY_FREQUENCY_MAX))
    is_first_period: Mapped[bool] = mapped_column(Boolean)
    previous_ends_on: Mapped[date | None] = mapped_column(
        Date,
        Computed(
            "CASE WHEN is_first_period THEN NULL ELSE starts_on - 1 END",
            persisted=True,
        ),
    )
    status: Mapped[str] = mapped_column(
        String(PERIOD_STATUS_MAX), server_default=text("'open'")
    )
    prepared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    prepared_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    total_amount: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finalized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    returned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    returned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    return_reason: Mapped[str | None] = mapped_column(
        String(RETURN_REASON_MAX), nullable=True
    )


class PayItemType(Audited, VersionedMixin, Model):
    """A staff-defined kind of bonus or deduction (data-model §11.7). It holds
    no pay, so nothing is masked. There is no delete: ``is_active`` retires it."""

    __tablename__ = "pay_item_types"
    __table_args__ = (
        CheckConstraint(
            _one_of("direction", PAY_ITEM_DIRECTIONS), name="direction_valid"
        ),
        CheckConstraint(
            f"char_length(name) BETWEEN 1 AND {PAY_ITEM_TYPE_NAME_MAX}"
            " AND name = btrim(name)",
            name="name_trimmed",
        ),
        Index(PAY_ITEM_TYPE_NAME_KEY, func.lower(text("name")), unique=True),
    )

    name: Mapped[str] = mapped_column(String(PAY_ITEM_TYPE_NAME_MAX))
    direction: Mapped[str] = mapped_column(String(PAY_ITEM_DIRECTION_MAX))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )


class PayItem(Audited, VersionedMixin, Model):
    """A fixed bonus or deduction of one tutor (data-model §11.8). ``amount``
    is always positive: the type's direction gives a line its sign."""

    __tablename__ = "pay_items"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"amount", "description"})
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            _one_of("recurrence", PAY_ITEM_RECURRENCES), name="recurrence_valid"
        ),
        CheckConstraint(
            f"char_length(description) BETWEEN 1 AND {PAY_ITEM_DESCRIPTION_MAX}"
            " AND description = btrim(description)",
            name="description_trimmed",
        ),
        CheckConstraint("effective_to >= effective_from", name="dates_ordered"),
        CheckConstraint(
            "recurrence = 'recurring' OR effective_to IS NULL",
            name="end_only_recurring",
        ),
        UniqueConstraint("id", "tutor_user_id", name=PAY_ITEM_TARGET_KEY),
        Index(
            "ix_pay_items_tutor_user_id_effective_from",
            "tutor_user_id",
            "effective_from",
        ),
        Index("ix_pay_items_pay_item_type_id", "pay_item_type_id"),
    )

    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    pay_item_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pay_item_types.id", ondelete="RESTRICT")
    )
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[Money] = mapped_column(MoneyType())
    recurrence: Mapped[str] = mapped_column(String(PAY_ITEM_RECURRENCE_MAX))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class PayrollLine(Model):
    """One line of a period (data-model §11.6): a session-sourced line of the
    tutor, a piece of salary, a pay item or an adjustment. ``approval_id`` is the
    revision used, ``NULL`` on a line that comes from a cancellation. A line "covered by
    salary" has kind ``session``, ``0.00`` and no rate snapshot. A ``salary`` line
    has no session, and its seven columns say how the amount was reached (#108
    E1). A ``pay_item`` line has only ``pay_item_id``, and is the one signed kind:
    a deduction is negative (#109). An ``adjustment`` line shows one
    :class:`PayrollAdjustment` in its target period, and has nothing else: its
    references are on the adjustment (#111 A4). The composite foreign key puts it
    in that period, and of that tutor, by construction."""

    __tablename__ = "payroll_lines"
    __table_args__ = (
        CheckConstraint(_one_of("kind", LINE_KINDS), name="kind_valid"),
        CheckConstraint(
            "(kind IN ('session', 'makeup')"
            " AND class_session_id IS NOT NULL AND approval_id IS NOT NULL)"
            " OR (kind = 'no_show' AND class_session_id IS NOT NULL)"
            " OR (kind = 'late_cancellation'"
            " AND class_session_id IS NOT NULL AND approval_id IS NULL)"
            " OR (kind = 'salary' AND class_session_id IS NULL"
            " AND approval_id IS NULL)"
            " OR (kind = 'pay_item' AND class_session_id IS NULL"
            " AND approval_id IS NULL)"
            " OR (kind = 'adjustment' AND class_session_id IS NULL"
            " AND approval_id IS NULL AND rate_model IS NULL)",
            name="source_per_kind",
        ),
        CheckConstraint(
            "(class_session_id IS NULL AND service_date IS NULL"
            " AND class_kind IS NULL AND minutes IS NULL)"
            " OR (class_session_id IS NOT NULL AND service_date IS NOT NULL"
            " AND class_kind IS NOT NULL AND minutes IS NOT NULL)",
            name="session_fields",
        ),
        CheckConstraint(_one_of("class_kind", CLASS_KINDS), name="class_kind_valid"),
        CheckConstraint(_one_of("rate_model", RATE_MODELS), name="rate_model_valid"),
        CheckConstraint(
            "(kind IN ('pay_item', 'adjustment') AND amount <> 0)"
            " OR (kind NOT IN ('pay_item', 'adjustment') AND amount >= 0)",
            name="amount_sign",
        ),
        CheckConstraint("minutes >= 0", name="minutes_non_negative"),
        CheckConstraint(
            "(rate_model IS NULL) = (rate IS NULL)"
            " AND (rate IS NULL) = (compensation_rate_id IS NULL)",
            name="rate_snapshot",
        ),
        CheckConstraint(
            "(session_pay_rule_id IS NOT NULL)"
            f" = ({_one_of('kind', LINE_EVENT_KINDS)})",
            name="pay_rule_with_event",
        ),
        CheckConstraint(
            "(cumulative_sessions IS NOT NULL) = (cohort_sessions IS NOT NULL)"
            " AND (cumulative_sessions IS NOT NULL)"
            " = COALESCE(rate_model = 'per_course', false)",
            name="course_share",
        ),
        CheckConstraint(
            "num_nonnulls(tutor_pay_terms_id, salary_amount, salary_starts_on,"
            " salary_ends_on, proration_numerator, proration_denominator,"
            " proration_basis) = CASE WHEN kind = 'salary' THEN 7 ELSE 0 END",
            name="salary_fields",
        ),
        CheckConstraint(
            "kind <> 'salary' OR rate_model IS NULL", name="salary_no_rate"
        ),
        CheckConstraint("salary_amount > 0", name="salary_amount_positive"),
        CheckConstraint("salary_starts_on <= salary_ends_on", name="salary_dates"),
        CheckConstraint(
            "proration_denominator > 0 AND proration_numerator >= 0"
            " AND proration_numerator <= proration_denominator",
            name="proration_fraction",
        ),
        CheckConstraint(
            _one_of("proration_basis", PRORATION_BASES), name="proration_basis_valid"
        ),
        CheckConstraint(
            "(pay_item_id IS NOT NULL) = (kind = 'pay_item')", name="pay_item_fields"
        ),
        CheckConstraint(
            "kind <> 'pay_item' OR rate_model IS NULL", name="pay_item_no_rate"
        ),
        CheckConstraint(
            "(payroll_adjustment_id IS NOT NULL) = (kind = 'adjustment')",
            name="adjustment_fields",
        ),
        ForeignKeyConstraint(
            ["payroll_period_id", "tutor_user_id"],
            ["payroll_periods.id", "payroll_periods.tutor_user_id"],
            name="fk_payroll_lines_period_tutor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["pay_item_id", "tutor_user_id"],
            ["pay_items.id", "pay_items.tutor_user_id"],
            name="fk_payroll_lines_pay_item_tutor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["payroll_adjustment_id", "payroll_period_id", "tutor_user_id"],
            [
                "payroll_adjustments.id",
                "payroll_adjustments.target_period_id",
                "payroll_adjustments.tutor_user_id",
            ],
            name="fk_payroll_lines_adjustment",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "tutor_user_id", name=LINE_TUTOR_KEY),
        Index(
            LINE_SESSION_UNIQUE,
            "class_session_id",
            unique=True,
            postgresql_where=text(_one_of("kind", SESSION_LINE_KINDS)),
        ),
        Index(
            LINE_SALARY_PIECE,
            "payroll_period_id",
            "salary_starts_on",
            unique=True,
            postgresql_where=text("kind = 'salary'"),
        ),
        Index(
            LINE_PAY_ITEM_PERIOD,
            "pay_item_id",
            "payroll_period_id",
            unique=True,
            postgresql_where=text("kind = 'pay_item'"),
        ),
        Index(
            LINE_ADJUSTMENT_UNIQUE,
            "payroll_adjustment_id",
            unique=True,
            postgresql_where=text("kind = 'adjustment'"),
        ),
        Index("ix_payroll_lines_payroll_period_id", "payroll_period_id"),
        Index(
            "ix_payroll_lines_class_session_id",
            "class_session_id",
            postgresql_where=text("class_session_id IS NOT NULL"),
        ),
        Index(
            "ix_payroll_lines_compensation_rate_id",
            "compensation_rate_id",
            postgresql_where=text("compensation_rate_id IS NOT NULL"),
        ),
        Index(
            "ix_payroll_lines_session_pay_rule_id",
            "session_pay_rule_id",
            postgresql_where=text("session_pay_rule_id IS NOT NULL"),
        ),
        Index(
            "ix_payroll_lines_tutor_pay_terms_id",
            "tutor_pay_terms_id",
            postgresql_where=text("tutor_pay_terms_id IS NOT NULL"),
        ),
        Index(
            "ix_payroll_lines_pay_item_id",
            "pay_item_id",
            postgresql_where=text("pay_item_id IS NOT NULL"),
        ),
    )

    payroll_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(LINE_KIND_MAX))
    amount: Mapped[Money] = mapped_column(MoneyType())
    class_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_closeout_approvals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    service_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    class_kind: Mapped[str | None] = mapped_column(
        String(CLASS_KIND_MAX), nullable=True
    )
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_model: Mapped[str | None] = mapped_column(
        String(RATE_MODEL_MAX), nullable=True
    )
    rate: Mapped[Rate | None] = mapped_column(RateType(), nullable=True)
    compensation_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compensation_rates.id", ondelete="RESTRICT"),
        nullable=True,
    )
    session_pay_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_pay_rules.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cumulative_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cohort_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tutor_pay_terms_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tutor_pay_terms.id", ondelete="RESTRICT"),
        nullable=True,
    )
    salary_amount: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    salary_starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    salary_ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    proration_numerator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proration_denominator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proration_basis: Mapped[str | None] = mapped_column(
        String(PRORATION_BASIS_MAX), nullable=True
    )
    pay_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    payroll_adjustment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )


def _none_of(*columns: str) -> str:
    return f"num_nonnulls({', '.join(columns)}) = 0"


def _all_of(*columns: str) -> str:
    return f"num_nonnulls({', '.join(columns)}) = {len(columns)}"


SESSION_SNAPSHOT: Final = ("line_kind", "service_date", "class_kind", "minutes")
ADJUSTMENT_KIND_CHECKS: Final = {
    "late_approval_fields": (
        f"kind <> '{LATE_APPROVAL}' OR ("
        + _all_of(
            "class_session_id", "origin_period_id", "source_key", *SESSION_SNAPSHOT
        )
        + " AND "
        + _none_of(
            "origin_line_id", "reason", "created_by_user_id", "paired_adjustment_id"
        )
        + " AND (approval_id IS NOT NULL) = (source_key LIKE 'approval:%'))"
    ),
    "hours_correction_fields": (
        f"kind <> '{HOURS_CORRECTION}' OR ("
        + _all_of(
            "origin_line_id",
            "class_session_id",
            "approval_id",
            "source_key",
            *SESSION_SNAPSHOT,
        )
        + " AND "
        + _none_of("origin_period_id", "reason", "paired_adjustment_id")
        + ")"
    ),
    "manual_fields": (
        f"kind <> '{MANUAL}' OR ("
        + _all_of("reason", "created_by_user_id")
        + " AND "
        + _none_of(
            "source_key",
            *ADJUSTMENT_SNAPSHOT_COLUMNS,
            "paired_adjustment_id",
            "approval_id",
            "origin_period_id",
        )
        + " AND num_nonnulls(origin_line_id, class_session_id) <= 1)"
    ),
    "carry_forward_fields": (
        f"kind <> '{CARRY_FORWARD}' OR ("
        + _all_of("origin_period_id", "created_by_user_id")
        + " AND "
        + _none_of(
            "source_key",
            *ADJUSTMENT_SNAPSHOT_COLUMNS,
            "origin_line_id",
            "class_session_id",
            "approval_id",
        )
        + ")"
    ),
}
"""One CHECK per kind (#111 A2): what it names and what it leaves ``NULL``."""


class PayrollAdjustment(Audited, Model):
    """A correction to finalized work, posted into an open period (data-model
    §11.9; ADR-0017 E11 and its #111 notes). Append-only: a trigger refuses
    ``UPDATE``, ``DELETE`` and ``TRUNCATE`` (migration ``0080``). Exactly one
    ``adjustment`` line shows it in ``target_period_id``.

    ``late_approval`` and ``hours_correction`` are the job's, keyed by
    ``source_key`` (``approval:<approval_id>`` or
    ``cancellation:<class_session_id>``), with ``created_by_user_id`` ``NULL``.
    ``manual`` and ``carry_forward`` are M7-08c's routes. The snapshot columns are
    what an automatic adjustment was priced on, so the next correction of the same
    session can be priced on them (#111 E2). ``amount``, ``rate`` and ``reason``
    are masked in the audit log (#111 E9)."""

    __tablename__ = "payroll_adjustments"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"amount", "rate", "reason"})
    __table_args__ = (
        CheckConstraint(_one_of("kind", ADJUSTMENT_KINDS), name="kind_valid"),
        CheckConstraint("amount <> 0", name="amount_non_zero"),
        CheckConstraint(
            f"char_length(reason) BETWEEN 1 AND {ADJUSTMENT_REASON_MAX}"
            " AND reason = btrim(reason)",
            name="reason_trimmed",
        ),
        CheckConstraint(
            _one_of("line_kind", SESSION_LINE_KINDS), name="line_kind_valid"
        ),
        CheckConstraint(_one_of("class_kind", CLASS_KINDS), name="class_kind_valid"),
        CheckConstraint(_one_of("rate_model", RATE_MODELS), name="rate_model_valid"),
        CheckConstraint("minutes >= 0", name="minutes_non_negative"),
        CheckConstraint(
            "(rate_model IS NULL) = (rate IS NULL)"
            " AND (rate IS NULL) = (compensation_rate_id IS NULL)",
            name="rate_snapshot",
        ),
        CheckConstraint(
            "(session_pay_rule_id IS NOT NULL)"
            f" = COALESCE({_one_of('line_kind', LINE_EVENT_KINDS)}, false)",
            name="pay_rule_with_event",
        ),
        CheckConstraint(
            "(cumulative_sessions IS NOT NULL) = (cohort_sessions IS NOT NULL)"
            " AND (cumulative_sessions IS NOT NULL)"
            " = COALESCE(rate_model = 'per_course', false)",
            name="course_share",
        ),
        *(
            CheckConstraint(condition, name=name)
            for name, condition in ADJUSTMENT_KIND_CHECKS.items()
        ),
        ForeignKeyConstraint(
            ["target_period_id", "tutor_user_id"],
            ["payroll_periods.id", "payroll_periods.tutor_user_id"],
            name="fk_payroll_adjustments_target_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["origin_line_id", "tutor_user_id"],
            ["payroll_lines.id", "payroll_lines.tutor_user_id"],
            name="fk_payroll_adjustments_origin_line",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint("source_key", name=ADJUSTMENT_SOURCE_KEY),
        UniqueConstraint(
            "id", "target_period_id", "tutor_user_id", name=ADJUSTMENT_TARGET_KEY
        ),
        Index(
            ADJUSTMENT_FIRST_PAYMENT,
            "class_session_id",
            unique=True,
            postgresql_where=text(
                "kind = 'late_approval'"
                " OR (kind = 'manual' AND class_session_id IS NOT NULL)"
            ),
        ),
        Index("ix_payroll_adjustments_target_period_id", "target_period_id"),
        Index("ix_payroll_adjustments_origin_line_id", "origin_line_id"),
        Index(
            "ix_payroll_adjustments_class_session_id",
            "class_session_id",
            postgresql_where=text("class_session_id IS NOT NULL"),
        ),
    )

    kind: Mapped[str] = mapped_column(String(ADJUSTMENT_KIND_MAX))
    tutor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    target_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    amount: Mapped[Money] = mapped_column(MoneyType())
    origin_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    origin_period_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll_periods.id", ondelete="RESTRICT"),
        nullable=True,
    )
    class_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("class_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_closeout_approvals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    paired_adjustment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll_adjustments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    line_kind: Mapped[str | None] = mapped_column(String(LINE_KIND_MAX), nullable=True)
    service_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    class_kind: Mapped[str | None] = mapped_column(
        String(CLASS_KIND_MAX), nullable=True
    )
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_model: Mapped[str | None] = mapped_column(
        String(RATE_MODEL_MAX), nullable=True
    )
    rate: Mapped[Rate | None] = mapped_column(RateType(), nullable=True)
    compensation_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("compensation_rates.id", ondelete="RESTRICT"),
        nullable=True,
    )
    session_pay_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_pay_rules.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cumulative_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cohort_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)


def _trimmed_text(column: str, maximum: int) -> str:
    return (
        f"char_length({column}) BETWEEN 1 AND {maximum} AND {column} = btrim({column})"
    )


class PayrollPayment(Audited, Model):
    """A payment made to a tutor outside the platform, against a finalized
    period (data-model §11.10; ADR-0017 E13; #112). Append-only: a trigger
    refuses ``UPDATE``, ``DELETE`` and ``TRUNCATE`` (migration ``0081``). A
    mistake is reversed (:class:`PayrollPaymentReversal`), never edited.

    ``reference`` and ``note`` are the proof (no file, owner decision
    2026-10-21). ``amount``, ``reference`` and ``note`` are masked in the audit
    log (#112 A2): Administrators hold ``audit.view`` and see no pay."""

    __tablename__ = "payroll_payments"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset(
        {"amount", "reference", "note"}
    )
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            _trimmed_text("reference", PAYMENT_REFERENCE_MAX), name="reference_trimmed"
        ),
        CheckConstraint(_trimmed_text("note", PAYMENT_NOTE_MAX), name="note_trimmed"),
        Index("ix_payroll_payments_payroll_period_id", "payroll_period_id"),
        Index("ix_payroll_payments_paid_on", "paid_on"),
    )

    payroll_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payroll_periods.id", ondelete="RESTRICT")
    )
    paid_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Money] = mapped_column(MoneyType())
    method_code: Mapped[str] = mapped_column(
        String(PAYMENT_METHOD_CODE_MAX),
        ForeignKey("payment_methods.code", ondelete="RESTRICT"),
    )
    reference: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )


class PayrollPaymentReversal(Audited, Model):
    """The reversal of a mistaken payroll payment (data-model §11.11; ADR-0017
    E12; #112). At most one per payment (the UNIQUE). Append-only (migration
    ``0081``). ``reason`` is masked in the audit log."""

    __tablename__ = "payroll_payment_reversals"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"reason"})
    __table_args__ = (
        CheckConstraint(
            _trimmed_text("reason", PAYMENT_REVERSAL_REASON_MAX),
            name="reason_trimmed",
        ),
        UniqueConstraint("payroll_payment_id", name=PAYMENT_REVERSAL_KEY),
    )

    payroll_payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "payroll_payments.id", ondelete="RESTRICT", name=PAYMENT_REVERSAL_FK
        ),
    )
    reason: Mapped[str] = mapped_column(Text)
    reversed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )


class Payslip(Audited, Model):
    """The payslip of one finalized period with lines, issued by ``finalize``
    in its transaction (data-model §11.12; ADR-0017 E14; #113). Append-only
    (migration ``0082``'s ``payslips_append_only()`` triggers, SQLSTATE
    42501). ``snapshot`` is a ``payroll.payslips.PayslipSnapshotV1`` (``"v":
    1``); the PDF is built from the row alone. ``language`` and
    ``template_version`` never change. ``snapshot`` is masked in the audit log
    (#113 A2): it holds amounts, descriptions and reasons, and Administrators
    hold ``audit.view``."""

    __tablename__ = "payslips"
    __audit_mask__: ClassVar[frozenset[str]] = frozenset({"snapshot"})
    __table_args__ = (
        CheckConstraint(f"number ~ '{PAYSLIP_NUMBER_PATTERN}'", name="number_format"),
        CheckConstraint(_one_of("language", PAYSLIP_LANGUAGES), name="language"),
        CheckConstraint("template_version > 0", name="template_version_positive"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="snapshot_object"),
        UniqueConstraint("number", name=PAYSLIP_NUMBER_KEY),
        UniqueConstraint("payroll_period_id", name=PAYSLIP_PERIOD_KEY),
        ForeignKeyConstraint(
            ["payroll_period_id", "tutor_user_id"],
            ["payroll_periods.id", "payroll_periods.tutor_user_id"],
            name="fk_payslips_period_tutor",
            ondelete="RESTRICT",
        ),
        Index("ix_payslips_tutor_user_id_issued_at", "tutor_user_id", "issued_at"),
    )

    number: Mapped[str] = mapped_column(String(PAYSLIP_NUMBER_MAX))
    payroll_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    tutor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str] = mapped_column(String(2))
    template_version: Mapped[int] = mapped_column(SmallInteger)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
