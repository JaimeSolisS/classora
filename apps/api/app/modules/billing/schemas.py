"""Billing shapes: the price catalog (#89 B1, B3, B5, C1) and the ledger
core's drafts and results (#90 C3, C4, D1-D4; at the end of this module).

The request bodies check everything that needs no database read, and
report every failing field in one ``422`` (each rule is a field validator
on the later field of the pair it compares, so the earlier fields are in
``info.data``):

- ``pricing_model``: ``per_session``, ``full_course`` or (since #93 B1)
  ``per_period`` (``package`` is #94's), and allowed for the class type
  (``pricing_model_not_allowed``);
- ``period_frequency`` (#93 K2): required with ``per_period``
  (``period_frequency_required``), refused otherwise
  (``period_frequency_not_allowed``);
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
    AfterValidator,
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
    NOTE_MAX,
    PAYER_NAME_MAX,
    PAYMENT_METHOD_CODE_MAX,
    REASON_MAX,
    REFERENCE_MAX,
    SCHEDULED_MINUTES_MAX,
    SCHEDULED_MINUTES_MIN,
    SOURCE_KEY_MAX,
)

type ClassType = Literal["individual", "group", "course"]
type CatalogPricingModel = Literal["per_session", "full_course", "per_period"]
"""The pricing models the #89 routes accept (E5; ``per_period`` since #93)."""
type PeriodFrequency = Literal["weekly", "biweekly", "monthly"]
"""The period length of a ``per_period`` item and of a tuition plan (#93
K2, K3)."""
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
    period_frequency: PeriodFrequency | None = Field(
        default=None, validate_default=True
    )
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
        model = info.data.get("pricing_model")
        if (model is not None and model != "per_session") or _scoped(info):
            raise PydanticCustomError(
                "duration_not_allowed",
                "Only a per-session price outside a cohort or group has a duration.",
            )
        if not DURATION_MIN <= value <= DURATION_MAX:
            raise PydanticCustomError(
                "duration_out_of_range", "Choose 15 to 480 minutes."
            )
        return value

    @field_validator("period_frequency")
    @classmethod
    def _frequency(cls, value: str | None, info: ValidationInfo) -> str | None:
        model = info.data.get("pricing_model")
        if model is None:
            return value
        if model == "per_period" and value is None:
            raise PydanticCustomError(
                "period_frequency_required",
                "Choose weekly, every two weeks or monthly.",
            )
        if model != "per_period" and value is not None:
            raise PydanticCustomError(
                "period_frequency_not_allowed",
                "Only a per-period price has a period length.",
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

type ChargeKind = Literal[
    "session",
    "group_enrollment",
    "cohort_enrollment",
    "tuition_period",
    "package_purchase",
    "manual",
]
"""The charge kinds of #90 (E3), ``tuition_period`` (#93) and
``package_purchase`` (#94)."""
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

PRORATION_MAX: Final = 32767
"""The ``SMALLINT`` limit of the proration columns."""

_CHARGE_SOURCES: Final[dict[str, str | None]] = {
    "session": "class_session_id",
    "group_enrollment": "group_enrollment_id",
    "cohort_enrollment": "cohort_enrollment_id",
    "tuition_period": "tuition_plan_id",
    "package_purchase": "package_id",
    MANUAL: None,
}
"""The source column each charge kind needs (``None``: no source)."""
_SOURCE_COLUMNS: Final = (
    "class_session_id",
    "group_enrollment_id",
    "cohort_enrollment_id",
    "tuition_plan_id",
    "package_id",
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
    ``scheduled_minutes`` only and always for ``session``;
    ``service_period_end`` (on or after ``service_date``) only and always
    for ``tuition_period`` (#93 A5); ``description`` only and always for
    ``manual``. ``amount = list_amount −
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
    tuition_plan_id: uuid.UUID | None = None
    package_id: uuid.UUID | None = None
    """The source of a ``package_purchase`` charge (#94 A5)."""
    service_period_end: date | None = None
    scheduled_minutes: int | None = None
    price_version_id: uuid.UUID | None = None
    custom_price_id: uuid.UUID | None = None
    education_level_id: uuid.UUID | None = None
    discount_amount: Money = Money.zero()
    discounts: tuple[DiscountLine, ...] = ()
    apply_family_offset: bool = True
    description: str | None = None
    proration: tuple[int, int] | None = None
    """``(remaining, total)`` of a prorated enrollment fee (#92 J3, J8):
    ``group_enrollment`` and ``cohort_enrollment`` only, ``1 <= total`` and
    ``0 <= remaining <= total``; ``None`` for every other charge and for an
    open-ended group fee."""

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
        for column in _SOURCE_COLUMNS:
            if (getattr(self, column) is not None) != (column == needed):
                raise ValueError(f"a {self.kind} charge needs exactly its source")
        if (self.service_period_end is not None) != (self.kind == "tuition_period"):
            raise ValueError("service_period_end is set for a tuition charge only")
        if (
            self.service_period_end is not None
            and self.service_period_end < self.service_date
        ):
            raise ValueError("service_period_end can't be before service_date")
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
        self._check_proration()

    def _check_proration(self) -> None:
        """#92 A2: a proration only on an enrollment fee, in range (the
        ``ck_charges_proration`` CHECK is the backstop)."""
        if self.proration is None:
            return
        if self.kind not in ("group_enrollment", "cohort_enrollment"):
            raise ValueError("a proration is set for an enrollment fee only")
        remaining, total = self.proration
        if not (
            isinstance(remaining, int)
            and isinstance(total, int)
            and 1 <= total <= PRORATION_MAX
            and 0 <= remaining <= total
        ):
            raise ValueError("the proration is (remaining, total), 0 <= r <= t, t >= 1")

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
    allocated``. ``posted_at`` breaks ties in the #96 oldest-first
    allocation order (M2)."""

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
    posted_at: datetime


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
    :func:`~app.modules.billing.service.package_covered_keys` (#91 G7, #94
    D1): what an eligible package is matched against (the student, the
    subject, the session's local service date and its scheduled minutes),
    the session's class type (only ``individual`` sessions are ever
    covered) and whether its closeout has an approval (an approved session
    is covered only once consumed, #94 L3)."""

    class_session_id: uuid.UUID
    student_id: uuid.UUID
    subject_id: uuid.UUID
    service_date: date
    scheduled_minutes: int
    class_type: str = "individual"
    closeout_approved: bool = False


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
    posting; ``service_price_id`` an optional item pin (#93)."""

    student_id: uuid.UUID
    family_id: uuid.UUID | None
    charge_kind: QuoteChargeKind
    class_type: ClassType
    pricing_model: str
    subject_id: uuid.UUID | None
    """``None`` only for a pinned ``package`` item without a subject (#94:
    an any-subject product; a discount limited to subjects never matches
    it)."""
    on: date
    education_level_id: uuid.UUID | None = None
    duration_minutes: int | None = None
    cohort_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    service_price_id: uuid.UUID | None = None
    """The pin (#93 B4): price exactly this catalog item (its custom prices,
    then its version in effect on ``on``; no rank search). Required for a
    ``per_period`` query (a tuition plan's item); ``None`` = the #89/#95
    precedence."""


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


# --- Payments and allocations (#96 B, C; M3-M5) --------------------------------------

type AllocationMode = Literal["oldest_first", "explicit", "none"]
"""How a payment is allocated (#96 M3): the oldest open charges first
(M2), the charges and amounts the staff member chose, or not at all (all
account credit)."""
type CreditAllocationMode = Literal["oldest_first", "explicit"]
"""How account credit is allocated later (#96 M4; no ``none``)."""

ALLOCATION_LINES_MAX: Final = 200
RECEIVED_ON_MIN: Final = date(2000, 1, 1)

PAYMENT_RULE_MESSAGES: Final[dict[str, str]] = {
    "allocation_lines_required": "Choose at least one charge.",
    "allocation_lines_not_allowed": (
        "Charges are chosen only for an explicit allocation."
    ),
    "allocation_duplicate_charge": "Each charge can appear only once.",
    "allocation_exceeds_payment": "The allocations add up to more than the payment.",
    "amount_not_allowed": "An amount is given only for an oldest-first allocation.",
}


def _payment_rule(code: str) -> PydanticCustomError:
    return PydanticCustomError(code, PAYMENT_RULE_MESSAGES[code])


def _blank_to_none(value: str | None) -> str | None:
    return value or None


ReferenceText = Annotated[
    Annotated[str, StringConstraints(strip_whitespace=True, max_length=REFERENCE_MAX)]
    | None,
    AfterValidator(_blank_to_none),
]
"""Optional staff text (#96 M5): trimmed, at most 100 characters, ``null``
when blank after trimming, so the ``staff_text_check`` CHECKs never fire.
:data:`PayerNameText` (200) and :data:`NoteText` (500) likewise."""
PayerNameText = Annotated[
    Annotated[str, StringConstraints(strip_whitespace=True, max_length=PAYER_NAME_MAX)]
    | None,
    AfterValidator(_blank_to_none),
]
NoteText = Annotated[
    Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTE_MAX)]
    | None,
    AfterValidator(_blank_to_none),
]


def _received_on(value: date) -> date:
    if value < RECEIVED_ON_MIN:
        raise PydanticCustomError(
            "date_out_of_range", "Choose a date from 2000-01-01 on."
        )
    return value


class AllocationLineBody(BaseModel):
    """One explicit allocation line: a charge of the account and an amount
    above zero (``amount_not_positive``)."""

    model_config = ConfigDict(extra="forbid")

    charge_id: uuid.UUID
    amount: Money

    _positive = field_validator("amount")(_amount_positive)


def _lines_rule(
    value: list[AllocationLineBody] | None, info: ValidationInfo
) -> list[AllocationLineBody] | None:
    mode = info.data.get("mode")
    if mode is None:
        return value
    if mode == "explicit" and not value:
        raise _payment_rule("allocation_lines_required")
    if mode != "explicit" and value is not None:
        raise _payment_rule("allocation_lines_not_allowed")
    if value is not None and len({line.charge_id for line in value}) != len(value):
        raise _payment_rule("allocation_duplicate_charge")
    return value


class PaymentAllocationBody(BaseModel):
    """How to allocate a payment (#96 M3). ``lines`` (1 to 200, distinct
    charges) only and always with ``explicit``."""

    model_config = ConfigDict(extra="forbid")

    mode: AllocationMode
    lines: Annotated[
        list[AllocationLineBody] | None,
        Field(max_length=ALLOCATION_LINES_MAX, validate_default=True),
    ] = None

    _lines = field_validator("lines")(_lines_rule)


class CreditAllocationBody(BaseModel):
    """How to allocate account credit (#96 M4): ``oldest_first`` or
    ``explicit`` (``lines`` as in :class:`PaymentAllocationBody`)."""

    model_config = ConfigDict(extra="forbid")

    mode: CreditAllocationMode
    lines: Annotated[
        list[AllocationLineBody] | None,
        Field(max_length=ALLOCATION_LINES_MAX, validate_default=True),
    ] = None

    _lines = field_validator("lines")(_lines_rule)


def _lines_total(lines: list[AllocationLineBody] | None) -> Decimal:
    # Decimal, not Money: 200 lines near the NUMERIC(14,2) limit may add up
    # to more than Money can hold.
    return sum((line.amount.amount for line in lines or ()), Decimal(0))


class PaymentCreate(BaseModel):
    """``POST /api/v1/families/{family_id}/billing-account/payments`` (#96
    C1, M5). ``amount`` above zero (``amount_not_positive``; ``money_*``);
    ``received_on`` from 2000-01-01 (``date_out_of_range``; a date after
    the center's today is the route's ``received_on_in_future``);
    ``reference`` (≤ 100), ``payer_name`` (≤ 200) and ``note`` (≤ 500)
    trimmed, blank → ``null``. Explicit lines may not add up to more than
    the payment (``allocation_exceeds_payment``)."""

    model_config = ConfigDict(extra="forbid")

    amount: Money
    received_on: date
    payment_method_code: Annotated[
        str, StringConstraints(min_length=1, max_length=PAYMENT_METHOD_CODE_MAX)
    ]
    reference: ReferenceText = None
    payer_name: PayerNameText = None
    note: NoteText = None
    allocation: PaymentAllocationBody

    _positive = field_validator("amount")(_amount_positive)
    _date = field_validator("received_on")(_received_on)

    @field_validator("allocation")
    @classmethod
    def _within_payment(
        cls, value: PaymentAllocationBody, info: ValidationInfo
    ) -> PaymentAllocationBody:
        amount = info.data.get("amount")
        if amount is not None and _lines_total(value.lines) > amount.amount:
            raise _payment_rule("allocation_exceeds_payment")
        return value


class CreditAllocationCreate(BaseModel):
    """``POST /api/v1/families/{family_id}/billing-account/allocations``
    (#96 C4, M4). ``amount`` (above zero) only with ``oldest_first``
    (``amount_not_allowed``); without it the whole account credit is
    allocated."""

    model_config = ConfigDict(extra="forbid")

    allocation: CreditAllocationBody
    amount: Money | None = None

    @field_validator("amount")
    @classmethod
    def _amount_rule(cls, value: Money | None, info: ValidationInfo) -> Money | None:
        _amount_positive(value)
        allocation = info.data.get("allocation")
        if (
            value is not None
            and allocation is not None
            and allocation.mode != "oldest_first"
        ):
            raise _payment_rule("amount_not_allowed")
        return value


@dataclass(frozen=True)
class AllocationLine:
    """One explicit allocation line (#96 M3)."""

    charge_id: uuid.UUID
    amount: Money


def _check_lines(mode: str, lines: tuple[AllocationLine, ...]) -> None:
    if mode == "explicit":
        if not 1 <= len(lines) <= ALLOCATION_LINES_MAX:
            raise ValueError(f"explicit needs 1 to {ALLOCATION_LINES_MAX} lines")
    elif lines:
        raise ValueError("lines only with explicit")
    if len({line.charge_id for line in lines}) != len(lines):
        raise ValueError("allocation lines name distinct charges")
    if any(not _require_money(line.amount, "amount").is_positive() for line in lines):
        raise ValueError("an allocation line is positive")


@dataclass(frozen=True, kw_only=True)
class AllocationRequest:
    """How to allocate (#96 M3, M4). Rule breaks raise :class:`ValueError`
    when built (the request bodies refuse them first)."""

    mode: AllocationMode
    lines: tuple[AllocationLine, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in ("oldest_first", "explicit", "none"):
            raise ValueError(f"unknown allocation mode {self.mode!r}")
        _check_lines(self.mode, tuple(self.lines))

    @property
    def total(self) -> Money:
        """Σ explicit lines (zero for the other modes)."""
        return Money.total(line.amount for line in self.lines)

    @classmethod
    def of(
        cls, body: PaymentAllocationBody | CreditAllocationBody
    ) -> AllocationRequest:
        """The request of a validated body."""
        return cls(
            mode=body.mode,
            lines=tuple(
                AllocationLine(charge_id=line.charge_id, amount=line.amount)
                for line in body.lines or ()
            ),
        )


@dataclass(frozen=True, kw_only=True)
class PaymentDraft:
    """A payment to record with
    :func:`app.modules.billing.service.record_payment` (#96 B1). ``amount``
    above zero; text fields ``None`` or trimmed staff text; explicit lines
    within the amount. Rule breaks raise :class:`ValueError` when built."""

    billing_account_id: uuid.UUID
    amount: Money
    received_on: date
    payment_method_code: str
    reference: str | None = None
    payer_name: str | None = None
    note: str | None = None
    allocation: AllocationRequest

    def __post_init__(self) -> None:
        if not _require_money(self.amount, "amount").is_positive():
            raise ValueError("a payment amount is above zero")
        _check_staff_text(self.reference, "reference", REFERENCE_MAX)
        _check_staff_text(self.payer_name, "payer_name", PAYER_NAME_MAX)
        _check_staff_text(self.note, "note", NOTE_MAX)
        if self.allocation.total > self.amount:
            raise ValueError("the allocation lines add up to more than the payment")


@dataclass(frozen=True, kw_only=True)
class CreditAllocationRequest:
    """An allocation of account credit for
    :func:`app.modules.billing.service.allocate_account_credit` (#96 B2,
    M4): ``oldest_first`` (``amount``: at most this much; ``None`` = the
    whole credit) or ``explicit``."""

    billing_account_id: uuid.UUID
    allocation: AllocationRequest
    amount: Money | None = None

    def __post_init__(self) -> None:
        if self.allocation.mode == "none":
            raise ValueError("a credit allocation is oldest_first or explicit")
        if self.amount is not None:
            if not _require_money(self.amount, "amount").is_positive():
                raise ValueError("a credit allocation amount is above zero")
            if self.allocation.mode != "oldest_first":
                raise ValueError("amount only with oldest_first")


@dataclass(frozen=True)
class PlannedAllocation:
    """One line of an allocation plan (:func:`plan_allocations`)."""

    charge_id: uuid.UUID
    amount: Money


@dataclass(frozen=True)
class RecordedAllocation:
    """A ``payment_allocations`` row written by #96."""

    allocation_id: uuid.UUID
    payment_id: uuid.UUID
    charge_id: uuid.UUID
    student_id: uuid.UUID
    amount: Money


@dataclass(frozen=True)
class RecordedPayment:
    """The result of ``record_payment`` (#96 B1): the payment, its
    allocations (in plan order), the part left as account credit and the
    account credit afterwards."""

    payment_id: uuid.UUID
    allocations: tuple[RecordedAllocation, ...]
    unallocated: Money
    account_credit_after: Money


@dataclass(frozen=True)
class CreditAllocation:
    """The result of ``allocate_account_credit`` (#96 B2): the rows written
    (one target line may become several, one per source payment), their
    total and the account credit afterwards."""

    allocations: tuple[RecordedAllocation, ...]
    allocated_total: Money
    account_credit_after: Money


@dataclass(frozen=True)
class PaymentMethodRow:
    """A ``payment_methods`` row (#96 B6)."""

    code: str
    is_active: bool


@dataclass(frozen=True)
class PaymentRow:
    """A payment with its derived amounts (#96 C6). ``unallocated_amount =
    amount − allocated_amount − refunded_amount``; it is account credit
    only while ``is_reversed`` is false. ``recorded_at`` is the row's
    ``created_at``."""

    id: uuid.UUID
    amount: Money
    received_on: date
    payment_method_code: str
    reference: str | None
    payer_name: str | None
    note: str | None
    recorded_by_user_id: uuid.UUID
    recorded_at: datetime
    allocated_amount: Money
    refunded_amount: Money
    unallocated_amount: Money
    is_reversed: bool


@dataclass(frozen=True)
class PaymentPage:
    """One page of :class:`PaymentRow` and the account's payment total."""

    items: tuple[PaymentRow, ...]
    total: int


@dataclass(frozen=True)
class PaymentAllocationRow:
    """One allocation of a payment, valid or released (#96 C6)."""

    id: uuid.UUID
    charge_id: uuid.UUID
    student_id: uuid.UUID
    amount: Money
    created_at: datetime
    created_by_user_id: uuid.UUID | None
    is_valid: bool
