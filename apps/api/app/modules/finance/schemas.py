"""Finance shapes (#114): the category and vendor bodies and items.

A name is trimmed and must not be empty (``text_empty``), too long
(``text_too_long``) or hold a control character or a lone surrogate
(``text_invalid``), the pay item type codes. A vendor's ``contact`` follows the
same rules, except that it may span lines (tabs and line breaks pass). An
unknown field is a ``422``, ``system_key`` included: no route sets a key
(#114 E3). A ``PATCH`` must change something (``no_changes``).
"""

import unicodedata
import uuid
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.modules.finance.models import (
    CATEGORY_NAME_MAX,
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
