"""Payroll shapes (#105): the compensation bodies, their items and the lookup
results.

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
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from app.core.money import Money, Rate

type PayFrequency = Literal["weekly", "biweekly", "monthly"]
type ClassKind = Literal["individual", "group", "course"]
type RateModel = Literal["per_hour", "per_session", "per_course"]
type CompensationStatus = Literal["current", "upcoming", "past"]


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
