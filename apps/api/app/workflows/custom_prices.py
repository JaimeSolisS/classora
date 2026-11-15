"""Custom student and family prices (#95 E; H6, H7, H10): the cross-module
reads behind ``app.workflows.custom_prices_router``.

The billing module owns the rows and their rules
(``app.modules.billing.service``, ``custom_prices.py``). This workflow adds
what needs other modules (ADR-0002):

- **Create order** (E3), after the body's ``422``: ``422
  custom_price_effective_from_invalid`` (before the center's local today,
  H7) → the references, all in one ``422``: the item
  (``service_price_unknown``; ``pricing_model_not_allowed`` unless
  ``per_session`` or ``full_course``) and the student (``student_unknown``)
  or family (``family_unknown``) → ``409 custom_price_overlap`` → insert.
- **Names and order:** the #89 catalog names (``app.workflows.
  price_catalog``) and the student and family name selects
  (``families.service``), joined by billing in one statement; the item's
  current catalog version in one more.
- **Today:** the center's local date (statuses and the date rules).

Statements: a list page is at most three (the zone, the page with its
total, the current catalog versions; a count when the page is past the
end), a detail three.
"""

import uuid
from collections.abc import Collection, Sequence
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FieldError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.families import service as families
from app.workflows.billing_discount_schemas import (
    BillingReference,
    CustomPriceItem,
    CustomPriceServicePrice,
)
from app.workflows.billing_discounts import target_names
from app.workflows.price_catalog import catalog_names, service_price_item


async def _items(
    session: AsyncSession, rows: Sequence[billing.CustomPriceRow], today: date
) -> list[CustomPriceItem]:
    versions = await billing.current_and_next(
        session, {row.item.id for row in rows}, today=today
    )
    items: list[CustomPriceItem] = []
    for row in rows:
        item = service_price_item(row.item, versions[row.item.id])
        target = row.student_id if row.student_id is not None else row.family_id
        assert target is not None  # noqa: S101 - one target (ck_custom_prices_one_target)
        reference = BillingReference(id=target, name=row.target_name)
        items.append(
            CustomPriceItem(
                id=row.id,
                service_price=CustomPriceServicePrice(
                    **item.model_dump(exclude={"next", "created_at"})
                ),
                student=reference if row.student_id is not None else None,
                family=reference if row.family_id is not None else None,
                effective_from=row.effective_from,
                expires_on=row.expires_on,
                amount=row.amount,
                status=row.status,  # type: ignore[arg-type]
                in_use=row.in_use,
                version=row.version,
                created_at=row.created_at,
            )
        )
    return items


async def list_custom_prices(
    session: AsyncSession,
    *,
    student_id: uuid.UUID | None,
    family_id: uuid.UUID | None,
    service_price_id: uuid.UUID | None,
    statuses: Collection[str],
    page: OffsetParams,
) -> OffsetPage[CustomPriceItem]:
    """``GET /api/v1/billing/custom-prices`` (E2)."""
    today = await administration.center_today(session)
    rows, total = await billing.list_custom_prices(
        session,
        catalog_names(),
        target_names(),
        student_id=student_id,
        family_id=family_id,
        service_price_id=service_price_id,
        statuses=statuses,
        today=today,
        limit=page.limit,
        offset=page.offset,
    )
    return OffsetPage[CustomPriceItem](
        items=await _items(session, rows, today),
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def _detail(
    session: AsyncSession, custom_price_id: uuid.UUID, today: date
) -> CustomPriceItem:
    row = await billing.get_custom_price(
        session, catalog_names(), target_names(), custom_price_id, today=today
    )
    [item] = await _items(session, [row], today)
    return item


async def get_custom_price(
    session: AsyncSession, custom_price_id: uuid.UUID
) -> CustomPriceItem:
    """``GET /api/v1/billing/custom-prices/{id}`` (E4)."""
    return await _detail(
        session, custom_price_id, await administration.center_today(session)
    )


def _error(field: str, code: str, message: str) -> FieldError:
    return {"path": ["body", field], "code": code, "message": message}


async def _reference_errors(
    session: AsyncSession, body: billing.CustomPriceCreate
) -> list[FieldError]:
    errors: list[FieldError] = []
    model = await billing.service_price_model(session, body.service_price_id)
    if model is None:
        errors.append(
            _error(
                "service_price_id",
                "service_price_unknown",
                "Choose an existing catalog price.",
            )
        )
    elif model not in billing.CUSTOM_PRICE_MODELS:
        errors.append(
            _error(
                "service_price_id",
                "pricing_model_not_allowed",
                "Custom prices are for per-session and full-course prices only.",
            )
        )
    if body.student_id is not None:
        if not await families.student_references(session, [body.student_id]):
            errors.append(
                _error("student_id", "student_unknown", "Choose an existing student.")
            )
    elif body.family_id is not None and (
        await families.family_record_reference(session, body.family_id) is None
    ):
        errors.append(
            _error("family_id", "family_unknown", "Choose an existing family.")
        )
    return errors


async def create_custom_price(
    session: AsyncSession, body: billing.CustomPriceCreate
) -> CustomPriceItem:
    """``POST /api/v1/billing/custom-prices`` (E3; the order of the module
    docstring)."""
    today = await administration.center_today(session)
    if body.effective_from < today:
        raise billing.CustomPriceEffectiveFromInvalidError()
    errors = await _reference_errors(session, body)
    if errors:
        raise billing.BillingValidationError(errors)
    custom_price_id = await billing.create_custom_price(session, body)
    return await _detail(session, custom_price_id, today)


async def update_custom_price(
    session: AsyncSession, custom_price_id: uuid.UUID, body: billing.CustomPriceUpdate
) -> CustomPriceItem:
    """``PATCH /api/v1/billing/custom-prices/{id}`` (E5)."""
    today = await administration.center_today(session)
    await billing.update_custom_price(session, custom_price_id, body, today=today)
    return await _detail(session, custom_price_id, today)


async def delete_custom_price(
    session: AsyncSession, custom_price_id: uuid.UUID
) -> None:
    """``DELETE /api/v1/billing/custom-prices/{id}`` (E6)."""
    await billing.delete_custom_price(
        session, custom_price_id, today=await administration.center_today(session)
    )
