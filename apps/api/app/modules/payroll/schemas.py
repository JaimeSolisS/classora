"""Payroll shapes (#105, #106, #441): the compensation bodies, their items, the
lookup results, the settings and the session pay rules.

The bodies check everything that needs no database read and report every
failing field in one ``422``. Each rule is a field validator on the later
field of the pair it compares, so the earlier field is in ``info.data``:

- ``salary_amount``: a :class:`~app.core.money.Money` string (``money_*``)
  above zero (``salary_not_positive``), and never without a frequency
  (``salary_without_frequency``);
- ``rate_model``: ``per_course`` only on ``course``
  (``rate_model_not_allowed``);
- ``rate``: a :class:`~app.core.money.Rate` string (``rate_*``), not negative
  (``rate_negative``), and set exactly when ``rate_model`` is
  (``rate_model_rate_mismatch``).

Every field is required: ``null`` is written out, because a ``null`` version
means "not on payroll" or "no rate" from that date.

The settings body (:class:`PayrollSettingsUpdate`) takes dates from 2000-01-01
to 2099-12-31 (``date_out_of_range``), and the anchor is a Monday
(``anchor_not_monday``).

The pay rule body (:class:`SessionPayRuleCreate`) follows the same rule:
``percent`` is a :class:`~app.core.money.Rate` string (``rate_*``) from 0 to
1000 (``percent_out_of_range``), ``amount`` a
:class:`~app.core.money.Money` string (``money_*``), not negative
(``amount_negative``), and each is set exactly when ``mode`` asks for it
(``pay_rule_values_mismatch``, on that field).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from app.core.money import Money, Rate

type PayFrequency = Literal["weekly", "biweekly", "monthly"]
type ClassKind = Literal["individual", "group", "course"]
type RateModel = Literal["per_hour", "per_session", "per_course"]
type CompensationStatus = Literal["current", "upcoming", "past"]
type ProrationBasis = Literal["calendar_days", "weekdays"]
type PayRuleEvent = Literal["late_cancellation", "no_show", "makeup"]
type PayRuleMode = Literal["none", "percent", "fixed"]

SETTINGS_DATE_MIN: Final = date(2000, 1, 1)
SETTINGS_DATE_MAX: Final = date(2099, 12, 31)
MONDAY: Final = 0
PERCENT_MAX: Final = Rate(1000)


def _in_range(value: date) -> date:
    if not SETTINGS_DATE_MIN <= value <= SETTINGS_DATE_MAX:
        raise PydanticCustomError(
            "date_out_of_range", "Choose a date from 2000-01-01 to 2099-12-31."
        )
    return value


class PayTermsCreate(BaseModel):
    """``POST /api/v1/payroll/tutors/{id}/pay-terms``. ``pay_frequency: null``
    means "not on payroll from this date"; ``salary_amount`` is per full pay
    period of this frequency, ``null`` when there is no salary."""

    model_config = ConfigDict(extra="forbid")

    effective_from: date
    pay_frequency: PayFrequency | None
    salary_amount: Money | None

    @field_validator("salary_amount")
    @classmethod
    def _salary(cls, value: Money | None, info: ValidationInfo) -> Money | None:
        if value is None:
            return None
        if not value.is_positive():
            raise PydanticCustomError(
                "salary_not_positive",
                "A salary is above zero. Leave it empty when there is no salary.",
            )
        if "pay_frequency" in info.data and info.data["pay_frequency"] is None:
            raise PydanticCustomError(
                "salary_without_frequency", "A salary needs a pay frequency."
            )
        return value


class CompensationRateCreate(BaseModel):
    """``POST /api/v1/payroll/tutors/{id}/rates``. ``rate_model`` and ``rate``
    are both ``null`` to mean "no rate from this date"."""

    model_config = ConfigDict(extra="forbid")

    class_kind: ClassKind
    effective_from: date
    rate_model: RateModel | None
    rate: Rate | None

    @field_validator("rate_model")
    @classmethod
    def _model(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value == "per_course" and info.data.get("class_kind", "course") != "course":
            raise PydanticCustomError(
                "rate_model_not_allowed", "A per-course rate is for courses only."
            )
        return value

    @field_validator("rate")
    @classmethod
    def _rate(cls, value: Rate | None, info: ValidationInfo) -> Rate | None:
        if value is not None and value.is_negative():
            raise PydanticCustomError("rate_negative", "A rate can't be negative.")
        if "rate_model" in info.data and (info.data["rate_model"] is None) != (
            value is None
        ):
            raise PydanticCustomError(
                "rate_model_rate_mismatch",
                "Set the rate model and the rate together, or leave both empty.",
            )
        return value


class PayTermsItem(BaseModel):
    """One terms version. ``status`` is against the center's local today:
    ``current`` is the version in effect, even when ``pay_frequency`` is
    ``null`` (not on payroll)."""

    id: uuid.UUID
    effective_from: date
    pay_frequency: PayFrequency | None
    salary_amount: Money | None
    status: CompensationStatus


class CompensationRateItem(BaseModel):
    """One rate version; ``status`` as in :class:`PayTermsItem`. A current
    version with a ``null`` rate means "no rate"."""

    id: uuid.UUID
    class_kind: ClassKind
    effective_from: date
    rate_model: RateModel | None
    rate: Rate | None
    status: CompensationStatus


class CurrentTerms(BaseModel):
    """The terms in effect today, on the payroll tutor list."""

    pay_frequency: PayFrequency
    salary_amount: Money | None


class PayrollSettingsDetail(BaseModel):
    """``GET /api/v1/payroll/settings`` (#106 B1). ``payroll_starts_on`` is a
    center-local date: ``null`` means payroll is off and no period opens.
    ``start_locked`` is true once any period exists and ``anchor_locked`` once
    a biweekly one does (#106 E6, E9)."""

    payroll_starts_on: date | None
    biweekly_anchor_on: date
    salary_proration_basis: ProrationBasis
    start_locked: bool
    anchor_locked: bool
    version: int
    updated_at: datetime


class PayrollSettingsUpdate(BaseModel):
    """``PATCH /api/v1/payroll/settings`` (#106 B2). All keys are required;
    ``payroll_starts_on: null`` keeps payroll off. Past dates are allowed."""

    model_config = ConfigDict(extra="forbid")

    version: int
    payroll_starts_on: date | None
    biweekly_anchor_on: date
    salary_proration_basis: ProrationBasis

    @field_validator("payroll_starts_on")
    @classmethod
    def _start(cls, value: date | None) -> date | None:
        return None if value is None else _in_range(value)

    @field_validator("biweekly_anchor_on")
    @classmethod
    def _anchor(cls, value: date) -> date:
        _in_range(value)
        if value.weekday() != MONDAY:
            raise PydanticCustomError(
                "anchor_not_monday", "The biweekly anchor date must be a Monday."
            )
        return value


def _matches_mode(value: object, mode: str, info: ValidationInfo) -> None:
    if "mode" in info.data and (info.data["mode"] == mode) != (value is not None):
        raise PydanticCustomError(
            "pay_rule_values_mismatch",
            "Set the percent only with the percent mode, and the amount only"
            " with the fixed mode.",
        )


class SessionPayRuleCreate(BaseModel):
    """``POST /api/v1/payroll/pay-rules`` (#441 D2). Every field is required,
    with ``null`` written out."""

    model_config = ConfigDict(extra="forbid")

    based_on_id: uuid.UUID = Field(
        description=(
            "The id of the event's latest version (the current one or the last"
            " upcoming one); otherwise 409 version_conflict."
        )
    )
    event: PayRuleEvent
    effective_from: date = Field(
        description=(
            "The first service date it applies to: the center's today or later,"
            " and later than every version of the event (422"
            " invalid_effective_from)."
        )
    )
    mode: PayRuleMode = Field(
        description=(
            "none: pays nothing. percent: a percent of the ordinary pay."
            " fixed: a fixed amount."
        )
    )
    percent: Rate | None = Field(
        description="From 0 to 1000, set only with the percent mode."
    )
    amount: Money | None = Field(description="Set only with the fixed mode.")

    @field_validator("percent")
    @classmethod
    def _percent(cls, value: Rate | None, info: ValidationInfo) -> Rate | None:
        if value is not None and (value.is_negative() or value > PERCENT_MAX):
            raise PydanticCustomError(
                "percent_out_of_range", "Choose a percent from 0 to 1000."
            )
        _matches_mode(value, "percent", info)
        return value

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: Money | None, info: ValidationInfo) -> Money | None:
        if value is not None and value.is_negative():
            raise PydanticCustomError(
                "amount_negative", "The amount can't be negative."
            )
        _matches_mode(value, "fixed", info)
        return value


class SessionPayRuleItem(BaseModel):
    """One version of an event's rule, in force from ``effective_from`` until
    the next version's. ``percent`` and ``amount`` are ``null`` unless the
    mode uses them. ``created_by_user_id`` is ``null`` on a seeded version."""

    id: uuid.UUID
    event: PayRuleEvent
    effective_from: date
    mode: PayRuleMode
    percent: Rate | None
    amount: Money | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime


class SessionPayRuleEventOverview(BaseModel):
    """One event's versions."""

    current: SessionPayRuleItem
    upcoming: list[SessionPayRuleItem] = Field(
        description="Versions that take effect later, oldest first."
    )
    history: list[SessionPayRuleItem] = Field(
        description="Versions no longer in force, newest first (at most 50)."
    )


class SessionPayRuleOverview(BaseModel):
    """``GET /api/v1/payroll/pay-rules`` (#441 D1). ``today`` is the center's
    local date: the earliest date a new version can take."""

    today: date
    late_cancellation: SessionPayRuleEventOverview
    no_show: SessionPayRuleEventOverview
    makeup: SessionPayRuleEventOverview


@dataclass(frozen=True)
class SessionPayRuleVersion:
    """A version as the calculation reads it (#441 B3): ``percent`` for
    ``Rate.to_money(percent=)``, ``amount`` as it is."""

    id: uuid.UUID
    event: PayRuleEvent
    effective_from: date
    mode: PayRuleMode
    percent: Rate | None
    amount: Money | None

    @property
    def fixed_pay(self) -> Money | None:
        """``amount`` under the name the calculation reads, whose files keep
        away from ``.amount`` (#107 B2)."""
        return self.amount


@dataclass(frozen=True)
class RateInEffect:
    """The result of :func:`app.modules.payroll.service.rate_in_effect`."""

    compensation_rate_id: uuid.UUID
    rate_model: str
    rate: Rate
    effective_from: date


@dataclass(frozen=True)
class TermsInEffect:
    """The result of :func:`app.modules.payroll.service.terms_in_effect`."""

    tutor_pay_terms_id: uuid.UUID
    pay_frequency: str
    salary_amount: Money | None
    effective_from: date


@dataclass(frozen=True)
class CompensationSummary:
    """One tutor's terms and rated class kinds in effect today."""

    current_terms: CurrentTerms | None = None
    current_rate_kinds: tuple[ClassKind, ...] = ()


type LineKind = Literal["session", "late_cancellation", "no_show", "makeup", "salary"]
type PeriodStatus = Literal["open", "prepared", "finalized"]
type UnresolvedReason = Literal["before_first_period", "no_pay_terms", "no_rate"]
type UnresolvedSource = Literal["closeout", "cancellation"]


class PayrollPeriodSummary(BaseModel):
    """The head of a period's lines (#107 H3)."""

    id: uuid.UUID
    tutor_user_id: uuid.UUID
    starts_on: date
    ends_on: date
    pay_frequency: PayFrequency
    status: PeriodStatus


class PayrollLineItem(BaseModel):
    """One line (#107 H3): ``rate`` is text, and a "covered by salary" line has
    no rate snapshot. A ``salary`` line has no session, and says how its amount
    was reached: ``salary_amount`` × ``proration_numerator`` /
    ``proration_denominator`` for the piece ``salary_starts_on`` to
    ``salary_ends_on`` (#108 H1). No student data."""

    id: uuid.UUID
    kind: LineKind
    amount: Money
    class_session_id: uuid.UUID | None
    approval_id: uuid.UUID | None
    service_date: date | None
    class_kind: ClassKind | None
    minutes: int | None
    rate_model: RateModel | None
    rate: Rate | None
    compensation_rate_id: uuid.UUID | None
    session_pay_rule_id: uuid.UUID | None
    cumulative_sessions: int | None
    cohort_sessions: int | None
    tutor_pay_terms_id: uuid.UUID | None
    salary_amount: Money | None
    salary_starts_on: date | None
    salary_ends_on: date | None
    proration_numerator: int | None
    proration_denominator: int | None
    proration_basis: ProrationBasis | None


class PayrollPeriodLines(BaseModel):
    """``GET /api/v1/payroll/periods/{id}/lines`` (#107 H3). ``total`` is the
    sum of the rounded lines."""

    period: PayrollPeriodSummary
    lines: list[PayrollLineItem]
    total: Money


class PayrollRecalculation(BaseModel):
    """``POST /api/v1/payroll/periods/{id}/recalculate`` (#107 H2)."""

    line_count: int
    total: Money


class UnresolvedWorkItem(BaseModel):
    """One piece of approved work that can't be paid (#443 C2). No amount, no
    minutes, no student data."""

    class_session_id: uuid.UUID
    tutor_user_id: uuid.UUID
    tutor_display_name: str
    service_date: date
    class_kind: ClassKind
    source: UnresolvedSource
    reason: UnresolvedReason
    blocking: bool
