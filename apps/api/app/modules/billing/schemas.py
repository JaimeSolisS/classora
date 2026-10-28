"""Price catalog shapes (#89 B1, B3, B5, C1).

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
from app.modules.billing.models import ALLOWED_MODELS, DURATION_MAX, DURATION_MIN

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
