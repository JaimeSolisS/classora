"""Package products, purchases and package reads (#94 B, C, D5; L5, L7-L9):
the cross-module work behind ``app.workflows.package_products_router`` and
``app.workflows.packages_router``.

The billing module owns the rows and rules (``app.modules.billing.service``:
``packages.py``, ``package_rules.py``) and the #89 version service. This
workflow adds what needs other modules (ADR-0002): the subject
(``catalog``), the student and the family (``families``), the session
starts (``scheduling``) and the center's local today (``administration``).

**Products (L7).** Create: the subject (``subject_unknown``,
``subject_inactive``; the subject and its level ``FOR SHARE``) → the
``service_prices`` row, its first version and the product. ``PATCH``:
``name`` and ``is_active`` only. Versions go through the #89 service with
``pricing_models=("package",)``, so the #89 E3 date rules and ``409
price_version_in_use`` (a version a charge or a package used, the 0066
predicate) hold; a catalog item id is ``404`` here and a package item id is
``404`` on the #89 routes.

**Purchase (L8; C2 order after the body and the idempotency key):** the
product ``FOR SHARE`` (unknown → ``422`` on ``package_product_id``,
``package_product_unknown``; inactive → ``409 package_product_inactive``) →
the student (unknown → ``422`` on ``student_id``, ``student_unknown``) →
the student's **current** family (none → ``409 student_has_no_family``) →
the price: ``billing.service.quote`` pinned to the product's item
(``PriceQuery.service_price_id``, ``charge_kind='package_purchase'``, on
the center's today, the student's level), so #95 discounts apply (no custom
prices: #95 refuses them for ``package`` items); none or ``null`` → ``409
package_not_offered`` → the family's account (``ensure_billing_account``)
``FOR UPDATE`` (the last lock) → the ``packages`` row (``purchased_on`` =
the center's today, no backdating) → the ``package_purchase`` charge
(``post_charge``; ``service_date`` and due-date base = ``purchased_on``,
plus the account's ``due_offset_days``, E11; the ``charge_discounts``
snapshot) → the ``grant`` row. Billing off or a later floor never blocks a
purchase (L5): it is an explicit staff posting. A ``0.00`` price posts a
zero charge and still grants the units.
"""

import uuid
from collections.abc import Collection

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import FieldError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.billing.schemas import PriceVersionCreate
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import local_date
from app.workflows.package_schemas import (
    PackageCancelled,
    PackageDetail,
    PackageItem,
    PackageProductCreate,
    PackageProductDetail,
    PackageProductItem,
    PackageProductUpdate,
    PackagePurchase,
    PackageReference,
    PackageTransactionItem,
)


def _error(field: str, code: str, message: str) -> FieldError:
    return {"path": ["body", field], "code": code, "message": message}


# --- Products (B) --------------------------------------------------------------------


def _product_item(
    row: billing.ProductRow, current: billing.CurrentAndNext
) -> PackageProductItem:
    return PackageProductItem(
        id=row.id,
        name=row.name,
        unit=row.unit,
        quantity=row.quantity,
        validity_days=row.validity_days,
        subject=None
        if row.subject_id is None
        else PackageReference(id=row.subject_id, name=row.subject_name),
        is_active=row.is_active,
        current_amount=None if current.current is None else current.current.amount,
        version=row.version,
    )


async def list_package_products(
    session: AsyncSession,
    *,
    is_active: bool | None,
    subject_id: uuid.UUID | None,
    page: OffsetParams,
) -> OffsetPage[PackageProductItem]:
    """``GET /api/v1/billing/package-products`` (B1): by name and id. At
    most four statements (the zone, the page, the versions in effect; a
    count past the end)."""
    today = await administration.center_today(session)
    rows, total = await billing.list_products(
        session,
        catalog.subject_name_columns(),
        is_active=is_active,
        subject_id=subject_id,
        limit=page.limit,
        offset=page.offset,
    )
    versions = await billing.current_and_next(
        session, [row.service_price_id for row in rows], today=today
    )
    return OffsetPage[PackageProductItem](
        items=[_product_item(row, versions[row.service_price_id]) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def get_package_product(
    session: AsyncSession, product_id: uuid.UUID
) -> PackageProductDetail:
    """``GET /api/v1/billing/package-products/{id}`` (B1)."""
    today = await administration.center_today(session)
    row = await billing.get_product(session, product_id, catalog.subject_name_columns())
    versions = await billing.list_versions(session, row.service_price_id, today=today)
    item = _product_item(row, billing.current_and_next_of(versions))
    return PackageProductDetail(**item.model_dump(), versions=versions)


async def create_package_product(
    session: AsyncSession, body: PackageProductCreate
) -> PackageProductDetail:
    """``POST /api/v1/billing/package-products`` (B1): the subject (one
    ``422``) → the item, its first version and the product."""
    if body.subject_id is not None:
        subject = await catalog.get_subject_for_reference(
            session, body.subject_id, lock=True
        )
        if subject is None:
            raise billing.BillingValidationError(
                [_error("subject_id", "subject_unknown", "Choose an existing subject.")]
            )
        if not (subject.is_active and subject.education_level_is_active):
            raise billing.BillingValidationError(
                [
                    _error(
                        "subject_id",
                        "subject_inactive",
                        "The subject or its level is inactive. Choose an active"
                        " subject.",
                    )
                ]
            )
    product_id = await billing.create_product(
        session,
        name=body.name,
        unit=body.unit,
        quantity=body.quantity,
        validity_days=body.validity_days,
        subject_id=body.subject_id,
        amount=body.amount,
        effective_from=body.effective_from,
    )
    return await get_package_product(session, product_id)


async def update_package_product(
    session: AsyncSession, product_id: uuid.UUID, body: PackageProductUpdate
) -> PackageProductDetail:
    """``PATCH /api/v1/billing/package-products/{id}`` (B1)."""
    await billing.update_product(
        session,
        product_id,
        version=body.version,
        name=body.name,
        is_active=body.is_active,
    )
    return await get_package_product(session, product_id)


async def add_package_product_version(
    session: AsyncSession, product_id: uuid.UUID, body: PriceVersionCreate
) -> PackageProductDetail:
    """``POST /api/v1/billing/package-products/{id}/versions`` (B1; #89
    E3)."""
    service_price_id = await billing.product_service_price_id(session, product_id)
    await billing.add_price_version(
        session,
        service_price_id,
        effective_from=body.effective_from,
        amount=body.amount,
        today=await administration.center_today(session),
        pricing_models=billing.PACKAGE_PRICING_MODELS,
    )
    return await get_package_product(session, product_id)


async def delete_package_product_version(
    session: AsyncSession, product_id: uuid.UUID, price_version_id: uuid.UUID
) -> None:
    """``DELETE /api/v1/billing/package-products/{id}/versions/{version_id}``
    (B1; #89 B6)."""
    service_price_id = await billing.product_service_price_id(session, product_id)
    await billing.delete_price_version(
        session,
        service_price_id,
        price_version_id,
        today=await administration.center_today(session),
        pricing_models=billing.PACKAGE_PRICING_MODELS,
    )


# --- Purchase (C) --------------------------------------------------------------------


async def purchase_package(
    session: AsyncSession, body: PackagePurchase
) -> PackageDetail:
    """``POST /api/v1/billing/packages`` (C2, C3; the order of the module
    docstring). Never commits: the route stores the response on the
    idempotency key in the same transaction."""
    product = await billing.lock_product_for_sale(session, body.package_product_id)
    if product is None:
        raise billing.BillingValidationError(
            [
                _error(
                    "package_product_id",
                    "package_product_unknown",
                    "Choose an existing package.",
                )
            ]
        )
    if not product.is_active:
        raise billing.PackageProductInactiveError()
    student = (await families.student_level_references(session, [body.student_id])).get(
        body.student_id
    )
    if student is None:
        raise billing.BillingValidationError(
            [_error("student_id", "student_unknown", "Choose an existing student.")]
        )
    family = await families.family_of_student(session, body.student_id)
    if family is None:
        raise billing.StudentHasNoFamilyAccountError()
    zone = await administration.get_center_time_zone(session)
    today = local_date(clock.now(), zone)
    quote = await billing.quote(
        session,
        billing.PriceQuery(
            student_id=body.student_id,
            family_id=family.id,
            charge_kind="package_purchase",
            class_type="individual",
            pricing_model=billing.PACKAGE_PRICING_MODELS[0],
            subject_id=product.subject_id,
            on=today,
            education_level_id=student.education_level_id,
            service_price_id=product.service_price_id,
        ),
    )
    if quote is None or quote.price_version_id is None:
        raise billing.PackageNotOfferedError()
    account_id = await billing.ensure_billing_account(session, family.id)
    await billing.lock_billing_account(session, account_id)
    package = await billing.insert_package(
        session,
        billing_account_id=account_id,
        student_id=body.student_id,
        product=product,
        price_version_id=quote.price_version_id,
        purchased_on=today,
    )
    await billing.post_charge(
        session,
        billing.ChargeDraft.priced(
            quote,
            student_id=body.student_id,
            kind="package_purchase",
            package_id=package.id,
            service_date=today,
            due_date_base=today,
            education_level_id=student.education_level_id,
        ),
    )
    await billing.grant_package(session, package)
    return await get_package(session, package.id)


# --- Reads (D5) ----------------------------------------------------------------------


def _names() -> billing.PackageNames:
    return billing.PackageNames(
        students=families.student_name_columns(),
        families=families.family_name_columns(),
        subjects=catalog.subject_name_columns(),
    )


def _item(row: billing.PackageRow) -> PackageItem:
    return PackageItem(
        id=row.id,
        student=PackageReference(id=row.student_id, name=row.student_name),
        family=PackageReference(id=row.family_id, name=row.family_name),
        product=PackageReference(id=row.product_id, name=row.product_name),
        unit=row.unit,
        units_granted=row.units_granted,
        remaining_units=row.remaining_units,
        subject=None
        if row.subject_id is None
        else PackageReference(id=row.subject_id, name=row.subject_name),
        purchased_on=row.purchased_on,
        expires_on=row.expires_on,
        status=row.status,
        purchase_charge_id=row.purchase_charge_id,
    )


async def list_packages(
    session: AsyncSession,
    *,
    student_id: uuid.UUID | None,
    family_id: uuid.UUID | None,
    statuses: Collection[billing.PackageStatus],
    page: OffsetParams,
) -> OffsetPage[PackageItem]:
    """``GET /api/v1/billing/packages`` (D5): by ``(expires_on,
    purchased_on, id)``. At most three statements (the zone, the page; a
    count past the end), whatever the page size."""
    today = await administration.center_today(session)
    rows, total = await billing.list_packages(
        session,
        _names(),
        today=today,
        student_id=student_id,
        family_id=family_id,
        statuses=statuses,
        limit=page.limit,
        offset=page.offset,
    )
    return OffsetPage[PackageItem](
        items=[_item(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def get_package(session: AsyncSession, package_id: uuid.UUID) -> PackageDetail:
    """``GET /api/v1/billing/packages/{id}`` (D5): the item and its unit
    movements, oldest first (``404`` when unknown). Four statements (the
    zone, the package, its movements, the consumed sessions' starts)."""
    today = await administration.center_today(session)
    row, transactions = await billing.get_package(
        session, _names(), package_id, today=today
    )
    starts = await scheduling.session_starts(
        session,
        {t.class_session_id for t in transactions if t.class_session_id is not None},
    )
    return PackageDetail(
        **_item(row).model_dump(),
        transactions=[
            PackageTransactionItem(
                id=t.id,
                kind=t.kind,  # type: ignore[arg-type]
                units=t.units,
                class_session_id=t.class_session_id,
                session_starts_at=None
                if t.class_session_id is None
                else starts.get(t.class_session_id),
                created_at=t.created_at,
            )
            for t in transactions
        ],
    )


# --- Cancellation (#100 C5, R6) ------------------------------------------------------


async def cancel_package(
    session: AsyncSession,
    package_id: uuid.UUID,
    body: billing.PackageCancellationCreate,
) -> PackageCancelled:
    """``POST /api/v1/billing/packages/{package_id}/cancellation`` (#100
    C5): ``billing.cancel_package`` (unknown → ``404``; the account lock;
    ``409 package_not_cancellable``, ``409 charge_net_negative``) against
    the center's today, then the package read. Never commits."""
    today = await administration.center_today(session)
    result = await billing.cancel_package(
        session,
        package_id,
        reason=body.reason,
        credit_amount=body.credit_amount,
        today=today,
    )
    return PackageCancelled(
        package=await get_package(session, package_id),
        units_reversed=result.units_reversed,
        credit=result.credit,
        adjustment_id=result.adjustment_id,
        account_credit_after=result.account_credit_after,
    )
