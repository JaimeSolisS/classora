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

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from app.core.money import Money
from app.modules.billing.models import (
    ADJUSTMENT_KINDS,
    ALLOWED_MODELS,
    DESCRIPTION_MAX,
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
    education_level_id: uuid.UUID | None = None
    discount_amount: Money = Money.zero()
    apply_family_offset: bool = True
    description: str | None = None

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
