"""The service price catalog (#89 B; E3, E7): the cross-module reads behind
``app.workflows.price_catalog_router``.

The billing module owns the items, the versions, their rules and the
lookup (``app.modules.billing.service``). This workflow adds what needs
other modules (ADR-0002):

- **References** of a new item (``POST``), after the body's ``422`` and
  all in one ``422``: the subject (``subject_unknown``; ``subject_inactive``
  for an inactive subject or level) and the level (``education_level_unknown``,
  ``education_level_inactive``), both locked ``FOR SHARE``
  (``catalog.service``); the cohort (``cohort_unknown``,
  ``courses.service.get_cohort``) and the group (``group_unknown``,
  ``scheduling.service.group_subject_id``), each of the body's subject
  (``scope_subject_mismatch``). The level is the **student's** level, so
  any active level is accepted. Existing items keep working after a
  reference is deactivated.
- **Names and order** of the responses: the unexecuted name selects of
  ``catalog``, ``courses`` and ``scheduling`` that billing joins.
- **Today**: the center's local date (``administration.service.center_today``),
  for the version statuses and the date rules.

Statements: a list page is at most four (the zone, the page with its
total, the versions; a count when the page is past the end), a detail
three (the zone, the item, its versions).
"""

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FieldError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.billing.schemas import PriceVersionCreate, ServicePriceCreate
from app.modules.catalog import service as catalog
from app.modules.courses import service as courses
from app.modules.scheduling import service as scheduling
from app.workflows.price_catalog_schemas import (
    PriceCatalogReference,
    PriceCatalogSubject,
    ServicePriceDetail,
    ServicePriceItem,
)


def _names() -> billing.CatalogNames:
    return billing.CatalogNames(
        subjects=catalog.subject_sort_columns(),
        levels=catalog.education_level_sort_columns(),
        cohorts=courses.cohort_name_columns(),
        groups=scheduling.group_name_columns(),
    )


def _error(field: str, code: str, message: str) -> FieldError:
    return {"path": ["body", field], "code": code, "message": message}


def _item(
    row: billing.CatalogItemRow, versions: billing.CurrentAndNext
) -> ServicePriceItem:
    return ServicePriceItem(
        id=row.id,
        class_type=row.class_type,  # type: ignore[arg-type]
        pricing_model=row.pricing_model,  # type: ignore[arg-type]
        subject=PriceCatalogSubject(
            id=row.subject_id,
            name=row.subject_name,
            education_level_id=row.subject_level_id,
            education_level_name=row.subject_level_name,
        ),
        education_level=None
        if row.education_level_id is None
        else PriceCatalogReference(
            id=row.education_level_id, name=row.education_level_name
        ),
        duration_minutes=row.duration_minutes,
        cohort=None
        if row.cohort_id is None
        else PriceCatalogReference(id=row.cohort_id, name=row.cohort_name),
        group=None
        if row.group_id is None
        else PriceCatalogReference(id=row.group_id, name=row.group_name),
        current=versions.current,
        next=versions.next,
        created_at=row.created_at,
    )


async def list_service_prices(
    session: AsyncSession,
    *,
    class_type: str | None,
    pricing_model: str | None,
    subject_id: uuid.UUID | None,
    page: OffsetParams,
) -> OffsetPage[ServicePriceItem]:
    """``GET /api/v1/billing/service-prices`` (B2)."""
    today = await administration.center_today(session)
    rows, total = await billing.list_service_prices(
        session,
        _names(),
        class_type=class_type,
        pricing_model=pricing_model,
        subject_id=subject_id,
        limit=page.limit,
        offset=page.offset,
    )
    versions = await billing.current_and_next(
        session, [row.id for row in rows], today=today
    )
    return OffsetPage[ServicePriceItem](
        items=[_item(row, versions[row.id]) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def _detail(
    session: AsyncSession, service_price_id: uuid.UUID, today: date
) -> ServicePriceDetail:
    row = await billing.get_service_price(session, _names(), service_price_id)
    versions = await billing.list_versions(session, service_price_id, today=today)
    item = _item(row, billing.current_and_next_of(versions))
    return ServicePriceDetail(**item.model_dump(), versions=versions)


async def get_service_price(
    session: AsyncSession, service_price_id: uuid.UUID
) -> ServicePriceDetail:
    """``GET /api/v1/billing/service-prices/{id}`` (B4)."""
    return await _detail(
        session, service_price_id, await administration.center_today(session)
    )


async def _reference_errors(
    session: AsyncSession, body: ServicePriceCreate
) -> list[FieldError]:
    errors: list[FieldError] = []
    subject = await catalog.get_subject_for_reference(
        session, body.subject_id, lock=True
    )
    if subject is None:
        errors.append(
            _error("subject_id", "subject_unknown", "Choose an existing subject.")
        )
    elif not (subject.is_active and subject.education_level_is_active):
        errors.append(
            _error(
                "subject_id",
                "subject_inactive",
                "The subject or its level is inactive. Choose an active subject.",
            )
        )
    if body.education_level_id is not None:
        level = await catalog.get_level_for_reference(
            session, body.education_level_id, lock=True
        )
        if level is None:
            errors.append(
                _error(
                    "education_level_id",
                    "education_level_unknown",
                    "Choose an existing education level.",
                )
            )
        elif not level.is_active:
            errors.append(
                _error(
                    "education_level_id",
                    "education_level_inactive",
                    "The education level is inactive. Choose an active one.",
                )
            )
    if body.cohort_id is not None:
        try:
            cohort = await courses.get_cohort(session, body.cohort_id)
        except courses.CohortNotFoundError:
            errors.append(
                _error("cohort_id", "cohort_unknown", "Choose an existing cohort.")
            )
        else:
            if cohort.subject_id != body.subject_id:
                errors.append(
                    _error(
                        "cohort_id",
                        "scope_subject_mismatch",
                        "The cohort is of another subject.",
                    )
                )
    if body.group_id is not None:
        group_subject = await scheduling.group_subject_id(session, body.group_id)
        if group_subject is None:
            errors.append(
                _error("group_id", "group_unknown", "Choose an existing group.")
            )
        elif group_subject != body.subject_id:
            errors.append(
                _error(
                    "group_id",
                    "scope_subject_mismatch",
                    "The group is of another subject.",
                )
            )
    return errors


async def create_service_price(
    session: AsyncSession, body: ServicePriceCreate
) -> ServicePriceDetail:
    """``POST /api/v1/billing/service-prices`` (B3): the references (one
    ``422``) → ``409 service_price_exists`` → the item and its first
    version."""
    errors = await _reference_errors(session, body)
    if errors:
        raise billing.PriceCatalogValidationError(errors)
    service_price_id = await billing.create_service_price(
        session,
        billing.CatalogKey(
            class_type=body.class_type,
            pricing_model=body.pricing_model,
            subject_id=body.subject_id,
            education_level_id=body.education_level_id,
            duration_minutes=body.duration_minutes,
            cohort_id=body.cohort_id,
            group_id=body.group_id,
        ),
        effective_from=body.effective_from,
        amount=body.amount,
    )
    return await _detail(
        session, service_price_id, await administration.center_today(session)
    )


async def add_price_version(
    session: AsyncSession, service_price_id: uuid.UUID, body: PriceVersionCreate
) -> ServicePriceDetail:
    """``POST /api/v1/billing/service-prices/{id}/versions`` (B5): ``404``
    → the item lock → the E3 date rule → insert."""
    today = await administration.center_today(session)
    await billing.add_price_version(
        session,
        service_price_id,
        effective_from=body.effective_from,
        amount=body.amount,
        today=today,
    )
    return await _detail(session, service_price_id, today)


async def delete_price_version(
    session: AsyncSession, service_price_id: uuid.UUID, price_version_id: uuid.UUID
) -> None:
    """``DELETE /api/v1/billing/service-prices/{id}/versions/{version_id}``
    (B6)."""
    await billing.delete_price_version(
        session,
        service_price_id,
        price_version_id,
        today=await administration.center_today(session),
    )
