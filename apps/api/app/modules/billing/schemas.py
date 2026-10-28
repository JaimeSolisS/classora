"""Billing shapes: the price catalog (#89 B1, B3, B5, C1) and the ledger
core's drafts and results (#90 C3, C4, D1-D4; at the end of this module).

The request bodies check everything that needs no database read, and
report every failing field in one ``422`` (each rule is a field validator
on the later field of the pair it compares, so the earlier fields are in
``info.data``):

- ``pricing_model``: ``per_session`` or ``full_course`` only (#89 E5;
  ``per_period`` waits for #93 and ``package`` for #94), and allowed for the
  class type (``pricing_model_not_allowed``);
- ``cohort_id`` only for ``course`` and ``group_id`` only for ``group``
  (``scope_not_allowed``), never both (``scope_conflict``);
- a scoped item has no level (``level_not_allowed``) and no duration
  (``duration_not_allowed``); a duration only with ``per_session``
  (``duration_not_allowed``) and from 15 to 480 (``duration_out_of_range``);
- ``effective_from`` from 2000-01-01 to 2099-12-31 (``date_out_of_range``);
- ``amount`` a :class:`~app.core.money.Money` string (``money_*``) that is
  not negative (``amount_negative``); required and not ``null`` on create.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from app.core.errors import FieldError
from app.core.money import Money
from app.modules.billing.discounts import (
    DiscountAudience,
    DiscountEligibility,
    DiscountKind,
    DiscountLine,
)
from app.modules.billing.models import (
    ADJUSTMENT_KINDS,
    ALLOWED_MODELS,
    DESCRIPTION_MAX,
    DISCOUNT_NAME_MAX,
    DURATION_MAX,
    DURATION_MIN,
    MANUAL,
    REASON_MAX,
    SCHEDULED_MINUTES_MAX,
    SCHEDULED_MINUTES_MIN,
    SOURCE_KEY_MAX,
)

type ClassType = Literal["individual", "group", "course"]
type CatalogPricingModel = Literal["per_session", "full_course"]
"""The pricing models the #89 routes accept (E5)."""
type PriceVersionStatus = Literal["past", "current", "upcoming"]

EFFECTIVE_FROM_MIN: Final = date(2000, 1, 1)
EFFECTIVE_FROM_MAX: Final = date(2099, 12, 31)


def _effective_from(value: date) -> date:
    if not EFFECTIVE_FROM_MIN <= value <= EFFECTIVE_FROM_MAX:
        raise PydanticCustomError(
            "date_out_of_range", "Choose a date from 2000-01-01 to 2099-12-31."
        )
    return value


def _not_negative(value: Money | None) -> Money | None:
    if value is not None and value.is_negative():
        raise PydanticCustomError("amount_negative", "The amount can't be negative.")
    return value


class ServicePriceCreate(BaseModel):
    """``POST /api/v1/billing/service-prices``: the item's key and its first
    version (#89 B3). Fields are in rule order (see the module docstring)."""

    model_config = ConfigDict(extra="forbid")

    class_type: ClassType
    pricing_model: CatalogPricingModel
    subject_id: uuid.UUID
    cohort_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    education_level_id: uuid.UUID | None = None
    duration_minutes: int | None = None
    effective_from: date
    amount: Money

    @field_validator("pricing_model")
    @classmethod
    def _model_allowed(cls, value: str, info: ValidationInfo) -> str:
        class_type = info.data.get("class_type")
        if class_type is not None and value not in ALLOWED_MODELS[class_type]:
            raise PydanticCustomError(
                "pricing_model_not_allowed",
                "This pricing model isn't available for the class type.",
            )
        return value

    @field_validator("cohort_id")
    @classmethod
    def _cohort_scope(
        cls, value: uuid.UUID | None, info: ValidationInfo
    ) -> uuid.UUID | None:
        class_type = info.data.get("class_type")
        if value is not None and class_type is not None and class_type != "course":
            raise PydanticCustomError(
                "scope_not_allowed", "Only a course price can be for one cohort."
            )
        return value

    @field_validator("group_id")
    @classmethod
    def _group_scope(
        cls, value: uuid.UUID | None, info: ValidationInfo
    ) -> uuid.UUID | None:
        if value is None:
            return value
        if info.data.get("cohort_id") is not None:
            raise PydanticCustomError(
                "scope_conflict", "Choose a cohort or a group, not both."
            )
        class_type = info.data.get("class_type")
        if class_type is not None and class_type != "group":
            raise PydanticCustomError(
                "scope_not_allowed", "Only a group price can be for one group."
            )
        return value

    @field_validator("education_level_id")
    @classmethod
    def _level(cls, value: uuid.UUID | None, info: ValidationInfo) -> uuid.UUID | None:
        if value is not None and _scoped(info):
            raise PydanticCustomError(
                "level_not_allowed",
                "A cohort or group price applies to every level.",
            )
        return value

    @field_validator("duration_minutes")
    @classmethod
    def _duration(cls, value: int | None, info: ValidationInfo) -> int | None:
        if value is None:
            return value
        if info.data.get("pricing_model") == "full_course" or _scoped(info):
            raise PydanticCustomError(
                "duration_not_allowed",
                "Only a per-session price outside a cohort or group has a duration.",
            )
        if not DURATION_MIN <= value <= DURATION_MAX:
            raise PydanticCustomError(
                "duration_out_of_range", "Choose 15 to 480 minutes."
            )
        return value

    @field_validator("effective_from")
    @classmethod
    def _date_range(cls, value: date) -> date:
        return _effective_from(value)

    @field_validator("amount")
    @classmethod
    def _amount_sign(cls, value: Money) -> Money:
        _not_negative(value)
        return value


def _scoped(info: ValidationInfo) -> bool:
    return (
        info.data.get("cohort_id") is not None or info.data.get("group_id") is not None
    )


class PriceVersionCreate(BaseModel):
    """``POST /api/v1/billing/service-prices/{id}/versions`` (#89 B5).
    ``amount`` is required; an explicit ``null`` means "not offered from
    this date"."""

    model_config = ConfigDict(extra="forbid")

    effective_from: date
    amount: Money | None

    @field_validator("effective_from")
    @classmethod
    def _date_range(cls, value: date) -> date:
        return _effective_from(value)

    @field_validator("amount")
    @classmethod
    def _amount_sign(cls, value: Money | None) -> Money | None:
        return _not_negative(value)


class PriceVersionItem(BaseModel):
    """One version (#89 B1). ``status`` is against the center's local
    today: ``current`` is the version in effect, ``past`` an older one,
    ``upcoming`` one that starts later. ``amount: null`` means "not
    offered"."""

    id: uuid.UUID
    effective_from: date
    amount: Money | None
    status: PriceVersionStatus
    created_at: datetime


@dataclass(frozen=True)
class PriceInEffect:
    """The result of :func:`app.modules.billing.service.lookup_price`
    (#89 C1). ``rank`` is the precedence step that decided (0 scoped, 1
    exact level and duration, 2 exact level, 3 exact duration, 4 any)."""

    service_price_id: uuid.UUID
    price_version_id: uuid.UUID
    effective_from: date
    amount: Money
    rank: int


# --- Ledger core (#90) ---------------------------------------------------------------

type ChargeKind = Literal["session", "group_enrollment", "cohort_enrollment", "manual"]
"""The charge kinds of #90 (E3); #93 and #94 add theirs."""
type AdjustmentKind = Literal[
    "duration_change",
    "cancellation_credit",
    "closure_credit",
    "enrollment_removal",
    "withdrawal",
    "manual",
]
type ChargeStatus = Literal["void", "paid", "overdue", "partially_paid", "pending"]
type LedgerEntryType = Literal[
    "charge",
    "adjustment",
    "payment",
    "payment_reversal",
    "allocation",
    "allocation_release",
    "refund",
]

_CHARGE_SOURCES: Final[dict[str, str | None]] = {
    "session": "class_session_id",
    "group_enrollment": "group_enrollment_id",
    "cohort_enrollment": "cohort_enrollment_id",
    MANUAL: None,
}
"""The source column each charge kind needs (``None``: no source)."""
_SOURCE_COLUMNS: Final = (
    "class_session_id",
    "group_enrollment_id",
    "cohort_enrollment_id",
)


def _require_money(value: object, name: str) -> Money:
    if not isinstance(value, Money):
        raise ValueError(f"{name} must be Money")
    return value


def _check_staff_text(value: str | None, name: str, max_length: int) -> None:
    if value is None:
        return
    if value != value.strip() or not 1 <= len(value) <= max_length:
        raise ValueError(f"{name} must be trimmed text of 1 to {max_length} characters")


@dataclass(frozen=True, kw_only=True)
class ChargeDraft:
    """A charge to post with :func:`app.modules.billing.service.post_charge`
    (#90 C3). ``kind`` needs exactly its source id (none for ``manual``);
    ``scheduled_minutes`` only and always for ``session``; ``description``
    only and always for ``manual``. ``amount = list_amount −
    discount_amount``, never negative. The due date is ``due_date_base``,
    plus the family's ``due_offset_days`` when ``apply_family_offset``
    (callers pass ``False`` for a staff-entered due date, E11).

    A draft that breaks a rule raises :class:`ValueError` when it is built,
    so before any SQL (the CHECKs are the backstop)."""

    student_id: uuid.UUID
    kind: ChargeKind
    service_date: date
    list_amount: Money
    due_date_base: date
    class_session_id: uuid.UUID | None = None
    group_enrollment_id: uuid.UUID | None = None
    cohort_enrollment_id: uuid.UUID | None = None
    scheduled_minutes: int | None = None
    price_version_id: uuid.UUID | None = None
    custom_price_id: uuid.UUID | None = None
    education_level_id: uuid.UUID | None = None
    discount_amount: Money = Money.zero()
    discounts: tuple[DiscountLine, ...] = ()
    apply_family_offset: bool = True
    description: str | None = None

    @classmethod
    def priced(cls, quote: Quote, **source_fields: Any) -> ChargeDraft:
        """A draft priced by :func:`~app.modules.billing.service.quote`
        (#95 B5): ``list_amount``, ``discount_amount``, ``discounts`` and
        ``price_version_id`` or ``custom_price_id`` come from the quote;
        ``source_fields`` are the rest (student, kind, source, dates…)."""
        return cls(
            list_amount=quote.list_amount,
            discount_amount=quote.discount_amount,
            discounts=quote.discounts,
            price_version_id=quote.price_version_id,
            custom_price_id=quote.custom_price_id,
            **source_fields,
        )

    def __post_init__(self) -> None:
        if self.kind not in _CHARGE_SOURCES:
            raise ValueError(f"unknown charge kind {self.kind!r}")
        needed = _CHARGE_SOURCES[self.kind]
        for column in (
            "class_session_id",
            "group_enrollment_id",
            "cohort_enrollment_id",
        ):
            if (getattr(self, column) is not None) != (column == needed):
                raise ValueError(f"a {self.kind} charge needs exactly its source")
        if (self.scheduled_minutes is not None) != (self.kind == "session"):
            raise ValueError("scheduled_minutes is set for a session charge only")
        if self.scheduled_minutes is not None and not (
            SCHEDULED_MINUTES_MIN <= self.scheduled_minutes <= SCHEDULED_MINUTES_MAX
        ):
            raise ValueError("scheduled_minutes out of range")
        if (self.description is not None) != (self.kind == MANUAL):
            raise ValueError("a description is set for a manual charge only")
        _check_staff_text(self.description, "description", DESCRIPTION_MAX)
        list_amount = _require_money(self.list_amount, "list_amount")
        discount = _require_money(self.discount_amount, "discount_amount")
        if list_amount.is_negative() or discount.is_negative():
            raise ValueError("charge amounts can't be negative")
        if discount > list_amount:
            raise ValueError("discount_amount can't exceed list_amount")
        self._check_pricing(discount)

    def _check_pricing(self, discount: Money) -> None:
        """#95 B5: at most one price source; a manual charge has neither a
        custom price nor discounts; the lines are numbered 1…n, positive,
        and, when there are lines, add up to ``discount_amount`` (a draft
        without lines keeps the #90 behaviour of a bare
        ``discount_amount``)."""
        if self.price_version_id is not None and self.custom_price_id is not None:
            raise ValueError("a charge has a price version or a custom price, not both")
        if self.kind == MANUAL and (self.custom_price_id is not None or self.discounts):
            raise ValueError("a manual charge has no custom price and no discounts")
        lines = tuple(self.discounts)
        if [line.position for line in lines] != list(range(1, len(lines) + 1)):
            raise ValueError("discount lines are numbered 1 to n")
        if any(not line.amount.is_positive() for line in lines):
            raise ValueError("a discount line is positive")
        if lines and Money.total(line.amount for line in lines) != discount:
            raise ValueError("the discount lines add up to discount_amount")

    @property
    def amount(self) -> Money:
        """``list_amount − discount_amount`` (I7)."""
        return self.list_amount - self.discount_amount

    @property
    def source_id(self) -> uuid.UUID | None:
        """The id of the charge's source (``None`` for ``manual``)."""
        needed = _CHARGE_SOURCES[self.kind]
        source: uuid.UUID | None = None if needed is None else getattr(self, needed)
        return source


@dataclass(frozen=True)
class PostedCharge:
    """The result of ``post_charge``: ``created = False`` means the natural
    key was already posted (the existing charge; nothing was written)."""

    charge_id: uuid.UUID
    billing_account_id: uuid.UUID
    created: bool


@dataclass(frozen=True, kw_only=True)
class AdjustmentDraft:
    """An adjustment to post with
    :func:`app.modules.billing.service.post_adjustment` (#90 C4). A credit
    is a negative ``amount``; ``amount`` is never zero. ``source_key`` is
    required for every kind but ``manual`` (its idempotency key, data-model
    §3.10); ``reason`` is required for ``manual``. Rule breaks raise
    :class:`ValueError` when the draft is built."""

    charge_id: uuid.UUID
    kind: AdjustmentKind
    amount: Money
    reason: str | None = None
    source_key: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ADJUSTMENT_KINDS:
            raise ValueError(f"unknown adjustment kind {self.kind!r}")
        if _require_money(self.amount, "amount").is_zero():
            raise ValueError("an adjustment amount is never zero")
        _check_staff_text(self.reason, "reason", REASON_MAX)
        if self.kind == MANUAL and self.reason is None:
            raise ValueError("a manual adjustment needs a reason")
        if self.kind != MANUAL and self.source_key is None:
            raise ValueError("a generated adjustment needs a source_key")
        if self.source_key is not None and not (
            1 <= len(self.source_key) <= SOURCE_KEY_MAX
        ):
            raise ValueError(f"source_key must have 1 to {SOURCE_KEY_MAX} characters")


@dataclass(frozen=True)
class PostedAdjustment:
    """The result of ``post_adjustment``. ``created = False``: the
    ``source_key`` was already posted (nothing was written, nothing
    released). ``released_allocation_ids``: the allocations the adjustment
    released, newest first; ``reinserted_allocation_id``: the smaller
    allocation re-inserted for the last one, if any (#90 E5)."""

    adjustment_id: uuid.UUID
    created: bool
    released_allocation_ids: tuple[uuid.UUID, ...] = ()
    reinserted_allocation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ChargeBalance:
    """One charge with its derived values (#90 D1; data-model §4.2).
    ``allocated`` counts valid allocations only; ``open = net −
    allocated``."""

    charge_id: uuid.UUID
    student_id: uuid.UUID
    kind: str
    service_date: date
    effective_due_date: date
    amount: Money
    adjustments_total: Money
    net: Money
    allocated: Money
    open: Money
    status: ChargeStatus


@dataclass(frozen=True)
class ChargeDetail:
    """A :class:`ChargeBalance` with the charge's stored fields, for the
    charge list (#364 B2). ``due_date`` is the effective due date;
    ``posted_by_user_id`` is ``None`` for a job-posted charge."""

    charge_id: uuid.UUID
    student_id: uuid.UUID
    kind: str
    service_date: date
    due_date: date
    description: str | None
    list_amount: Money
    discount_amount: Money
    amount: Money
    adjustments_total: Money
    net: Money
    allocated: Money
    open: Money
    status: ChargeStatus
    posted_at: datetime
    posted_by_user_id: uuid.UUID | None
    class_session_id: uuid.UUID | None
    group_enrollment_id: uuid.UUID | None
    cohort_enrollment_id: uuid.UUID | None


@dataclass(frozen=True)
class ChargeDetailPage:
    """One page of :class:`ChargeDetail` rows and the total that match."""

    items: tuple[ChargeDetail, ...]
    total: int


@dataclass(frozen=True)
class BillingAccountReference:
    """A family's billing account: its id and due-date offset (#364 B1)."""

    id: uuid.UUID
    family_id: uuid.UUID
    due_offset_days: int


@dataclass(frozen=True)
class StudentBalance:
    """One student's open amounts on an account (#90 D2). Account credit is
    account-level only."""

    student_id: uuid.UUID
    outstanding: Money
    pending: Money
    partially_paid: Money
    overdue: Money


@dataclass(frozen=True)
class AccountBalance:
    """An account's derived balance (#90 D2). ``outstanding = pending +
    partially_paid + overdue``; ``net_balance = outstanding −
    account_credit``; the students' ``outstanding`` add up to the
    account's."""

    billing_account_id: uuid.UUID
    outstanding: Money
    account_credit: Money
    net_balance: Money
    pending: Money
    partially_paid: Money
    overdue: Money
    by_student: tuple[StudentBalance, ...]


class LedgerEntry(BaseModel):
    """One row of the ``billing_ledger_entries`` view (#90 A6, D4).
    ``balance_effect`` is the row's effect on the net balance; ``kind`` is
    the charge or adjustment kind, or the payment or refund method."""

    model_config = ConfigDict(frozen=True)

    entry_type: LedgerEntryType
    entry_id: uuid.UUID
    billing_account_id: uuid.UUID
    kind: str | None
    student_id: uuid.UUID | None
    charge_id: uuid.UUID | None
    payment_id: uuid.UUID | None
    amount: Money
    balance_effect: Money
    occurred_at: datetime


# --- Billing settings and package coverage (#91) -------------------------------------


class BillingSettingsDetail(BaseModel):
    """``GET /api/v1/billing/settings`` (#91 B1, B2). ``billing_starts_on``
    is the billing start floor, a center-local date: ``null`` means billing
    is off (nothing is posted); otherwise charges are posted only for
    service dates on or after it."""

    billing_starts_on: date | None
    version: int
    updated_at: datetime


class BillingSettingsUpdate(BaseModel):
    """``PATCH /api/v1/billing/settings`` (#91 B3). Both keys are required;
    ``billing_starts_on: null`` turns billing off. Any date from 2000-01-01
    to 2099-12-31, past included (``date_out_of_range`` otherwise)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    billing_starts_on: date | None

    @field_validator("billing_starts_on")
    @classmethod
    def _date_range(cls, value: date | None) -> date | None:
        return None if value is None else _effective_from(value)


@dataclass(frozen=True)
class CoverageQuery:
    """One ``(class_session_id, student_id)`` asked of
    :func:`~app.modules.billing.service.package_covered_keys` (#91 G7):
    with what #94 needs to find an eligible package (the student, the
    subject, the session's local service date and its scheduled
    minutes)."""

    class_session_id: uuid.UUID
    student_id: uuid.UUID
    subject_id: uuid.UUID
    service_date: date
    scheduled_minutes: int


# --- Price calculation (#95 B2-B4) ---------------------------------------------------

type QuoteChargeKind = Literal[
    "session",
    "group_enrollment",
    "cohort_enrollment",
    "tuition_period",
    "package_purchase",
]
"""The charge kinds priced through ``quote`` (never ``manual``, #100)."""
type BasePriceSource = Literal["custom_student", "custom_family", "catalog"]


@dataclass(frozen=True, kw_only=True)
class PriceQuery:
    """What :func:`~app.modules.billing.service.quote` prices (#95 B2): the
    :func:`~app.modules.billing.service.lookup_price` arguments plus the
    student, the student's family (``None``: no family price, no family
    assignment, no sibling discount) and the charge kind. ``on`` is the
    local service date; ``education_level_id`` the student's level at
    posting."""

    student_id: uuid.UUID
    family_id: uuid.UUID | None
    charge_kind: QuoteChargeKind
    class_type: ClassType
    pricing_model: str
    subject_id: uuid.UUID
    on: date
    education_level_id: uuid.UUID | None = None
    duration_minutes: int | None = None
    cohort_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None


@dataclass(frozen=True)
class BasePrice:
    """The base price of a :class:`PriceQuery` (#95 B2, H6): the custom
    price that applies, else the catalog price. Exactly one of
    ``price_version_id`` and ``custom_price_id`` is set; ``rank`` is the
    #89 rank of the deciding item."""

    list_amount: Money
    price_version_id: uuid.UUID | None
    custom_price_id: uuid.UUID | None
    source: BasePriceSource
    rank: int

    @classmethod
    def of_catalog(cls, price: PriceInEffect) -> BasePrice:
        """The catalog price as a base price."""
        return cls(
            list_amount=price.amount,
            price_version_id=price.price_version_id,
            custom_price_id=None,
            source="catalog",
            rank=price.rank,
        )


@dataclass(frozen=True)
class Quote:
    """A priced :class:`PriceQuery` (#95 B4): the :class:`BasePrice` fields,
    the discount lines (H4), ``discount_amount`` (Σ lines) and ``amount =
    list_amount − discount_amount``."""

    list_amount: Money
    price_version_id: uuid.UUID | None
    custom_price_id: uuid.UUID | None
    source: BasePriceSource
    rank: int
    discounts: tuple[DiscountLine, ...]
    discount_amount: Money
    amount: Money


# --- Discounts and custom prices: request bodies (#95 D, E) --------------------------

PERCENT_PLACES: Final = 4
_PERCENT_PATTERN: Final = re.compile(r"^(0|[1-9][0-9]{0,2})(\.[0-9]+)?$")
_HUNDRED: Final = Decimal(100)

DiscountName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=DISCOUNT_NAME_MAX
    ),
]
"""A discount name: trimmed, 1 to 120 characters."""

RULE_MESSAGES: Final[dict[str, str]] = {
    "value_required": "Enter a percentage or an amount.",
    "value_conflict": "Enter a percentage or an amount, not both.",
    "audience_not_allowed": (
        "A scholarship is for assigned students or families; a sibling discount"
        " is for everyone."
    ),
    "dates_required": "A promotion has a start date and an end date.",
    "dates_order": "The end date can't be before the start date.",
    "min_family_students_required": (
        "A sibling discount needs the minimum number of students in the family."
    ),
    "min_family_students_not_allowed": (
        "Only a sibling discount has a minimum number of students in the family."
    ),
    "target_required": "Choose a student or a family.",
    "target_conflict": "Choose a student or a family, not both.",
    "field_immutable": "This field can't be changed.",
}


def _rule(code: str) -> PydanticCustomError:
    return PydanticCustomError(code, RULE_MESSAGES[code])


def percent_text(value: str) -> str:
    """A percentage string checked (``percent_invalid``,
    ``percent_precision``, ``percent_out_of_range``: more than 0, at most
    100) and returned with four places."""
    if not _PERCENT_PATTERN.match(value):
        raise PydanticCustomError(
            "percent_invalid", "Enter a percentage such as 10 or 12.5."
        )
    number = Decimal(value)
    exponent = number.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > PERCENT_PLACES:
        raise PydanticCustomError(
            "percent_precision", "Use at most four decimal places."
        )
    if not (Decimal(0) < number <= _HUNDRED):
        raise PydanticCustomError(
            "percent_out_of_range", "Choose more than 0 and at most 100."
        )
    return f"{number:.4f}"


def _amount_positive(value: Money | None) -> Money | None:
    if value is not None and not value.is_positive():
        raise PydanticCustomError(
            "amount_not_positive", "The amount must be more than 0."
        )
    return value


def audience_rule(kind: str, audience: str) -> str | None:
    """``audience_not_allowed``: a scholarship is ``assigned``, a sibling
    discount ``all`` (H2)."""
    if (kind == "scholarship" and audience != "assigned") or (
        kind == "sibling" and audience != "all"
    ):
        return "audience_not_allowed"
    return None


def value_rule(percent: object, amount: object) -> str | None:
    """Exactly one of the percentage and the amount."""
    if percent is None and amount is None:
        return "value_required"
    if percent is not None and amount is not None:
        return "value_conflict"
    return None


def dates_rule(
    kind: str | None, starts_on: date | None, expires_on: date | None
) -> str | None:
    """``dates_order`` and, for a promotion, ``dates_required``."""
    if starts_on is not None and expires_on is not None and starts_on > expires_on:
        return "dates_order"
    if kind == "promotion" and (starts_on is None or expires_on is None):
        return "dates_required"
    return None


def threshold_rule(kind: str, eligibility: DiscountEligibility) -> str | None:
    """``min_family_students`` is set for a ``sibling`` discount only."""
    present = eligibility.min_family_students is not None
    if kind == "sibling" and not present:
        return "min_family_students_required"
    if kind != "sibling" and present:
        return "min_family_students_not_allowed"
    return None


def discount_rule_errors(
    *,
    kind: str,
    audience: str,
    percent: object,
    amount: object,
    starts_on: date | None,
    expires_on: date | None,
    eligibility: DiscountEligibility,
) -> list[FieldError]:
    """The D3 cross-field rules for a whole discount row (the ``PATCH``
    result, D5), as field errors in one list."""
    found = [
        ("audience", audience_rule(kind, audience)),
        ("amount", value_rule(percent, amount)),
        ("expires_on", dates_rule(kind, starts_on, expires_on)),
        ("eligibility", threshold_rule(kind, eligibility)),
    ]
    return [
        {"path": ["body", field], "code": code, "message": RULE_MESSAGES[code]}
        for field, code in found
        if code is not None
    ]


def _optional_date(value: date | None) -> date | None:
    return None if value is None else _effective_from(value)


def _immutable(value: object) -> object:
    raise _rule("field_immutable")


class DiscountCreate(BaseModel):
    """``POST /api/v1/billing/discounts`` (#95 D3). Fields are in rule
    order: each cross-field rule is checked on the later field."""

    model_config = ConfigDict(extra="forbid")

    kind: DiscountKind
    audience: DiscountAudience
    name: DiscountName
    percent: str | None = None
    amount: Money | None = Field(default=None, validate_default=True)
    starts_on: date | None = None
    expires_on: date | None = Field(default=None, validate_default=True)
    eligibility: DiscountEligibility = Field(
        default_factory=DiscountEligibility, validate_default=True
    )
    is_active: bool = True

    @field_validator("audience")
    @classmethod
    def _audience(cls, value: str, info: ValidationInfo) -> str:
        kind = info.data.get("kind")
        if kind is not None and audience_rule(kind, value) is not None:
            raise _rule("audience_not_allowed")
        return value

    @field_validator("percent")
    @classmethod
    def _percent(cls, value: str | None) -> str | None:
        return None if value is None else percent_text(value)

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: Money | None, info: ValidationInfo) -> Money | None:
        _amount_positive(value)
        if "percent" in info.data:
            code = value_rule(info.data["percent"], value)
            if code is not None:
                raise _rule(code)
        return value

    @field_validator("starts_on")
    @classmethod
    def _starts(cls, value: date | None) -> date | None:
        return _optional_date(value)

    @field_validator("expires_on")
    @classmethod
    def _expires(cls, value: date | None, info: ValidationInfo) -> date | None:
        _optional_date(value)
        if "starts_on" in info.data:
            code = dates_rule(info.data.get("kind"), info.data["starts_on"], value)
            if code is not None:
                raise _rule(code)
        return value

    @field_validator("eligibility")
    @classmethod
    def _eligibility(
        cls, value: DiscountEligibility, info: ValidationInfo
    ) -> DiscountEligibility:
        kind = info.data.get("kind")
        if kind is not None:
            code = threshold_rule(kind, value)
            if code is not None:
                raise _rule(code)
        return value


class DiscountUpdate(BaseModel):
    """``PATCH /api/v1/billing/discounts/{id}`` (#95 D5): ``version`` and
    any of the editable fields. Switching between a percentage and an
    amount sends the other as ``null``; ``eligibility`` replaces the whole
    object. ``kind`` and ``audience`` never change (``field_immutable``).
    The resulting row is checked against the D3 rules by the service."""

    model_config = ConfigDict(extra="forbid")

    version: int
    name: DiscountName | None = None
    percent: str | None = None
    amount: Money | None = None
    eligibility: DiscountEligibility | None = None
    starts_on: date | None = None
    expires_on: date | None = None
    is_active: bool | None = None
    kind: SkipJsonSchema[Any] = None
    audience: SkipJsonSchema[Any] = None

    _immutable = field_validator("kind", "audience")(_immutable)

    @field_validator("name", "eligibility", "is_active")
    @classmethod
    def _not_null(cls, value: object) -> object:
        if value is None:
            raise PydanticCustomError("value_not_null", "This field can't be null.")
        return value

    @field_validator("percent")
    @classmethod
    def _percent(cls, value: str | None) -> str | None:
        return None if value is None else percent_text(value)

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: Money | None) -> Money | None:
        return _amount_positive(value)

    @field_validator("starts_on", "expires_on")
    @classmethod
    def _dates(cls, value: date | None) -> date | None:
        return _optional_date(value)


def _target_rule(student_id: object, family_id: object) -> None:
    if student_id is None and family_id is None:
        raise _rule("target_required")
    if student_id is not None and family_id is not None:
        raise _rule("target_conflict")


class DiscountAssignmentCreate(BaseModel):
    """``POST /api/v1/billing/discounts/{id}/assignments`` (#95 D6): exactly
    one of ``student_id`` and ``family_id``."""

    model_config = ConfigDict(extra="forbid")

    student_id: uuid.UUID | None = None
    family_id: uuid.UUID | None = Field(default=None, validate_default=True)

    @field_validator("family_id")
    @classmethod
    def _target(cls, value: uuid.UUID | None, info: ValidationInfo) -> uuid.UUID | None:
        if "student_id" in info.data:
            _target_rule(info.data["student_id"], value)
        return value


class CustomPriceCreate(BaseModel):
    """``POST /api/v1/billing/custom-prices`` (#95 E3): one catalog item,
    exactly one target, the dates (inclusive; ``expires_on`` optional) and
    the amount (not negative)."""

    model_config = ConfigDict(extra="forbid")

    service_price_id: uuid.UUID
    student_id: uuid.UUID | None = None
    family_id: uuid.UUID | None = Field(default=None, validate_default=True)
    effective_from: date
    expires_on: date | None = None
    amount: Money

    @field_validator("family_id")
    @classmethod
    def _target(cls, value: uuid.UUID | None, info: ValidationInfo) -> uuid.UUID | None:
        if "student_id" in info.data:
            _target_rule(info.data["student_id"], value)
        return value

    @field_validator("effective_from")
    @classmethod
    def _from(cls, value: date) -> date:
        return _effective_from(value)

    @field_validator("expires_on")
    @classmethod
    def _expires(cls, value: date | None, info: ValidationInfo) -> date | None:
        _optional_date(value)
        start = info.data.get("effective_from")
        if value is not None and start is not None and value < start:
            raise _rule("dates_order")
        return value

    @field_validator("amount")
    @classmethod
    def _amount_sign(cls, value: Money) -> Money:
        _not_negative(value)
        return value


class CustomPriceUpdate(BaseModel):
    """``PATCH /api/v1/billing/custom-prices/{id}`` (#95 E5): end (or
    re-open with ``null``) a custom price. ``expires_on`` is required; every
    other field is ``field_immutable`` (E7)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    expires_on: date | None
    service_price_id: SkipJsonSchema[Any] = None
    student_id: SkipJsonSchema[Any] = None
    family_id: SkipJsonSchema[Any] = None
    effective_from: SkipJsonSchema[Any] = None
    amount: SkipJsonSchema[Any] = None

    _immutable = field_validator(
        "service_price_id", "student_id", "family_id", "effective_from", "amount"
    )(_immutable)

    @field_validator("expires_on")
    @classmethod
    def _expires(cls, value: date | None) -> date | None:
        return _optional_date(value)
