"""Billing factories: catalog items and their versions (#89), and the
ledger (#90).

:func:`persist_service_price` writes the rows directly (flush, never
commit), so a test can build any catalog the CHECKs allow, ``package`` and
``per_period`` rows included; the routes are tested through the API.

Ledger (#90): :func:`billing_account` and :func:`charge` go through the
posting primitives (``ensure_billing_account``, ``post_charge``).
:func:`payment`, :func:`allocation`, :func:`reversal` (which also releases
the payment's valid allocations, as #100 will) and :func:`refund` insert
ORM rows directly, so a test can build any ledger state; #96's
``record_payment`` / ``allocate_account_credit`` are tested through their
routes and primitives. Every helper flushes and never commits.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.discounts import DiscountEligibility
from app.modules.billing.models import (
    AllocationRelease,
    CustomPrice,
    Discount,
    DiscountAssignment,
    Payment,
    PaymentAllocation,
    PaymentReversal,
    PriceVersion,
    Refund,
    ServicePrice,
)
from app.modules.billing.schemas import ChargeDraft
from tests.factories.families import FamilyFactory, add_to_family, persist_student
from tests.factories.identity import persist_user


async def persist_service_price(
    session: AsyncSession,
    *,
    subject_id: uuid.UUID | None,
    class_type: str = "individual",
    pricing_model: str = "per_session",
    education_level_id: uuid.UUID | None = None,
    duration_minutes: int | None = None,
    cohort_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    versions: Sequence[tuple[date, str | None]] = (),
    period_frequency: str | None = None,
) -> ServicePrice:
    """An item and its ``versions`` (``(effective_from, amount)``, ``None``
    = not offered). A ``per_period`` item gets ``period_frequency``
    (``monthly`` unless given, #93 K2). Flushes, never commits."""
    if pricing_model == "per_period" and period_frequency is None:
        period_frequency = "monthly"
    item = ServicePrice(
        class_type=class_type,
        pricing_model=pricing_model,
        subject_id=subject_id,
        education_level_id=education_level_id,
        duration_minutes=duration_minutes,
        cohort_id=cohort_id,
        group_id=group_id,
        period_frequency=period_frequency,
    )
    session.add(item)
    await session.flush()
    session.add_all(
        PriceVersion(
            service_price_id=item.id,
            effective_from=effective_from,
            amount=None if amount is None else Money(amount),
        )
        for effective_from, amount in versions
    )
    await session.flush()
    return item


# --- Ledger (#90) --------------------------------------------------------------------

LEDGER_DATE = date(2031, 3, 10)


async def family_with_students(
    session: AsyncSession, students: int = 1
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A new family and ``students`` new students in it."""
    family = FamilyFactory.build()
    session.add(family)
    await session.flush()
    members = [await persist_student(session, contacts=0) for _ in range(students)]
    if members:
        await add_to_family(session, family, *members)
    return family.id, [m.id for m in members]


async def billing_account(
    session: AsyncSession, family_id: uuid.UUID | None = None
) -> uuid.UUID:
    """The account of ``family_id`` (a new family when ``None``)."""
    if family_id is None:
        family_id, _ = await family_with_students(session, 0)
    return await billing.ensure_billing_account(session, family_id)


def manual_draft(
    student_id: uuid.UUID,
    amount: str = "500.00",
    *,
    service_date: date = LEDGER_DATE,
    due: date | None = None,
    **overrides: Any,
) -> ChargeDraft:
    """A ``manual`` charge draft (no natural key, so any number per
    student)."""
    values: dict[str, Any] = {
        "student_id": student_id,
        "kind": "manual",
        "service_date": service_date,
        "list_amount": Money(amount),
        "due_date_base": due if due is not None else service_date,
        "description": "Cargo de prueba",
        "apply_family_offset": False,
    }
    values.update(overrides)
    return ChargeDraft(**values)


async def charge(
    session: AsyncSession,
    student_id: uuid.UUID,
    amount: str = "500.00",
    **overrides: Any,
) -> billing.PostedCharge:
    """A ``manual`` charge posted through ``post_charge``."""
    return await billing.post_charge(
        session, manual_draft(student_id, amount, **overrides)
    )


async def staff_user_id(session: AsyncSession) -> uuid.UUID:
    """A user to record payments with (no role needed by the table)."""
    return (await persist_user(session)).id


async def payment(
    session: AsyncSession,
    account_id: uuid.UUID,
    amount: str,
    *,
    recorded_by: uuid.UUID,
    method: str = "cash",
    received_on: date = LEDGER_DATE,
    **overrides: Any,
) -> Payment:
    row = Payment(
        billing_account_id=account_id,
        amount=Money(amount),
        received_on=received_on,
        payment_method_code=method,
        recorded_by_user_id=recorded_by,
        **overrides,
    )
    session.add(row)
    await session.flush()
    return row


async def allocation(
    session: AsyncSession, paid: Payment, charge_id: uuid.UUID, amount: str
) -> PaymentAllocation:
    row = PaymentAllocation(
        payment_id=paid.id,
        charge_id=charge_id,
        billing_account_id=paid.billing_account_id,
        amount=Money(amount),
        created_by_user_id=paid.recorded_by_user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def reversal(
    session: AsyncSession, paid: Payment, *, reason: str = "Pago rebotado"
) -> PaymentReversal:
    """A reversal of ``paid`` that releases its valid allocations (E5)."""
    row = PaymentReversal(
        payment_id=paid.id,
        billing_account_id=paid.billing_account_id,
        reason=reason,
        reversed_by_user_id=paid.recorded_by_user_id,
    )
    session.add(row)
    await session.flush()
    released = select(AllocationRelease.id).where(
        AllocationRelease.allocation_id == PaymentAllocation.id
    )
    valid = await session.scalars(
        select(PaymentAllocation.id).where(
            PaymentAllocation.payment_id == paid.id, ~released.exists()
        )
    )
    session.add_all(
        AllocationRelease(
            allocation_id=allocation_id,
            payment_reversal_id=row.id,
            released_by_user_id=paid.recorded_by_user_id,
        )
        for allocation_id in list(valid)
    )
    await session.flush()
    return row


async def refund(
    session: AsyncSession,
    paid: Payment,
    amount: str,
    *,
    reason: str = "Devolución de saldo",
    method: str = "cash",
) -> Refund:
    row = Refund(
        payment_id=paid.id,
        billing_account_id=paid.billing_account_id,
        amount=Money(amount),
        refunded_on=LEDGER_DATE,
        payment_method_code=method,
        reason=reason,
        refunded_by_user_id=paid.recorded_by_user_id,
    )
    session.add(row)
    await session.flush()
    return row


# --- Discounts and custom prices (#95) -----------------------------------------------


async def persist_discount(
    session: AsyncSession,
    *,
    kind: str = "discount",
    audience: str = "all",
    name: str | None = None,
    percent: str | None = None,
    amount: str | None = None,
    eligibility: dict[str, Any] | None = None,
    starts_on: date | None = None,
    expires_on: date | None = None,
    is_active: bool = True,
) -> Discount:
    """A discount row (no reference checks; a unique name by default).
    Tests in a shared database restrict ``all`` discounts to their own
    subject (``eligibility={"subject_ids": [...]}``) so they never reach
    another test's charges. Flushes, never commits."""
    discount = Discount(
        kind=kind,
        audience=audience,
        name=name or f"{kind} {uuid.uuid4().hex[:8]}",
        percent=None if percent is None else Decimal(percent),
        amount=None if amount is None else Money(amount),
        eligibility=DiscountEligibility.model_validate(eligibility or {}).stored(),
        starts_on=starts_on,
        expires_on=expires_on,
        is_active=is_active,
    )
    session.add(discount)
    await session.flush()
    return discount


async def assign_discount(
    session: AsyncSession,
    discount_id: uuid.UUID,
    *,
    student_id: uuid.UUID | None = None,
    family_id: uuid.UUID | None = None,
) -> DiscountAssignment:
    """An assignment row. Flushes, never commits."""
    assignment = DiscountAssignment(
        discount_id=discount_id, student_id=student_id, family_id=family_id
    )
    session.add(assignment)
    await session.flush()
    return assignment


async def persist_custom_price(
    session: AsyncSession,
    *,
    service_price_id: uuid.UUID,
    amount: str,
    effective_from: date,
    expires_on: date | None = None,
    student_id: uuid.UUID | None = None,
    family_id: uuid.UUID | None = None,
) -> CustomPrice:
    """A custom price row (any dates: the no-backdating rule is the
    route's). Flushes, never commits."""
    custom = CustomPrice(
        service_price_id=service_price_id,
        student_id=student_id,
        family_id=family_id,
        effective_from=effective_from,
        expires_on=expires_on,
        amount=Money(amount),
    )
    session.add(custom)
    await session.flush()
    return custom
