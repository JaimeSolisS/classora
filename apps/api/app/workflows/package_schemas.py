"""Package product and package shapes (#94 B, C, D5; ADR-0006 E10).

The request bodies check everything that needs no database read and report
every failing field in one ``422`` (each rule is a field validator on the
later field of the pair it compares):

- ``name``: trimmed, 1 to 120 characters;
- ``unit``: ``sessions`` or ``minutes``;
- ``quantity`` (#94 B2): 1-500 sessions, or 15-30000 minutes in steps of 15
  (``quantity_out_of_range``, ``quantity_step``);
- ``validity_days``: 1-730 (``validity_days_out_of_range``);
- ``amount``: a :class:`~app.core.money.Money` string (``money_*``), not
  negative (``amount_negative``), required and not ``null`` on create;
- ``effective_from``: 2000-01-01 to 2099-12-31 (``date_out_of_range``).

``PATCH`` accepts only ``version``, ``name`` and ``is_active``: ``unit``,
``quantity``, ``validity_days`` and ``subject_id`` are fixed after creation
(any other key is ``422 validation_error``, ``extra_forbidden``; #94 L7).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationInfo,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.core.money import Money
from app.modules.billing.schemas import PriceVersionItem
from app.modules.billing.service import PackageStatus, PackageUnit

SESSIONS_MIN: Final = 1
SESSIONS_MAX: Final = 500
MINUTES_MIN: Final = 15
MINUTES_MAX: Final = 30000
MINUTES_STEP: Final = 15
VALIDITY_MIN: Final = 1
VALIDITY_MAX: Final = 730
EFFECTIVE_FROM_MIN: Final = date(2000, 1, 1)
EFFECTIVE_FROM_MAX: Final = date(2099, 12, 31)

type PackageTransactionKind = Literal["grant", "consume", "expire", "reversal"]
"""The unit movements of a package (#94 A4; ``refund`` arrives with #101)."""

PackageProductName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
"""A product name: trimmed, 1 to 120 characters."""


def _amount(value: Money) -> Money:
    if value.is_negative():
        raise PydanticCustomError("amount_negative", "The amount can't be negative.")
    return value


class PackageProductCreate(BaseModel):
    """``POST /api/v1/billing/package-products`` (#94 B1, B2): the product
    and its first price version. Fields are in rule order."""

    model_config = ConfigDict(extra="forbid")

    name: PackageProductName
    unit: PackageUnit
    quantity: int
    validity_days: int
    subject_id: uuid.UUID | None = None
    amount: Money
    effective_from: date

    @field_validator("quantity")
    @classmethod
    def _quantity(cls, value: int, info: ValidationInfo) -> int:
        unit = info.data.get("unit")
        if unit == "sessions" and not SESSIONS_MIN <= value <= SESSIONS_MAX:
            raise PydanticCustomError(
                "quantity_out_of_range", "Choose 1 to 500 sessions."
            )
        if unit == "minutes":
            if not MINUTES_MIN <= value <= MINUTES_MAX:
                raise PydanticCustomError(
                    "quantity_out_of_range", "Choose 15 to 30000 minutes."
                )
            if value % MINUTES_STEP:
                raise PydanticCustomError(
                    "quantity_step", "Choose a number of minutes in steps of 15."
                )
        return value

    @field_validator("validity_days")
    @classmethod
    def _validity(cls, value: int) -> int:
        if not VALIDITY_MIN <= value <= VALIDITY_MAX:
            raise PydanticCustomError(
                "validity_days_out_of_range", "Choose 1 to 730 days."
            )
        return value

    @field_validator("amount")
    @classmethod
    def _amount_sign(cls, value: Money) -> Money:
        return _amount(value)

    @field_validator("effective_from")
    @classmethod
    def _date_range(cls, value: date) -> date:
        if not EFFECTIVE_FROM_MIN <= value <= EFFECTIVE_FROM_MAX:
            raise PydanticCustomError(
                "date_out_of_range", "Choose a date from 2000-01-01 to 2099-12-31."
            )
        return value


class PackageProductUpdate(BaseModel):
    """``PATCH /api/v1/billing/package-products/{id}`` (#94 B1): ``version``
    and the fields to change (absent or ``null`` = unchanged)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    name: PackageProductName | None = None
    is_active: bool | None = None


class PackagePurchase(BaseModel):
    """``POST /api/v1/billing/packages`` (#94 C1)."""

    model_config = ConfigDict(extra="forbid")

    student_id: uuid.UUID
    package_product_id: uuid.UUID


class PackageReference(BaseModel):
    """A record a package or product points at: its id and current name."""

    id: uuid.UUID
    name: str


class PackageProductItem(BaseModel):
    """One package product (#94 B1). ``subject: null`` = any subject;
    ``current_amount`` is the price in effect today (``null``: none, or not
    offered)."""

    id: uuid.UUID
    name: str
    unit: PackageUnit
    quantity: int
    validity_days: int
    subject: PackageReference | None
    is_active: bool
    current_amount: Money | None
    version: int


class PackageProductDetail(PackageProductItem):
    """A product with its price versions, newest ``effective_from`` first
    (the #89 version shape)."""

    versions: list[PriceVersionItem]


class PackageItem(BaseModel):
    """One sold package (#94 D5). ``status`` is for the center's today:
    ``expired`` (expired, or past ``expires_on``) → ``exhausted`` (no units
    left) → ``active``."""

    id: uuid.UUID
    student: PackageReference
    family: PackageReference
    product: PackageReference
    unit: PackageUnit
    units_granted: int
    remaining_units: int
    subject: PackageReference | None
    purchased_on: date
    expires_on: date
    status: PackageStatus
    purchase_charge_id: uuid.UUID | None


class PackageTransactionItem(BaseModel):
    """One unit movement (signed, in the package's unit). A ``consume``
    names its session and the session's start."""

    id: uuid.UUID
    kind: PackageTransactionKind
    units: int
    class_session_id: uuid.UUID | None
    session_starts_at: datetime | None
    created_at: datetime


class PackageDetail(PackageItem):
    """A package with its unit movements, oldest first."""

    transactions: list[PackageTransactionItem]
