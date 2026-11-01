"""Payroll tables (#105, #106, #441; ADR-0017 E1, E2, E4, E6; data-model
§11.1-§11.5, §14.2, §14.3).

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
opens the rows and the status moves carry named events (#110).

``session_pay_rules`` is the center-wide list of late-cancellation, no-show and
makeup rule versions, guarded by time (migration ``0075``): nothing changes,
and only an upcoming version can go away. ``percent`` and ``amount`` are masked
in the audit log, as above.
"""

import uuid
from datetime import date, datetime
from typing import ClassVar, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
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

PAY_FREQUENCY_MAX: Final = 10
PERIOD_STATUS_MAX: Final = 10
PRORATION_BASIS_MAX: Final = 20
CLASS_KIND_MAX: Final = 20
RATE_MODEL_MAX: Final = 20
PAY_RULE_EVENT_MAX: Final = 20
PAY_RULE_MODE_MAX: Final = 10

TERMS_KEY: Final = "uq_tutor_pay_terms_tutor_user_id_effective_from"
RATE_KEY: Final = "uq_compensation_rates_tutor_user_id_class_kind_effective_from"
PAY_RULE_KEY: Final = "uq_session_pay_rules_event_effective_from"
PERIOD_START_KEY: Final = "uq_payroll_periods_tutor_user_id_starts_on"
PERIOD_END_KEY: Final = "uq_payroll_periods_tutor_user_id_ends_on"
PERIOD_TARGET_KEY: Final = "uq_payroll_periods_id_tutor_user_id"
PERIOD_OVERLAP: Final = "ex_payroll_periods_tutor_overlap"
PERIOD_RANGE_SQL: Final = "daterange(starts_on, ends_on, '[]')"
"""The inclusive span of a period."""


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
