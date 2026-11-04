"""Finance shapes (#114, #115): the category, vendor and expense bodies and
items.

A name is trimmed and must not be empty (``text_empty``), too long
(``text_too_long``) or hold a control character or a lone surrogate
(``text_invalid``), the pay item type codes. A vendor's ``contact`` follows the
same rules, except that it may span lines (tabs and line breaks pass). An
unknown field is a ``422``, ``system_key`` included: no route sets a key
(#114 E3). A ``PATCH`` must change something (``no_changes``).

Expense texts (#115) follow the same codes: ``description``,
``receipt_reference`` and a payment's ``reference`` are one line; ``note`` and
``reason`` may span lines. An optional text sent blank is ``null``. The
``POST`` answers hold ids and the derived status only (#115 E7): the stored
idempotent response is kept in clear.
"""

import unicodedata
import uuid
from datetime import date, datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.core.money import Money
from app.modules.administration.schemas import AccountName
from app.modules.finance.models import (
    CATEGORY_NAME_MAX,
    DESCRIPTION_MAX,
    METHOD_CODE_MAX,
    NOTE_MAX,
    REASON_MAX,
    REFERENCE_MAX,
    VENDOR_CONTACT_MAX,
    VENDOR_NAME_MAX,
)

type SystemKey = Literal["rent", "utilities", "supplies", "equipment", "maintenance"]

CONTACT_LAYOUT: Final = frozenset("\t\n\r")
"""Control characters a multi-line contact may hold."""


def _encodable(text: str) -> bool:
    """False for a lone surrogate, which JSON can carry and UTF-8 can't."""
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True


def _clean(value: str, maximum: int, allowed: frozenset[str] = frozenset()) -> str:
    text = value.strip()
    if not text:
        raise PydanticCustomError("text_empty", "This can't be empty.")
    if not _encodable(text) or any(
        unicodedata.category(char) == "Cc" and char not in allowed for char in text
    ):
        raise PydanticCustomError("text_invalid", "This has an invalid character.")
    if len(text) > maximum:
        raise PydanticCustomError(
            "text_too_long",
            "Use at most {maximum} characters.",
            {"maximum": maximum},
        )
    return text


def _category_name(value: str) -> str:
    return _clean(value, CATEGORY_NAME_MAX)


def _vendor_name(value: str) -> str:
    return _clean(value, VENDOR_NAME_MAX)


def _contact(value: str) -> str:
    return _clean(value, VENDOR_CONTACT_MAX, CONTACT_LAYOUT)


def _nothing_to_change() -> PydanticCustomError:
    return PydanticCustomError("no_changes", "Change at least one field.")


# --- Categories ----------------------------------------------------------------------


class ExpenseCategoryItem(BaseModel):
    """An expense category (#114 B1). While ``system_key`` is set, show the
    catalog label of the key in the viewer's language instead of ``name``."""

    id: uuid.UUID
    name: str
    system_key: SystemKey | None = Field(
        description="A seeded category's key, until its first rename (never set"
        " again). While set, the client shows its own label for the key."
    )
    is_active: bool
    version: int


class ExpenseCategoryCreate(BaseModel):
    """``POST /api/v1/finance/expense-categories`` (#114 B2)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="1-100 characters, trimmed. Unique ignoring case, also"
        " against a seeded category's Spanish and English label."
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _category_name(value)


class ExpenseCategoryUpdate(BaseModel):
    """``PATCH /api/v1/finance/expense-categories/{id}`` (#114 B3). A missing
    field stays as it is; at least one of ``name`` and ``is_active``."""

    model_config = ConfigDict(extra="forbid")

    version: int
    name: str | None = Field(
        default=None,
        description="A new name clears the seeded key for good (#114 E3).",
    )
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _category_name(value)

    @model_validator(mode="after")
    def _something_changes(self) -> Self:
        if self.name is None and self.is_active is None:
            raise _nothing_to_change()
        return self


# --- Vendors -------------------------------------------------------------------------


class VendorItem(BaseModel):
    """A vendor (#114 C1)."""

    id: uuid.UUID
    name: str
    contact: str | None
    is_active: bool
    version: int


class VendorCreate(BaseModel):
    """``POST /api/v1/finance/vendors`` (#114 C2)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="1-200 characters, trimmed; unique ignoring case.")
    contact: str | None = Field(
        default=None, description="Free text, 1-500 characters, trimmed."
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _vendor_name(value)

    @field_validator("contact")
    @classmethod
    def _contact(cls, value: str | None) -> str | None:
        return None if value is None else _contact(value)


class VendorUpdate(BaseModel):
    """``PATCH /api/v1/finance/vendors/{id}`` (#114 C3). A missing field stays
    as it is; ``contact: null`` clears the contact. At least one of ``name``,
    ``contact`` and ``is_active``."""

    model_config = ConfigDict(extra="forbid")

    version: int
    name: str | None = None
    contact: str | None = Field(
        default=None, description="`null` clears it; leaving it out keeps it."
    )
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _vendor_name(value)

    @field_validator("contact")
    @classmethod
    def _contact(cls, value: str | None) -> str | None:
        return None if value is None else _contact(value)

    @model_validator(mode="after")
    def _something_changes(self) -> Self:
        if (
            self.name is None
            and self.is_active is None
            and "contact" not in self.model_fields_set
        ):
            raise _nothing_to_change()
        return self


# --- Expenses (#115) -----------------------------------------------------------------

type ExpenseStatus = Literal["unpaid", "partially_paid", "paid", "overdue", "void"]
"""Derived, never stored (data-model §13.2), with the center's today."""

type ExpenseSort = Literal["-incurred_on", "due_on"]


def _optional_line(value: str | None, maximum: int) -> str | None:
    """Blank is ``null``."""
    if value is None or not value.strip():
        return None
    return _clean(value, maximum)


def _optional_text(value: str | None, maximum: int) -> str | None:
    """May span lines; blank is ``null``."""
    if value is None or not value.strip():
        return None
    return _clean(value, maximum, CONTACT_LAYOUT)


def _positive(value: Money) -> Money:
    if not value.is_positive():
        raise PydanticCustomError(
            "amount_not_positive", "The amount must be above zero."
        )
    return value


class ExpenseCreate(BaseModel):
    """``POST /api/v1/finance/expenses`` (#115 B2). The category and vendor
    must exist and be active; ``incurred_on`` not after the center's today,
    ``due_on`` not before ``incurred_on`` (#115 E2)."""

    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID
    vendor_id: uuid.UUID | None = None
    description: str = Field(description="1-500 characters, trimmed; one line.")
    amount: Money = Field(description="Above zero.")
    incurred_on: date = Field(description="Up to the center's today.")
    due_on: date = Field(
        description="On or after `incurred_on`; a past date is allowed (overdue)."
    )
    receipt_reference: str | None = Field(
        default=None,
        description="The vendor's invoice or receipt number, 1-100 characters;"
        " blank is null.",
    )
    note: str | None = Field(
        default=None, description="Up to 1000 characters; blank is null."
    )

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return _clean(value, DESCRIPTION_MAX)

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: Money) -> Money:
        return _positive(value)

    @field_validator("receipt_reference")
    @classmethod
    def _receipt_reference(cls, value: str | None) -> str | None:
        return _optional_line(value, REFERENCE_MAX)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _optional_text(value, NOTE_MAX)


class ExpenseCreated(BaseModel):
    """What ``POST /expenses`` answers (#115 B5, E7): no amount or text."""

    id: uuid.UUID
    status: ExpenseStatus


class ExpenseCategoryRef(BaseModel):
    """An expense's category, inactive ones included (#114 MUST). While
    ``system_key`` is set, show the key's label in the viewer's language."""

    id: uuid.UUID
    name: str
    system_key: SystemKey | None
    is_active: bool


class VendorRef(BaseModel):
    """An expense's vendor, inactive ones included."""

    id: uuid.UUID
    name: str
    is_active: bool


class ExpenseItem(BaseModel):
    """One expense of the list (#115 C1), with its derived figures."""

    id: uuid.UUID
    category: ExpenseCategoryRef
    vendor: VendorRef | None
    description: str
    amount: Money = Field(description="As entered.")
    adjusted_amount: Money = Field(
        description="The net: the amount plus every adjustment."
    )
    paid_amount: Money = Field(description="The payments without a reversal.")
    open_amount: Money = Field(description="adjusted_amount - paid_amount.")
    status: ExpenseStatus
    incurred_on: date
    due_on: date
    receipt_reference: str | None
    created_at: datetime


class ExpensePaymentReversalItem(BaseModel):
    """A payment's reversal (#115 C3)."""

    reason: str
    reversed_by: AccountName
    created_at: datetime


class ExpensePaymentItem(BaseModel):
    """One payment of an expense, reversed ones included (#115 C3)."""

    id: uuid.UUID
    paid_on: date
    amount: Money
    method_code: str
    reference: str | None
    note: str | None
    recorded_by: AccountName
    created_at: datetime
    reversal: ExpensePaymentReversalItem | None


class ExpenseAdjustmentItem(BaseModel):
    """One adjustment of an expense (#115 C3)."""

    id: uuid.UUID
    amount: Money
    reason: str
    created_by: AccountName
    created_at: datetime


class ExpenseDetail(ExpenseItem):
    """``GET /expenses/{id}`` (#115 C3): the item, the note, who entered it
    (``null`` for the recurring job), the payments (newest ``paid_on`` first)
    and the adjustments (oldest first)."""

    note: str | None
    created_by: AccountName | None
    payments: list[ExpensePaymentItem]
    adjustments: list[ExpenseAdjustmentItem]


class ExpensePaymentCreate(BaseModel):
    """``POST /api/v1/finance/expenses/{id}/payments`` (#115 D2)."""

    model_config = ConfigDict(extra="forbid")

    paid_on: date = Field(
        description="Up to the center's today; before the expense date is fine."
    )
    amount: Money = Field(description="Above zero, up to the open amount.")
    method_code: str = Field(
        min_length=1,
        max_length=METHOD_CODE_MAX,
        description="An active payment method's code.",
    )
    reference: str | None = Field(
        default=None,
        description="Up to 100 characters; blank is null. With the method, it"
        " identifies the payment: the same pair twice on an expense is refused.",
    )
    note: str | None = Field(
        default=None, description="Up to 1000 characters; blank is null."
    )

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: Money) -> Money:
        return _positive(value)

    @field_validator("reference")
    @classmethod
    def _reference(cls, value: str | None) -> str | None:
        return _optional_line(value, REFERENCE_MAX)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _optional_text(value, NOTE_MAX)


class ExpensePaymentRecorded(BaseModel):
    """What ``POST …/payments`` answers (#115 D5, E7)."""

    id: uuid.UUID
    expense_id: uuid.UUID
    status: ExpenseStatus


class ExpensePaymentReversalCreate(BaseModel):
    """``POST /api/v1/finance/expense-payments/{id}/reversal`` (#115 E2)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(description="1-1000 characters, trimmed.")

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean(value, REASON_MAX, CONTACT_LAYOUT)


class ExpensePaymentReversed(BaseModel):
    """What ``POST …/reversal`` answers (#115 E2, E7)."""

    id: uuid.UUID
    expense_payment_id: uuid.UUID
    status: ExpenseStatus


class ExpenseAdjustmentCreate(BaseModel):
    """``POST /api/v1/finance/expenses/{id}/adjustments`` (#115 F2)."""

    model_config = ConfigDict(extra="forbid")

    amount: Money = Field(
        description="Signed, never 0.00. Exactly minus the net voids the expense."
    )
    reason: str = Field(description="1-1000 characters, trimmed.")

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: Money) -> Money:
        if value.is_zero():
            raise PydanticCustomError("amount_zero", "The amount can't be zero.")
        return value

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean(value, REASON_MAX, CONTACT_LAYOUT)


class ExpenseAdjustmentPosted(BaseModel):
    """What ``POST …/adjustments`` answers (#115 F4, E7)."""

    id: uuid.UUID
    expense_id: uuid.UUID
    status: ExpenseStatus
