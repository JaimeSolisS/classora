"""Payroll shapes (#105, #106, #441, #109): the compensation bodies, their items,
the lookup results, the settings, the session pay rules and the pay items.

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

The pay item bodies (:class:`PayItemCreate`): ``amount`` is a
:class:`~app.core.money.Money` string (``money_*``) above zero
(``amount_not_positive``), ``description`` and a type's ``name`` are trimmed and
not empty (``text_empty``), too long (``text_too_long``) or holding a NUL or a
lone surrogate (``text_invalid``), and ``effective_to``
is only for a recurring item (``effective_to_not_allowed``), on or after
``effective_from`` (``effective_to_before_from``).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.core.money import Money, Rate

type PayFrequency = Literal["weekly", "biweekly", "monthly"]
type ClassKind = Literal["individual", "group", "course"]
type RateModel = Literal["per_hour", "per_session", "per_course"]
type CompensationStatus = Literal["current", "upcoming", "past"]
type ProrationBasis = Literal["calendar_days", "weekdays"]
type PayRuleEvent = Literal["late_cancellation", "no_show", "makeup"]
type PayRuleMode = Literal["none", "percent", "fixed"]
type PayItemDirection = Literal["bonus", "deduction"]
type PayItemRecurrence = Literal["one_time", "recurring"]
type PayItemPeriodStatus = Literal["open", "prepared"]

SETTINGS_DATE_MIN: Final = date(2000, 1, 1)
SETTINGS_DATE_MAX: Final = date(2099, 12, 31)
MONDAY: Final = 0
PERCENT_MAX: Final = Rate(1000)
PAY_ITEM_TYPE_NAME_MAX: Final = 100
PAY_ITEM_DESCRIPTION_MAX: Final = 500
RETURN_REASON_MAX: Final = 1000


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


def _encodable(text: str) -> bool:
    """False for a lone surrogate, which JSON can carry and UTF-8 can't."""
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True


def _trimmed(value: str, maximum: int) -> str:
    text = value.strip()
    if not text:
        raise PydanticCustomError("text_empty", "This can't be empty.")
    if "\x00" in text or not _encodable(text):
        # PostgreSQL text holds neither, so they would reach the database as a 500.
        raise PydanticCustomError("text_invalid", "This has an invalid character.")
    if len(text) > maximum:
        raise PydanticCustomError(
            "text_too_long",
            "Use at most {maximum} characters.",
            {"maximum": maximum},
        )
    return text


class PayItemTypeCreate(BaseModel):
    """``POST /api/v1/payroll/pay-item-types`` (#109 D2)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    direction: PayItemDirection = Field(
        description="It can't change later: a wrong direction is a new type."
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _trimmed(value, PAY_ITEM_TYPE_NAME_MAX)


class PayItemTypeUpdate(BaseModel):
    """``PATCH /api/v1/payroll/pay-item-types/{type_id}`` (#109 D3). A missing
    field stays as it is. ``direction`` isn't a field."""

    model_config = ConfigDict(extra="forbid")

    version: int
    name: str | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _trimmed(value, PAY_ITEM_TYPE_NAME_MAX)


class PayItemTypeItem(BaseModel):
    """A bonus or deduction type (#109 D1)."""

    id: uuid.UUID
    name: str
    direction: PayItemDirection
    is_active: bool
    version: int


class PayItemCreate(BaseModel):
    """``POST /api/v1/payroll/tutors/{id}/pay-items`` (#109 F2). Any
    ``effective_from`` is accepted, past or future."""

    model_config = ConfigDict(extra="forbid")

    pay_item_type_id: uuid.UUID
    description: str = Field(
        description="Up to 500 characters. The tutor reads it once it is finalized."
    )
    amount: Money = Field(
        description=(
            "Above zero, whatever the direction. A recurring amount is per"
            " period, with no proration."
        )
    )
    recurrence: PayItemRecurrence
    effective_from: date
    effective_to: date | None = None

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return _trimmed(value, PAY_ITEM_DESCRIPTION_MAX)

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: Money) -> Money:
        if not value.is_positive():
            raise PydanticCustomError(
                "amount_not_positive", "The amount must be above zero."
            )
        return value

    @field_validator("effective_to")
    @classmethod
    def _end(cls, value: date | None, info: ValidationInfo) -> date | None:
        if value is None:
            return None
        if info.data.get("recurrence") == "one_time":
            raise PydanticCustomError(
                "effective_to_not_allowed", "Only a recurring item has an end date."
            )
        start = info.data.get("effective_from")
        if start is not None and value < start:
            raise PydanticCustomError(
                "effective_to_before_from",
                "The end date can't be before the start date.",
            )
        return value


class PayItemUpdate(BaseModel):
    """``PATCH /api/v1/payroll/tutors/{id}/pay-items/{item_id}`` (#109 F5):
    only the end moves, and ``null`` reopens it. Anything else is a delete and a
    new item (#109 E9)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    effective_to: date | None


class PayItemTypeReference(BaseModel):
    """An item's type."""

    id: uuid.UUID
    name: str
    direction: PayItemDirection


class PayItemPlacedPeriod(BaseModel):
    """The period a one-time item lands in (#109 E3)."""

    id: uuid.UUID
    starts_on: date
    ends_on: date
    status: PayItemPeriodStatus


class PayItemDetail(BaseModel):
    """One bonus or deduction (#109 F1). ``amount`` is positive: the type's
    direction gives a line its sign. ``applied`` is true once a finalized line
    uses it. ``placed_period`` is where a one-time item lands, ``null`` when no
    period can take it yet or it has been paid, and on every recurring item."""

    id: uuid.UUID
    tutor_user_id: uuid.UUID
    pay_item_type: PayItemTypeReference
    description: str
    amount: Money
    recurrence: PayItemRecurrence
    effective_from: date
    effective_to: date | None
    version: int
    applied: bool
    placed_period: PayItemPlacedPeriod | None
    created_at: datetime


type LineKind = Literal[
    "session",
    "late_cancellation",
    "no_show",
    "makeup",
    "salary",
    "pay_item",
    "adjustment",
]
type AdjustmentKind = Literal[
    "late_approval", "hours_correction", "manual", "carry_forward"
]
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


class PayrollPeriodFacts(PayrollPeriodSummary):
    """A period's row (#110), for the workflow. Not a response: it holds ids the
    routes turn into names."""

    prepared_at: datetime | None
    prepared_by_user_id: uuid.UUID | None
    total_amount: Money | None
    finalized_at: datetime | None
    finalized_by_user_id: uuid.UUID | None
    returned_at: datetime | None
    returned_by_user_id: uuid.UUID | None
    return_reason: str | None


class PayrollPeriodRow(PayrollPeriodFacts):
    """One row of the period list (#110 F1): ``total`` is ``None`` when the
    lines sum beyond the money range (F2)."""

    line_count: int
    total: Money | None


class PayrollPeriodTransition(BaseModel):
    """What ``prepare``, ``return`` and ``finalize`` answer (#110 E10). No amount:
    a stored idempotent response holds it in clear, so the page refetches."""

    id: uuid.UUID
    status: PeriodStatus
    prepared_at: datetime | None
    finalized_at: datetime | None


class PayrollPeriodReturn(BaseModel):
    """``POST /api/v1/payroll/periods/{id}/return`` (#110 C1)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(description="Up to 1000 characters.")

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _trimmed(value, RETURN_REASON_MAX)


class PayrollPeriodFinalize(BaseModel):
    """``POST /api/v1/payroll/periods/{id}/finalize`` (#110 E4)."""

    model_config = ConfigDict(extra="forbid")

    prepared_at: AwareDatetime = Field(
        description=(
            "The prepared_at of the review the caller read. Lines can't change"
            " while a period is prepared, so this identifies them."
        )
    )


class PayrollLineItem(BaseModel):
    """One line (#107 H3): ``rate`` is text, and a "covered by salary" line has
    no rate snapshot. A ``salary`` line has no session, and says how its amount
    was reached: ``salary_amount`` × ``proration_numerator`` /
    ``proration_denominator`` for the piece ``salary_starts_on`` to
    ``salary_ends_on`` (#108 H1). A ``pay_item`` line is a fixed amount, negative
    for a deduction, with the type's current name and the item's description
    (#109 G1). An ``adjustment`` line has only its amount and
    ``payroll_adjustment_id``; the ``adjustment_*`` fields come from the
    adjustment: the session, date and class kind it corrects, the line or the
    finalized period it references, and the reason, on ``manual`` only (#111
    G1). No student data."""

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
    pay_item_id: uuid.UUID | None
    pay_item_type_name: str | None
    pay_item_description: str | None
    payroll_adjustment_id: uuid.UUID | None
    adjustment_kind: AdjustmentKind | None
    adjustment_reason: str | None
    adjustment_class_session_id: uuid.UUID | None
    adjustment_service_date: date | None
    adjustment_class_kind: ClassKind | None
    adjustment_origin_line_id: uuid.UUID | None
    adjustment_origin_period_id: uuid.UUID | None


class PayrollPeriodLines(BaseModel):
    """``GET /api/v1/payroll/periods/{id}/lines`` (#107 H3). ``total`` is the
    sum of the rounded lines, and may be negative."""

    period: PayrollPeriodSummary
    lines: list[PayrollLineItem]
    total: Money


class PayrollRecalculation(BaseModel):
    """``POST /api/v1/payroll/periods/{id}/recalculate`` (#107 H2). After the
    run, all the period's lines, the same as ``GET …/lines`` (#109 E2): ``total``
    may be negative, and only ``prepare`` refuses that."""

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
