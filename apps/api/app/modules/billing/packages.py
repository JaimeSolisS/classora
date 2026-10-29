"""Prepaid packages (#94 M6-09; spec §6 packages, W4.1; ADR-0006 E1, E6,
E7, E10, E11; ``_docs/data-model.md`` §3.18-§3.20, §4.1 I8/I9, §4.2, §6).

**Products** (L7): a package product owns one ``service_prices`` row
(``individual``/``package``, the product's subject or ``NULL``) whose
versions are its price; the version rules are #89's
(:func:`~app.modules.billing.prices.add_price_version` and
:func:`~app.modules.billing.prices.delete_price_version` with
``pricing_models=("package",)``). ``unit``, ``quantity``,
``validity_days`` and ``subject_id`` never change (a different offer is a
new product; sold packages snapshot them anyway); only ``name`` and
``is_active`` do. Products are never deleted.

**Purchase** (L8; the workflow ``app.workflows.packages`` runs the order):
the product ``FOR SHARE`` (:func:`lock_product_for_sale`) → the account
``FOR UPDATE`` (last) → :func:`insert_package` (the snapshot, ``expires_on
= purchased_on + validity_days``) → the ``package_purchase`` charge
(``post_charge``) → :func:`grant_package` (the ``grant`` row).

**Coverage (L2, L3; :func:`package_covered_keys`)**. A key ``(class_session_id,
student_id)`` is covered when (i) a ``consume`` row exists for it, or (ii)
it is an ``individual`` session whose closeout has no approval yet and an
**eligible** package can cover it whole (the L4 plan,
:func:`~app.modules.billing.package_rules.plan_consumption`). Eligible:

- the same student, on the account of the student's **current** family (a
  sibling's or another family's package is never used, spec §6);
- ``subject_id`` ``NULL`` or the session's subject;
- ``purchased_on <= service_date <= expires_on`` (the session's local
  date, not today, L2: a session dated before the purchase was normally
  charged already, E10 "not converted");
- no ``expire`` row, and remaining units > 0.

An approved key that wasn't consumed (too little credit at approval) is
**not** covered, so the session sweeper charges it (L3).

**Consumption (L4, D2; :func:`consume_session_credit`)**, at closeout
approval revision 1 and in the expiry job's fallback (L1), under the
account lock: the student's current family (none → ``no_package``) → its
account (none → ``no_package``) ``FOR UPDATE`` (the last lock) → a
``session`` charge for the key (any account) → ``charged``, nothing written
→ a ``consume`` row for the key → ``already_consumed`` → the eligible
packages (none → ``no_package``) → the L4 plan (none → ``short``, nothing
written: all or nothing, E10) → one ``consume`` row per package used, in a
savepoint (a natural-key violation → ``already_consumed``). The session's
state is **not** checked here (#99 consumes for a cancelled session's
``charge`` outcome). A minutes package consumes ``scheduled_minutes``
(E2), a sessions package 1 unit.

**I9** (at most one of {session charge, consumption} per key) holds on both
sides under the same account lock: ``post_charge`` refuses a ``session``
charge for a consumed key (``SessionSettledByPackageError``), and this
consumption writes nothing for a charged key. **I8** (remaining >= 0) holds
by construction: a plan never takes more than a package's remaining units,
computed under the account lock.

**Expiry** (L1, F; ``app.workflows.package_expiry``): :func:`due_packages`
(``expires_on <`` the center's today, no ``expire`` row, remaining > 0)
and :func:`expire_package` (account ``FOR UPDATE`` → remaining recomputed
→ one ``expire`` row for it).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal, cast

from sqlalchemy import (
    ColumnElement,
    Select,
    case,
    exists,
    func,
    literal,
    or_,
    select,
    tuple_,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.core.money import Money
from app.modules.billing.ledger import lock_billing_account
from app.modules.billing.models import (
    PACKAGE_CONSUME_KEY,
    PACKAGE_EXPIRE_KEY,
    BillingAccount,
    Charge,
    Package,
    PackageCreditTransaction,
    PackageProduct,
    PriceVersion,
    ServicePrice,
)
from app.modules.billing.package_rules import (
    ConsumptionPlan,
    EligiblePackage,
    PackageStatus,
    PackageUnit,
    package_status,
    plan_consumption,
)
from app.modules.billing.schemas import CoverageQuery
from app.modules.families import service as families

PACKAGE_MODEL: Final = "package"
PACKAGE_PRICING_MODELS: Final = (PACKAGE_MODEL,)
"""What the package product routes pass to the #89 version service."""
DUE_PAGE: Final = 200
"""Due packages per keyset page of the expiry job."""

type ConsumeOutcome = Literal[
    "consumed", "already_consumed", "charged", "short", "no_package"
]
type ExpireOutcome = Literal["expired", "already_expired", "nothing_left"]


def _violates(error: IntegrityError, name: str) -> bool:
    return name in str(error.orig)


# --- Products (L7) -------------------------------------------------------------------


@dataclass(frozen=True)
class ProductRow:
    """A package product with its subject's name (``""`` when it has none or
    the name wasn't joined)."""

    id: uuid.UUID
    service_price_id: uuid.UUID
    name: str
    unit: PackageUnit
    quantity: int
    validity_days: int
    subject_id: uuid.UUID | None
    subject_name: str
    is_active: bool
    version: int
    created_at: datetime


def _product_row(product: PackageProduct, subject_name: str | None) -> ProductRow:
    return ProductRow(
        id=product.id,
        service_price_id=product.service_price_id,
        name=product.name,
        unit=cast(PackageUnit, product.unit),
        quantity=product.quantity,
        validity_days=product.validity_days,
        subject_id=product.subject_id,
        subject_name=subject_name or "",
        is_active=product.is_active,
        version=product.version,
        created_at=product.created_at,
    )


async def create_product(
    session: AsyncSession,
    *,
    name: str,
    unit: PackageUnit,
    quantity: int,
    validity_days: int,
    subject_id: uuid.UUID | None,
    amount: Money,
    effective_from: date,
) -> uuid.UUID:
    """A product, its ``service_prices`` row (``individual``/``package``)
    and its first version, in the caller's transaction (B1). The caller
    checked the body and the subject."""
    item = ServicePrice(
        class_type="individual", pricing_model=PACKAGE_MODEL, subject_id=subject_id
    )
    session.add(item)
    await session.flush()
    session.add(
        PriceVersion(
            service_price_id=item.id, effective_from=effective_from, amount=amount
        )
    )
    product = PackageProduct(
        service_price_id=item.id,
        name=name,
        unit=unit,
        quantity=quantity,
        validity_days=validity_days,
        subject_id=subject_id,
        is_active=True,
    )
    session.add(product)
    await session.flush()
    return product.id


def _named_products(subject_names: Select[*tuple[Any, ...]]) -> tuple[Select[Any], Any]:
    subjects = subject_names.subquery("product_subjects")
    return (
        select(PackageProduct, subjects.c.name).outerjoin(
            subjects, subjects.c.id == PackageProduct.subject_id
        ),
        subjects,
    )


async def get_product(
    session: AsyncSession,
    product_id: uuid.UUID,
    subject_names: Select[*tuple[Any, ...]],
) -> ProductRow:
    """One product with its subject's name (``404`` when unknown). One
    statement."""
    query, _ = _named_products(subject_names)
    found = (
        await session.execute(query.where(PackageProduct.id == product_id))
    ).first()
    if found is None:
        raise RecordNotFoundError()
    return _product_row(found[0], found[1])


async def list_products(
    session: AsyncSession,
    subject_names: Select[*tuple[Any, ...]],
    *,
    is_active: bool | None,
    subject_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[ProductRow], int]:
    """A page of products by name (case-insensitive) and id, and the total.
    One statement (a window count), plus a count past the end."""
    query, _ = _named_products(subject_names)
    conditions: list[ColumnElement[bool]] = []
    if is_active is not None:
        conditions.append(PackageProduct.is_active.is_(is_active))
    if subject_id is not None:
        conditions.append(PackageProduct.subject_id == subject_id)
    rows = (
        await session.execute(
            query.add_columns(func.count().over())
            .where(*conditions)
            .order_by(func.lower(PackageProduct.name), PackageProduct.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if rows:
        return [_product_row(row[0], row[1]) for row in rows], int(rows[0][-1])
    total = await session.scalar(
        select(func.count(PackageProduct.id)).where(*conditions)
    )
    return [], int(total or 0)


async def product_service_price_id(
    session: AsyncSession, product_id: uuid.UUID
) -> uuid.UUID:
    """The product's ``service_prices`` id (``404`` when unknown)."""
    found: uuid.UUID | None = await session.scalar(
        select(PackageProduct.service_price_id).where(PackageProduct.id == product_id)
    )
    if found is None:
        raise RecordNotFoundError()
    return found


async def update_product(
    session: AsyncSession,
    product_id: uuid.UUID,
    *,
    version: int,
    name: str | None,
    is_active: bool | None,
) -> None:
    """``PATCH`` (B1): the row ``FOR UPDATE`` (``404``) → ``409
    version_conflict`` → the given fields (no change: no write, no
    version bump)."""
    product = await session.scalar(
        select(PackageProduct)
        .where(PackageProduct.id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if product is None:
        raise RecordNotFoundError()
    ensure_version(product, version)
    changed = False
    if name is not None and name != product.name:
        product.name = name
        changed = True
    if is_active is not None and is_active != product.is_active:
        product.is_active = is_active
        changed = True
    if changed:
        await session.flush()


async def lock_product_for_sale(
    session: AsyncSession, product_id: uuid.UUID
) -> PackageProduct | None:
    """The product row ``FOR SHARE`` (a ``PATCH`` waits for the sale), or
    ``None`` when unknown (C2)."""
    product: PackageProduct | None = await session.scalar(
        select(PackageProduct)
        .where(PackageProduct.id == product_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    return product


# --- Purchase (L8) -------------------------------------------------------------------


async def insert_package(
    session: AsyncSession,
    *,
    billing_account_id: uuid.UUID,
    student_id: uuid.UUID,
    product: PackageProduct,
    price_version_id: uuid.UUID,
    purchased_on: date,
) -> Package:
    """The ``packages`` row: the product's unit, quantity and subject
    snapshotted, ``expires_on = purchased_on + validity_days`` (C3). The
    caller holds the account lock."""
    package = Package(
        billing_account_id=billing_account_id,
        student_id=student_id,
        package_product_id=product.id,
        price_version_id=price_version_id,
        unit=product.unit,
        units_granted=product.quantity,
        subject_id=product.subject_id,
        purchased_on=purchased_on,
        expires_on=purchased_on + timedelta(days=product.validity_days),
    )
    session.add(package)
    await session.flush()
    return package


async def grant_package(session: AsyncSession, package: Package) -> uuid.UUID:
    """The ``grant`` row (``+units_granted``; C3)."""
    grant = PackageCreditTransaction(
        package_id=package.id, kind="grant", units=package.units_granted
    )
    session.add(grant)
    await session.flush()
    return grant.id


# --- Remaining units -----------------------------------------------------------------


def _remaining_column() -> Any:
    """``Σ units`` of the package row (a correlated scalar subquery)."""
    return (
        select(func.coalesce(func.sum(PackageCreditTransaction.units), 0))
        .where(PackageCreditTransaction.package_id == Package.id)
        .correlate(Package)
        .scalar_subquery()
    )


def _has_expire_column() -> Any:
    return (
        exists()
        .where(
            PackageCreditTransaction.package_id == Package.id,
            PackageCreditTransaction.kind == "expire",
        )
        .correlate(Package)
    )


async def package_remaining_units(
    session: AsyncSession, package_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """The remaining units of each package (Σ of its transactions; D4). One
    statement (none for no ids); unknown ids are left out."""
    if not package_ids:
        return {}
    rows = await session.execute(
        select(Package.id, _remaining_column()).where(Package.id.in_(set(package_ids)))
    )
    return {row[0]: int(row[1]) for row in rows}


def consumed_key_columns() -> Select[uuid.UUID | None, uuid.UUID | None]:
    """``SELECT class_session_id, student_id FROM package_credit_transactions
    WHERE kind = 'consume'``, **not executed**: the session sweeper's
    candidates anti-join it (#94 E2), and the expiry job's fallback too."""
    return select(
        PackageCreditTransaction.class_session_id, PackageCreditTransaction.student_id
    ).where(PackageCreditTransaction.kind == "consume")


# --- Coverage and consumption (D1, D2) -----------------------------------------------


@dataclass(frozen=True)
class SessionCreditClaim:
    """What :func:`consume_session_credit` consumes for (D2): one key with
    the session's subject, local service date and scheduled minutes (read
    under the caller's session lock)."""

    class_session_id: uuid.UUID
    student_id: uuid.UUID
    subject_id: uuid.UUID
    service_date: date
    scheduled_minutes: int


@dataclass(frozen=True)
class ConsumeResult:
    """The outcome of :func:`consume_session_credit` and ``(package_id,
    units)`` per package used (units positive; the rows store them
    negative). Only ``consumed`` has packages."""

    outcome: ConsumeOutcome
    consumed: ConsumptionPlan = ()


@dataclass(frozen=True)
class _Candidate:
    package: EligiblePackage
    billing_account_id: uuid.UUID
    family_id: uuid.UUID
    student_id: uuid.UUID
    subject_id: uuid.UUID | None


def _eligible_for(
    candidates: Sequence[_Candidate],
    *,
    family_id: uuid.UUID,
    student_id: uuid.UUID,
    subject_id: uuid.UUID,
    service_date: date,
) -> list[EligiblePackage]:
    """The D1 rules over prefetched candidates (remaining > 0, no ``expire``
    row already applied in SQL)."""
    return [
        c.package
        for c in candidates
        if c.family_id == family_id
        and c.student_id == student_id
        and (c.subject_id is None or c.subject_id == subject_id)
        and c.package.purchased_on <= service_date <= c.package.expires_on
    ]


async def _candidates(
    session: AsyncSession,
    pairs: Collection[tuple[uuid.UUID, uuid.UUID]],
    *,
    first: date,
    last: date,
) -> list[_Candidate]:
    """The packages of these ``(family_id, student_id)`` pairs (on the
    family's account) without an ``expire`` row, with remaining > 0 and a
    window meeting ``[first, last]``. One statement."""
    if not pairs:
        return []
    remaining = _remaining_column().label("remaining")
    inner = (
        select(
            Package.id,
            Package.unit,
            Package.expires_on,
            Package.purchased_on,
            Package.billing_account_id,
            Package.student_id,
            Package.subject_id,
            BillingAccount.family_id,
            remaining,
        )
        .join(BillingAccount, BillingAccount.id == Package.billing_account_id)
        .where(
            tuple_(BillingAccount.family_id, Package.student_id).in_(list(set(pairs))),
            Package.purchased_on <= last,
            Package.expires_on >= first,
            ~_has_expire_column(),
        )
        .subquery("candidates")
    )
    rows = await session.execute(select(inner).where(inner.c.remaining > 0))
    return [
        _Candidate(
            package=EligiblePackage(
                id=row.id,
                unit=cast(PackageUnit, row.unit),
                remaining=int(row.remaining),
                expires_on=row.expires_on,
                purchased_on=row.purchased_on,
            ),
            billing_account_id=row.billing_account_id,
            family_id=row.family_id,
            student_id=row.student_id,
            subject_id=row.subject_id,
        )
        for row in rows
    ]


async def _consumed_keys(
    session: AsyncSession, keys: Collection[tuple[uuid.UUID, uuid.UUID]]
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    if not keys:
        return set()
    rows = await session.execute(
        consumed_key_columns().where(
            tuple_(
                PackageCreditTransaction.class_session_id,
                PackageCreditTransaction.student_id,
            ).in_(list(set(keys)))
        )
    )
    return {(row[0], row[1]) for row in rows if row[0] is not None and row[1]}


async def package_covered_keys(
    session: AsyncSession, items: Sequence[CoverageQuery]
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """The keys of ``items`` a package covers (module docstring; D1, L3), so
    they take the package path instead of a session charge. The session
    sweeper, the unbilled list (#91) and, through the same rules,
    :func:`consume_session_credit` ask it. At most three statements for any
    number of items: the consumed keys, the students' families, the
    candidate packages with their remaining units."""
    if not items:
        return set()
    keys = {(i.class_session_id, i.student_id) for i in items}
    covered = await _consumed_keys(session, keys)
    open_items = [
        i
        for i in items
        if (i.class_session_id, i.student_id) not in covered
        and i.class_type == "individual"
        and not i.closeout_approved
    ]
    if not open_items:
        return covered
    family_of = await families.families_of_students(
        session, {i.student_id for i in open_items}
    )
    pairs = {
        (family_of[i.student_id].id, i.student_id)
        for i in open_items
        if i.student_id in family_of
    }
    if not pairs:
        return covered
    candidates = await _candidates(
        session,
        pairs,
        first=min(i.service_date for i in open_items),
        last=max(i.service_date for i in open_items),
    )
    for item in open_items:
        family = family_of.get(item.student_id)
        if family is None:
            continue
        eligible = _eligible_for(
            candidates,
            family_id=family.id,
            student_id=item.student_id,
            subject_id=item.subject_id,
            service_date=item.service_date,
        )
        if eligible and plan_consumption(eligible, item.scheduled_minutes):
            covered.add((item.class_session_id, item.student_id))
    return covered


async def _session_charged(
    session: AsyncSession, class_session_id: uuid.UUID, student_id: uuid.UUID
) -> bool:
    found = await session.scalar(
        select(Charge.id)
        .where(
            Charge.kind == "session",
            Charge.class_session_id == class_session_id,
            Charge.student_id == student_id,
        )
        .limit(1)
    )
    return found is not None


async def consume_session_credit(
    session: AsyncSession, claim: SessionCreditClaim
) -> ConsumeResult:
    """D2 (the order of the module docstring). Never raises for these
    outcomes; any other error propagates (the caller's Unit of Work rolls
    back)."""
    family = await families.family_of_student(session, claim.student_id)
    if family is None:
        return ConsumeResult("no_package")
    account_id: uuid.UUID | None = await session.scalar(
        select(BillingAccount.id).where(BillingAccount.family_id == family.id)
    )
    if account_id is None:
        return ConsumeResult("no_package")
    await lock_billing_account(session, account_id)
    key = (claim.class_session_id, claim.student_id)
    if await _session_charged(session, *key):
        return ConsumeResult("charged")
    if await _consumed_keys(session, [key]):
        return ConsumeResult("already_consumed")
    eligible = _eligible_for(
        await _candidates(
            session,
            [(family.id, claim.student_id)],
            first=claim.service_date,
            last=claim.service_date,
        ),
        family_id=family.id,
        student_id=claim.student_id,
        subject_id=claim.subject_id,
        service_date=claim.service_date,
    )
    if not eligible:
        return ConsumeResult("no_package")
    plan = plan_consumption(eligible, claim.scheduled_minutes)
    if plan is None:
        return ConsumeResult("short")
    try:
        async with session.begin_nested():
            session.add_all(
                PackageCreditTransaction(
                    package_id=package_id,
                    kind="consume",
                    units=-units,
                    class_session_id=claim.class_session_id,
                    student_id=claim.student_id,
                )
                for package_id, units in plan
            )
    except IntegrityError as exc:
        if not _violates(exc, PACKAGE_CONSUME_KEY):
            raise
        return ConsumeResult("already_consumed")
    return ConsumeResult("consumed", plan)


# --- Expiry (L1, F) ------------------------------------------------------------------


@dataclass(frozen=True)
class DuePackage:
    """A package the expiry job settles (F2)."""

    id: uuid.UUID
    billing_account_id: uuid.UUID
    student_id: uuid.UUID
    subject_id: uuid.UUID | None
    purchased_on: date
    expires_on: date


async def due_packages(
    session: AsyncSession,
    *,
    today: date,
    after: uuid.UUID | None = None,
    limit: int = DUE_PAGE,
) -> list[DuePackage]:
    """One keyset page (by id) of the packages with ``expires_on < today``,
    no ``expire`` row and remaining > 0 (F2). One statement."""
    query = select(
        Package.id,
        Package.billing_account_id,
        Package.student_id,
        Package.subject_id,
        Package.purchased_on,
        Package.expires_on,
    ).where(
        Package.expires_on < today,
        ~_has_expire_column(),
        _remaining_column() > 0,
    )
    if after is not None:
        query = query.where(Package.id > after)
    rows = await session.execute(query.order_by(Package.id).limit(limit))
    return [
        DuePackage(
            id=row.id,
            billing_account_id=row.billing_account_id,
            student_id=row.student_id,
            subject_id=row.subject_id,
            purchased_on=row.purchased_on,
            expires_on=row.expires_on,
        )
        for row in rows
    ]


async def expire_package(session: AsyncSession, package_id: uuid.UUID) -> ExpireOutcome:
    """F3 (ii): the account ``FOR UPDATE`` → an ``expire`` row exists →
    ``already_expired`` → remaining recomputed under the lock: 0 →
    ``nothing_left`` → one ``expire`` row for ``−remaining`` in a savepoint
    (a natural-key violation → ``already_expired``). The caller checked that
    the package is due. An unknown id is a :class:`ValueError`."""
    account_id: uuid.UUID | None = await session.scalar(
        select(Package.billing_account_id).where(Package.id == package_id)
    )
    if account_id is None:
        raise ValueError("unknown package")
    await lock_billing_account(session, account_id)
    row = (
        await session.execute(
            select(_remaining_column(), _has_expire_column()).where(
                Package.id == package_id
            )
        )
    ).one()
    remaining, expired = int(row[0]), bool(row[1])
    if expired:
        return "already_expired"
    if remaining <= 0:
        return "nothing_left"
    try:
        async with session.begin_nested():
            session.add(
                PackageCreditTransaction(
                    package_id=package_id, kind="expire", units=-remaining
                )
            )
    except IntegrityError as exc:
        if not _violates(exc, PACKAGE_EXPIRE_KEY):
            raise
        return "already_expired"
    return "expired"


# --- Reads (D5) ----------------------------------------------------------------------


@dataclass(frozen=True)
class PackageNames:
    """Unexecuted ``SELECT id, name`` of other modules (ADR-0002), joined
    as subqueries: ``students``, ``families`` and ``subjects``."""

    students: Select[*tuple[Any, ...]]
    families: Select[*tuple[Any, ...]]
    subjects: Select[*tuple[Any, ...]]


@dataclass(frozen=True)
class PackageRow:
    """One package with its names, remaining units and status (D5)."""

    id: uuid.UUID
    billing_account_id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    family_id: uuid.UUID
    family_name: str
    product_id: uuid.UUID
    product_name: str
    unit: PackageUnit
    units_granted: int
    remaining_units: int
    subject_id: uuid.UUID | None
    subject_name: str
    purchased_on: date
    expires_on: date
    status: PackageStatus
    purchase_charge_id: uuid.UUID | None


@dataclass(frozen=True)
class TransactionRow:
    """One unit movement (D5)."""

    id: uuid.UUID
    kind: str
    units: int
    class_session_id: uuid.UUID | None
    created_at: datetime


def _status_column(today: date) -> Any:
    """D4 in SQL (the twin of :func:`package_status`)."""
    return case(
        (or_(_has_expire_column(), Package.expires_on < today), literal("expired")),
        (_remaining_column() <= 0, literal("exhausted")),
        else_=literal("active"),
    )


def _named_packages(names: PackageNames) -> Select[Any]:
    students = names.students.subquery("package_students")
    families_ = names.families.subquery("package_families")
    subjects = names.subjects.subquery("package_subjects")
    purchase = (
        select(Charge.id)
        .where(Charge.kind == "package_purchase", Charge.package_id == Package.id)
        .correlate(Package)
        .scalar_subquery()
    )
    return (
        select(
            Package,
            students.c.name.label("student_name"),
            BillingAccount.family_id,
            families_.c.name.label("family_name"),
            PackageProduct.name.label("product_name"),
            subjects.c.name.label("subject_name"),
            _remaining_column().label("remaining"),
            _has_expire_column().label("has_expire"),
            purchase.label("purchase_charge_id"),
        )
        .join(BillingAccount, BillingAccount.id == Package.billing_account_id)
        .join(PackageProduct, PackageProduct.id == Package.package_product_id)
        .outerjoin(students, students.c.id == Package.student_id)
        .outerjoin(families_, families_.c.id == BillingAccount.family_id)
        .outerjoin(subjects, subjects.c.id == Package.subject_id)
    )


def _package_row(found: Any, today: date) -> PackageRow:
    package: Package = found[0]
    remaining = int(found.remaining)
    return PackageRow(
        id=package.id,
        billing_account_id=package.billing_account_id,
        student_id=package.student_id,
        student_name=found.student_name or "",
        family_id=found.family_id,
        family_name=found.family_name or "",
        product_id=package.package_product_id,
        product_name=found.product_name,
        unit=cast(PackageUnit, package.unit),
        units_granted=package.units_granted,
        remaining_units=remaining,
        subject_id=package.subject_id,
        subject_name=found.subject_name or "",
        purchased_on=package.purchased_on,
        expires_on=package.expires_on,
        status=package_status(
            remaining,
            package.expires_on,
            has_expire_row=bool(found.has_expire),
            today=today,
        ),
        purchase_charge_id=found.purchase_charge_id,
    )


async def list_packages(
    session: AsyncSession,
    names: PackageNames,
    *,
    today: date,
    student_id: uuid.UUID | None,
    family_id: uuid.UUID | None,
    statuses: Collection[PackageStatus],
    limit: int,
    offset: int,
) -> tuple[list[PackageRow], int]:
    """A page of packages by ``(expires_on, purchased_on, id)`` with the
    total (D5). The status is computed in SQL against ``today``, so a page
    is one statement (a window count), plus a count past the end."""
    conditions: list[ColumnElement[bool]] = []
    if student_id is not None:
        conditions.append(Package.student_id == student_id)
    if family_id is not None:
        conditions.append(BillingAccount.family_id == family_id)
    if statuses:
        conditions.append(_status_column(today).in_(sorted(set(statuses))))
    rows = (
        await session.execute(
            _named_packages(names)
            .add_columns(func.count().over().label("total"))
            .where(*conditions)
            .order_by(Package.expires_on, Package.purchased_on, Package.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if rows:
        return [_package_row(row, today) for row in rows], int(rows[0].total)
    total = await session.scalar(
        select(func.count(Package.id))
        .join(BillingAccount, BillingAccount.id == Package.billing_account_id)
        .where(*conditions)
    )
    return [], int(total or 0)


async def get_package(
    session: AsyncSession, names: PackageNames, package_id: uuid.UUID, *, today: date
) -> tuple[PackageRow, list[TransactionRow]]:
    """One package (``404`` when unknown) and its transactions, oldest
    first (D5). Two statements."""
    found = (
        await session.execute(_named_packages(names).where(Package.id == package_id))
    ).first()
    if found is None:
        raise RecordNotFoundError()
    rows = await session.scalars(
        select(PackageCreditTransaction)
        .where(PackageCreditTransaction.package_id == package_id)
        .order_by(PackageCreditTransaction.created_at, PackageCreditTransaction.id)
    )
    return _package_row(found, today), [
        TransactionRow(
            id=row.id,
            kind=row.kind,
            units=row.units,
            class_session_id=row.class_session_id,
            created_at=row.created_at,
        )
        for row in rows
    ]
