"""Price catalog responses (#89 B1, E7): the billing items with the names
of the subject, level, cohort and group they point at."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.modules.billing.schemas import (
    CatalogPricingModel,
    ClassType,
    PriceVersionItem,
)


class PriceCatalogReference(BaseModel):
    """A level, cohort or group an item points at: its id and current
    name."""

    id: uuid.UUID
    name: str


class PriceCatalogSubject(BaseModel):
    """The item's subject, with its own level's name."""

    id: uuid.UUID
    name: str
    education_level_id: uuid.UUID
    education_level_name: str


class ServicePriceItem(BaseModel):
    """One catalog item (#89 B1). ``education_level`` and
    ``duration_minutes`` ``null`` mean "any"; ``cohort``/``group`` scope it.
    ``current`` is the version in effect today (``amount: null`` = not
    offered) and ``next`` the earliest upcoming one."""

    id: uuid.UUID
    class_type: ClassType
    pricing_model: CatalogPricingModel
    subject: PriceCatalogSubject
    education_level: PriceCatalogReference | None
    duration_minutes: int | None
    cohort: PriceCatalogReference | None
    group: PriceCatalogReference | None
    current: PriceVersionItem | None
    next: PriceVersionItem | None
    created_at: datetime


class ServicePriceDetail(ServicePriceItem):
    """An item with all its versions, newest ``effective_from`` first."""

    versions: list[PriceVersionItem]
