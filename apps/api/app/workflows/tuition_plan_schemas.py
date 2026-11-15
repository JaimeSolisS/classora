"""Tuition plan shapes (#93 D1, D3, D5, D7-D9, G1): the request bodies of
``app.workflows.tuition_plans_router`` and the responses of it and of the
unbilled tuition read."""

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.core.money import Money
from app.modules.billing.models import DUE_OFFSET_MAX
from app.modules.billing.schemas import (
    EFFECTIVE_FROM_MAX,
    EFFECTIVE_FROM_MIN,
    ChargeStatus,
    PeriodFrequency,
)
from app.modules.billing.tuition import PeriodState
from app.workflows.price_catalog_schemas import ServicePriceItem

type TuitionPlanStatus = Literal["scheduled", "active", "paused", "ended"]
"""A plan's status for the center's local today (#93 D1): ``scheduled``
(starts later), ``active``, ``paused`` (today's period is in a pause) or
``ended`` (its end is before today's period, or the linked enrollment was
withdrawn)."""
type TuitionUnbilledReason = Literal["no_price", "family_changed"]
"""Why a due period is not posted (#93 G1, K9)."""


def _in_range(value: date | None) -> date | None:
    if value is not None and not EFFECTIVE_FROM_MIN <= value <= EFFECTIVE_FROM_MAX:
        raise PydanticCustomError(
            "date_out_of_range", "Choose a date from 2000-01-01 to 2099-12-31."
        )
    return value


class TuitionRecord(BaseModel):
    """A student, family, group or subject: its id and current name."""

    id: uuid.UUID
    name: str


class TuitionPauseItem(BaseModel):
    """A pause (#93 K4): from a period start to a period end (``ends_on:
    null`` = until resumed)."""

    id: uuid.UUID
    version: int
    starts_on: date
    ends_on: date | None


class TuitionPostedPeriod(BaseModel):
    """A posted period: its charge, the charge's net and its status."""

    period_start: date
    period_end: date
    charge_id: uuid.UUID
    amount: Money
    status: ChargeStatus


class TuitionUpcomingPeriod(BaseModel):
    """A period from the current one on (#93 D1): its state (C2) and, for
    a due period, its charge (``charge_id``, once posted) or why it isn't
    posted (``reason``; ``null`` = the next run posts it)."""

    period_start: date
    period_end: date
    state: PeriodState
    charge_id: uuid.UUID | None
    reason: TuitionUnbilledReason | None


class TuitionPlanItem(BaseModel):
    """A tuition plan (#93 D1). ``family`` is the family whose account the
    plan bills (K9); ``service_price`` its pinned ``per_period`` catalog
    price; ``current_amount`` that price's version in effect today, before
    discounts (``null``: none, or not offered)."""

    id: uuid.UUID
    version: int
    student: TuitionRecord
    family: TuitionRecord
    service_price: ServicePriceItem
    group: TuitionRecord | None
    group_enrollment_id: uuid.UUID | None
    frequency: PeriodFrequency
    starts_on: date
    ends_on: date | None
    due_offset_days: int
    status: TuitionPlanStatus
    current_amount: Money | None


class TuitionPlanDetail(TuitionPlanItem):
    """A plan with its pauses, the end of its latest posted period, its last
    24 posted periods (newest first) and the next 12 periods from the
    current one (none after the plan's end)."""

    pauses: list[TuitionPauseItem]
    posted_through: date | None
    recent_periods: list[TuitionPostedPeriod]
    upcoming_periods: list[TuitionUpcomingPeriod]


class TuitionPlanCreate(BaseModel):
    """``POST /api/v1/billing/tuition-plans`` (#93 D3)."""

    model_config = ConfigDict(extra="forbid")

    student_id: uuid.UUID
    service_price_id: uuid.UUID
    group_enrollment_id: uuid.UUID | None = None
    starts_on: date
    ends_on: date | None = None
    due_offset_days: int = Field(default=0, ge=0, le=DUE_OFFSET_MAX)

    @field_validator("starts_on", "ends_on")
    @classmethod
    def _dates(cls, value: date | None) -> date | None:
        return _in_range(value)


class TuitionPlanUpdate(BaseModel):
    """``PATCH /api/v1/billing/tuition-plans/{id}`` (#93 D5): ``version``
    and any of ``due_offset_days`` and ``ends_on`` (``null`` =
    open-ended)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    due_offset_days: int | None = Field(default=None, ge=0, le=DUE_OFFSET_MAX)
    ends_on: date | None = None

    @field_validator("ends_on")
    @classmethod
    def _dates(cls, value: date | None) -> date | None:
        return _in_range(value)


class TuitionPauseCreate(BaseModel):
    """``POST /api/v1/billing/tuition-plans/{id}/pauses`` (#93 D7)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    starts_on: date
    ends_on: date | None = None

    @field_validator("starts_on", "ends_on")
    @classmethod
    def _dates(cls, value: date | None) -> date | None:
        return _in_range(value)


class TuitionResume(BaseModel):
    """``POST /api/v1/billing/tuition-plans/{id}/resume`` (#93 D8)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    resume_on: date

    @field_validator("resume_on")
    @classmethod
    def _dates(cls, value: date) -> date:
        _in_range(value)
        return value


class EligibleEnrollment(BaseModel):
    """An active group enrollment of a student a plan can be linked to
    (#93 D9), with whether it has a flat fee charge or a linked plan."""

    group_enrollment_id: uuid.UUID
    group: TuitionRecord
    subject: TuitionRecord
    enrolled_on: date
    has_fee_charge: bool
    has_plan: bool


class UnbilledTuitionItem(BaseModel):
    """A due tuition period that is not posted (#93 G1): ``no_price`` (the
    pinned price has no version in effect on the period's start, or it says
    "not offered") or ``family_changed`` (the student is no longer in the
    family whose account the plan bills, K9). ``family`` is the student's
    current family."""

    tuition_plan_id: uuid.UUID
    period_start: date
    period_end: date
    frequency: PeriodFrequency
    student: TuitionRecord
    family: TuitionRecord | None
    subject: TuitionRecord
    group: TuitionRecord | None
    reason: TuitionUnbilledReason
