"""Discounts and their assignments: reads and writes (#95 D; H2, H8, H10).

The routes are ``app.workflows.billing_discounts_router``; the workflow
(``app.workflows.billing_discounts``) checks the references in other
modules (subjects, levels, students, families), names the response and
passes the center's local ``today``. Every function runs in the caller's
Unit of Work and never commits.

- **Discounts** are configuration (audited, versioned) and are never
  deleted (H8): staff retire one with ``is_active = false``. ``kind`` and
  ``audience`` never change. An edit or a deactivation affects only charges
  posted afterwards: a posted charge keeps its ``charge_discounts``
  snapshot.
- **Names** are unique ignoring case (``409 discount_name_exists``: a check,
  and the unique index mapped by name for the concurrent case).
- **Status** against ``today``: ``inactive`` (``is_active`` false), else
  ``upcoming`` (``starts_on`` after today), ``expired`` (``expires_on``
  before today), else ``current``.
- **Assignments** (H2) exist only for ``assigned`` discounts (``422
  audience_not_assigned``); one per discount and student or family (``409
  discount_assignment_exists``, also the unique indexes). They are
  hard-deleted (nothing references them).
"""

import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import ColumnElement, Select, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.core.money import Money
from app.modules.billing.discounts import KIND_ORDER, DiscountEligibility
from app.modules.billing.errors import (
    AudienceNotAssignedError,
    BillingValidationError,
    DiscountAssignmentExistsError,
    DiscountNameExistsError,
)
from app.modules.billing.models import (
    DISCOUNT_ASSIGNMENT_FAMILY_KEY,
    DISCOUNT_ASSIGNMENT_STUDENT_KEY,
    DISCOUNT_NAME_INDEX,
    Discount,
    DiscountAssignment,
)
from app.modules.billing.schemas import (
    DiscountCreate,
    DiscountUpdate,
    discount_rule_errors,
)

type ReferenceCheck = Callable[[DiscountEligibility], Awaitable[list[FieldError]]]
"""The workflow's reference check of new criteria (subjects and levels)."""

DISCOUNT_STATUSES: Final = ("inactive", "upcoming", "current", "expired")


@dataclass(frozen=True)
class TargetNames:
    """Unexecuted ``SELECT id, name`` of students and of families (from
    ``families.service``), joined as subqueries."""

    students: Select[*tuple[Any, ...]]
    families: Select[*tuple[Any, ...]]


@dataclass(frozen=True)
class DiscountRow:
    """One discount with its status and its number of assignments."""

    id: uuid.UUID
    kind: str
    name: str
    audience: str
    percent: Decimal | None
    amount: Money | None
    eligibility: DiscountEligibility
    starts_on: date | None
    expires_on: date | None
    is_active: bool
    status: str
    assignment_count: int
    version: int
    created_at: datetime


@dataclass(frozen=True)
class AssignmentRow:
    """One assignment with its student's or family's current name."""

    id: uuid.UUID
    student_id: uuid.UUID | None
    student_name: str
    family_id: uuid.UUID | None
    family_name: str
    created_at: datetime


def _status(today: date) -> ColumnElement[str]:
    return case(
        (Discount.is_active.is_(False), "inactive"),
        (Discount.starts_on > today, "upcoming"),
        (Discount.expires_on < today, "expired"),
        else_="current",
    )


def _assignment_count() -> ColumnElement[int]:
    return (
        select(func.count(DiscountAssignment.id))
        .where(DiscountAssignment.discount_id == Discount.id)
        .scalar_subquery()
    )


def _discounts_query(today: date) -> Any:
    return select(
        Discount,
        _status(today).label("status"),
        _assignment_count().label("assignment_count"),
    )


def _row(found: Any) -> DiscountRow:
    discount: Discount = found[0]
    return DiscountRow(
        id=discount.id,
        kind=discount.kind,
        name=discount.name,
        audience=discount.audience,
        percent=discount.percent,
        amount=discount.amount,
        eligibility=DiscountEligibility.model_validate(discount.eligibility),
        starts_on=discount.starts_on,
        expires_on=discount.expires_on,
        is_active=discount.is_active,
        status=found.status,
        assignment_count=int(found.assignment_count),
        version=discount.version,
        created_at=discount.created_at,
    )


async def list_discounts(
    session: AsyncSession,
    *,
    kinds: Collection[str],
    statuses: Collection[str],
    q: str | None,
    today: date,
    limit: int,
    offset: int,
) -> tuple[list[DiscountRow], int]:
    """A page of discounts (D2), active first, then by kind (H4 order),
    name and id, with the total. One statement (the total is a window
    count; a count when the page is past the end)."""
    status = _status(today)
    conditions: list[ColumnElement[bool]] = []
    if kinds:
        conditions.append(Discount.kind.in_(set(kinds)))
    if statuses:
        conditions.append(status.in_(set(statuses)))
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(Discount.name.ilike(f"%{escaped}%", escape="\\"))
    kind_order = case(
        {kind: n for n, kind in enumerate(KIND_ORDER)}, value=Discount.kind
    )
    found = (
        await session.execute(
            _discounts_query(today)
            .add_columns(func.count().over().label("total"))
            .where(*conditions)
            .order_by(
                Discount.is_active.desc(),
                kind_order,
                func.lower(Discount.name),
                Discount.id,
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if found:
        return [_row(row) for row in found], int(found[0].total)
    total = await session.scalar(select(func.count(Discount.id)).where(*conditions))
    return [], int(total or 0)


async def get_discount(
    session: AsyncSession, discount_id: uuid.UUID, *, today: date
) -> DiscountRow:
    """One discount (``404 not_found``). One statement."""
    found = (
        await session.execute(_discounts_query(today).where(Discount.id == discount_id))
    ).first()
    if found is None:
        raise RecordNotFoundError()
    return _row(found)


async def list_assignments(
    session: AsyncSession, names: TargetNames, discount_id: uuid.UUID
) -> list[AssignmentRow]:
    """The discount's assignments by student or family name, then id. One
    statement."""
    students = names.students.subquery("student_names")
    families = names.families.subquery("family_names")
    rows = (
        await session.execute(
            select(
                DiscountAssignment,
                students.c.name.label("student_name"),
                families.c.name.label("family_name"),
            )
            .outerjoin(students, students.c.id == DiscountAssignment.student_id)
            .outerjoin(families, families.c.id == DiscountAssignment.family_id)
            .where(DiscountAssignment.discount_id == discount_id)
            .order_by(
                func.lower(func.coalesce(students.c.name, families.c.name)),
                DiscountAssignment.id,
            )
        )
    ).all()
    return [
        AssignmentRow(
            id=row[0].id,
            student_id=row[0].student_id,
            student_name=row.student_name or "",
            family_id=row[0].family_id,
            family_name=row.family_name or "",
            created_at=row[0].created_at,
        )
        for row in rows
    ]


# --- Writes --------------------------------------------------------------------------


async def _ensure_name_free(
    session: AsyncSession, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    query = select(Discount.id).where(func.lower(Discount.name) == name.lower())
    if except_id is not None:
        query = query.where(Discount.id != except_id)
    if await session.scalar(query.limit(1)) is not None:
        raise DiscountNameExistsError()


async def _flush(session: AsyncSession) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        if DISCOUNT_NAME_INDEX in str(exc.orig):
            raise DiscountNameExistsError() from None
        raise


def _percent(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


async def create_discount(
    session: AsyncSession, body: DiscountCreate, *, check_references: ReferenceCheck
) -> uuid.UUID:
    """A new discount (D3): the references of the criteria (one ``422``)
    → ``409 discount_name_exists`` → insert."""
    errors = await check_references(body.eligibility)
    if errors:
        raise BillingValidationError(errors)
    await _ensure_name_free(session, body.name)
    discount = Discount(
        kind=body.kind,
        audience=body.audience,
        name=body.name,
        percent=_percent(body.percent),
        amount=body.amount,
        eligibility=body.eligibility.stored(),
        starts_on=body.starts_on,
        expires_on=body.expires_on,
        is_active=body.is_active,
    )
    session.add(discount)
    await _flush(session)
    return discount.id


async def _locked(session: AsyncSession, discount_id: uuid.UUID) -> Discount:
    discount = await session.scalar(
        select(Discount)
        .where(Discount.id == discount_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if discount is None:
        raise RecordNotFoundError()
    return discount


async def update_discount(
    session: AsyncSession,
    discount_id: uuid.UUID,
    body: DiscountUpdate,
    *,
    check_references: ReferenceCheck,
) -> None:
    """Change a discount (D5): the row ``FOR UPDATE`` (``404``) → ``409
    version_conflict`` → the resulting row against the D3 rules and the new
    criteria's references (one ``422``) → ``409 discount_name_exists`` →
    the update. Nothing changed: no write, no event, no version bump."""
    discount = await _locked(session, discount_id)
    ensure_version(discount, body.version)
    sent = body.model_fields_set
    values: dict[str, Any] = {
        "name": discount.name,
        "percent": discount.percent,
        "amount": discount.amount,
        "eligibility": DiscountEligibility.model_validate(discount.eligibility),
        "starts_on": discount.starts_on,
        "expires_on": discount.expires_on,
        "is_active": discount.is_active,
    }
    for field in values:
        if field in sent:
            values[field] = getattr(body, field)
    values["percent"] = (
        _percent(body.percent) if "percent" in sent else values["percent"]
    )
    errors = discount_rule_errors(
        kind=discount.kind,
        audience=discount.audience,
        percent=values["percent"],
        amount=values["amount"],
        starts_on=values["starts_on"],
        expires_on=values["expires_on"],
        eligibility=values["eligibility"],
    )
    if "eligibility" in sent:
        errors.extend(await check_references(values["eligibility"]))
    if errors:
        raise BillingValidationError(errors)
    values["eligibility"] = values["eligibility"].stored()
    changed = {
        field: value
        for field, value in values.items()
        if getattr(discount, field) != value
    }
    if not changed:
        return
    if "name" in changed:
        await _ensure_name_free(session, values["name"], except_id=discount.id)
    for field, value in changed.items():
        setattr(discount, field, value)
    await _flush(session)


async def lock_for_assignment(session: AsyncSession, discount_id: uuid.UUID) -> None:
    """The discount row ``FOR SHARE`` before an assignment is added (D6):
    ``404``, then ``422 audience_not_assigned`` for an ``all`` discount."""
    audience = await session.scalar(
        select(Discount.audience)
        .where(Discount.id == discount_id)
        .with_for_update(read=True)
    )
    if audience is None:
        raise RecordNotFoundError()
    if audience != "assigned":
        raise AudienceNotAssignedError()


async def add_assignment(
    session: AsyncSession,
    discount_id: uuid.UUID,
    *,
    student_id: uuid.UUID | None,
    family_id: uuid.UUID | None,
) -> uuid.UUID:
    """Insert an assignment (after :func:`lock_for_assignment` and the
    workflow's reference check): ``409 discount_assignment_exists`` (a check,
    and the unique indexes for the concurrent case)."""
    target = (
        DiscountAssignment.student_id == student_id
        if student_id is not None
        else DiscountAssignment.family_id == family_id
    )
    existing = await session.scalar(
        select(DiscountAssignment.id).where(
            DiscountAssignment.discount_id == discount_id, target
        )
    )
    if existing is not None:
        raise DiscountAssignmentExistsError()
    assignment = DiscountAssignment(
        discount_id=discount_id, student_id=student_id, family_id=family_id
    )
    try:
        async with session.begin_nested():
            session.add(assignment)
            await session.flush()
    except IntegrityError as exc:
        message = str(exc.orig)
        if (
            DISCOUNT_ASSIGNMENT_STUDENT_KEY in message
            or DISCOUNT_ASSIGNMENT_FAMILY_KEY in message
        ):
            raise DiscountAssignmentExistsError() from None
        raise
    return assignment.id


async def delete_assignment(
    session: AsyncSession, discount_id: uuid.UUID, assignment_id: uuid.UUID
) -> None:
    """Delete an assignment of the discount (D7): ``404`` for an unknown
    discount or an assignment of another one. Posted charges keep their
    lines."""
    if (
        await session.scalar(select(Discount.id).where(Discount.id == discount_id))
        is None
    ):
        raise RecordNotFoundError()
    assignment = await session.scalar(
        select(DiscountAssignment).where(
            DiscountAssignment.id == assignment_id,
            DiscountAssignment.discount_id == discount_id,
        )
    )
    if assignment is None:
        raise RecordNotFoundError()
    await session.delete(assignment)
    await session.flush()
