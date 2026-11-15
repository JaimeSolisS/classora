"""Discount and custom price responses (#95 D1, E1): the billing rows with
the names of the subjects, levels, students, families and catalog items
they point at."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.money import Money
from app.modules.billing.discounts import (
    CriteriaChargeKind,
    CriteriaClassType,
    DiscountAudience,
    DiscountKind,
)
from app.modules.billing.schemas import (
    CatalogPricingModel,
    ClassType,
    PriceVersionItem,
)
from app.workflows.price_catalog_schemas import (
    PriceCatalogReference,
    PriceCatalogSubject,
)

type DiscountStatus = Literal["inactive", "upcoming", "current", "expired"]
type CustomPriceStatus = Literal["past", "current", "upcoming"]


class BillingReference(BaseModel):
    """A record a discount or custom price points at: its id and current
    name."""

    id: uuid.UUID
    name: str


class DiscountEligibilityDetail(BaseModel):
    """The criteria of a discount (#95 H2), every one ``null`` = any; the
    subject and level ids come with their current names. ``subjects`` is the
    charge's subject; ``education_levels`` the student's level at posting;
    ``min_family_students`` (sibling discounts only) the family's active
    students."""

    charge_kinds: list[CriteriaChargeKind] | None
    class_types: list[CriteriaClassType] | None
    subjects: list[BillingReference] | None
    education_levels: list[BillingReference] | None
    min_family_students: int | None


class DiscountItem(BaseModel):
    """One discount (#95 D1). Exactly one of ``percent`` (four places) and
    ``amount``. ``status`` is against the center's local today:
    ``inactive``, ``upcoming`` (starts later), ``expired`` (ended before
    today) or ``current``."""

    id: uuid.UUID
    kind: DiscountKind
    name: str
    audience: DiscountAudience
    percent: str | None = Field(examples=["10.0000"])
    amount: Money | None
    eligibility: DiscountEligibilityDetail
    starts_on: date | None
    expires_on: date | None
    is_active: bool
    status: DiscountStatus
    assignment_count: int
    version: int
    created_at: datetime


class DiscountAssignmentItem(BaseModel):
    """One assignment: a student or a family (the other is ``null``)."""

    id: uuid.UUID
    student: BillingReference | None
    family: BillingReference | None
    created_at: datetime


class DiscountDetail(DiscountItem):
    """A discount with its assignments, by student or family name."""

    assignments: list[DiscountAssignmentItem]


class CustomPriceServicePrice(BaseModel):
    """The catalog item a custom price is for (the #89 item without its
    versions), with its ``current`` catalog version for comparison."""

    id: uuid.UUID
    class_type: ClassType
    pricing_model: CatalogPricingModel
    subject: PriceCatalogSubject
    education_level: PriceCatalogReference | None
    duration_minutes: int | None
    cohort: PriceCatalogReference | None
    group: PriceCatalogReference | None
    current: PriceVersionItem | None


class CustomPriceItem(BaseModel):
    """One custom price (#95 E1): for a student or a family (the other is
    ``null``), from ``effective_from`` to ``expires_on`` (inclusive;
    ``null`` = open-ended). ``status`` against the center's local today;
    ``in_use``: a charge was posted at this price (then only ``expires_on``
    can change)."""

    id: uuid.UUID
    service_price: CustomPriceServicePrice
    student: BillingReference | None
    family: BillingReference | None
    effective_from: date
    expires_on: date | None
    amount: Money
    status: CustomPriceStatus
    in_use: bool
    version: int
    created_at: datetime
