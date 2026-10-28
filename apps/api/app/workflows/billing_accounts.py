"""Billing account reads (#364 B; #90b F1 (a); spec §6, W4.4, W4.6; #86
E4, E5, E8): the cross-module reads behind
``app.workflows.billing_accounts_router``.

The billing module owns the ledger and the derived balances
(``billing.service``); the families module owns the family, its members
and the guardian's own family (``families.service``). This workflow joins
them (ADR-0002).

**Record policy** (F1 (a); the ``/api/v1/groups`` precedent, #56): a
caller holding ``billing.view`` (Owner, Administrator, Finance Staff; not
delegable) sees every family's account. A caller holding ``guardian`` sees
only the family of their **active** guardian profile (``family_guardians``,
the #172 rule, loaded on every request): billing follows the family, not
the ``student_guardians`` links (ADR-0007 D17, #86 E4). Anyone else, and a
family that doesn't exist, gets the same ``404 not_found``. A caller
without ``billing.view`` sees the same shapes with the actor ids
(``posted_by_user_id``) ``null``.

**Reads never write:** a family without an account yet answers with
``account_id: null`` and zeros (empty pages for the lists); no account is
created and no lock is taken (B4).

Statements: the summary at most seven whatever the number of charges (the
guardian's own family, the family with its students, the account, the
center's today, the charges, the account credit, the students' names);
the charge list at most seven; the ledger at most four per page.
"""

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError
from app.core.money import Money
from app.core.pagination import (
    CursorPage,
    CursorParams,
    InvalidCursorError,
    OffsetPage,
    OffsetParams,
)
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.families import service as families
from app.workflows.billing_account_schemas import (
    BillingAccountSummary,
    BillingFamily,
    BillingStudent,
    ChargeItem,
    LedgerEntryItem,
    StudentBalanceItem,
)

BILLING_VIEW: Final = "billing.view"


def sees_every_account(principal: Principal) -> bool:
    """Whether the caller sees every family's account and the actor ids
    (``billing.view``)."""
    return principal.has(BILLING_VIEW)


async def _visible_family(
    session: AsyncSession, principal: Principal, family_id: uuid.UUID
) -> families.FamilyRecordReference:
    """The family, or ``404 not_found`` when it is missing or hidden: one
    statement for staff with ``billing.view``, two for a guardian (their own
    family first), none more for anyone else."""
    if not sees_every_account(principal):
        own = await families.own_family_id(session, principal)
        if own is None or own != family_id:
            raise RecordNotFoundError()
    family = await families.family_record_reference(session, family_id)
    if family is None:
        raise RecordNotFoundError()
    return family


async def _students(
    session: AsyncSession, ids: set[uuid.UUID]
) -> dict[uuid.UUID, BillingStudent]:
    references = await families.student_references(session, ids)
    return {
        student_id: BillingStudent(id=student_id, name=reference.display_name)
        for student_id, reference in references.items()
    }


def _student(
    students: dict[uuid.UUID, BillingStudent], student_id: uuid.UUID
) -> BillingStudent:
    # Every charge's student exists (a foreign key), so the fallback is
    # never used in practice.
    return students.get(student_id) or BillingStudent(id=student_id, name="")


async def get_summary(
    session: AsyncSession, principal: Principal, family_id: uuid.UUID
) -> BillingAccountSummary:
    """The family's balance (B1)."""
    family = await _visible_family(session, principal, family_id)
    today = await administration.center_today(session)
    account = await billing.account_of_family(session, family_id)
    balance = (
        None
        if account is None
        else await billing.account_balance(session, account.id, today=today)
    )
    per_student = (
        {} if balance is None else {row.student_id: row for row in balance.by_student}
    )
    ids = set(per_student) | set(family.student_ids)
    students = await _students(session, ids)
    zero = Money.zero()
    items = []
    for student_id in ids:
        row = per_student.get(student_id)
        items.append(
            StudentBalanceItem(
                student=_student(students, student_id),
                outstanding=zero if row is None else row.outstanding,
                pending=zero if row is None else row.pending,
                partially_paid=zero if row is None else row.partially_paid,
                overdue=zero if row is None else row.overdue,
            )
        )
    items.sort(key=lambda item: (item.student.name.casefold(), item.student.id))
    return BillingAccountSummary(
        family=BillingFamily(
            id=family.id, name=family.name, is_active=family.is_active
        ),
        account_id=None if account is None else account.id,
        due_offset_days=0 if account is None else account.due_offset_days,
        outstanding=zero if balance is None else balance.outstanding,
        account_credit=zero if balance is None else balance.account_credit,
        net_balance=zero if balance is None else balance.net_balance,
        pending=zero if balance is None else balance.pending,
        partially_paid=zero if balance is None else balance.partially_paid,
        overdue=zero if balance is None else balance.overdue,
        students=items,
        today=today,
    )


async def list_charges(
    session: AsyncSession,
    principal: Principal,
    family_id: uuid.UUID,
    *,
    page: OffsetParams,
    student_id: uuid.UUID | None,
    statuses: list[billing.ChargeStatus] | None,
) -> OffsetPage[ChargeItem]:
    """The family's charges (B2), newest first. A student with no charge on
    the account (another family's included) gives an empty page."""
    await _visible_family(session, principal, family_id)
    account = await billing.account_of_family(session, family_id)
    if account is None:
        return OffsetPage[ChargeItem](
            items=[], total=0, limit=page.limit, offset=page.offset
        )
    found = await billing.charge_details(
        session, account.id, page=page, student_id=student_id, statuses=statuses
    )
    students = await _students(session, {c.student_id for c in found.items})
    staff = sees_every_account(principal)
    return OffsetPage[ChargeItem](
        items=[
            ChargeItem(
                id=c.charge_id,
                student=_student(students, c.student_id),
                kind=c.kind,
                service_date=c.service_date,
                due_date=c.due_date,
                description=c.description,
                list_amount=c.list_amount,
                discount_amount=c.discount_amount,
                amount=c.amount,
                adjustments_total=c.adjustments_total,
                net=c.net,
                allocated=c.allocated,
                open=c.open,
                status=c.status,
                posted_at=c.posted_at,
                posted_by_user_id=c.posted_by_user_id if staff else None,
                class_session_id=c.class_session_id,
                group_enrollment_id=c.group_enrollment_id,
                cohort_enrollment_id=c.cohort_enrollment_id,
            )
            for c in found.items
        ],
        total=found.total,
        limit=page.limit,
        offset=page.offset,
    )


async def list_ledger(
    session: AsyncSession,
    principal: Principal,
    family_id: uuid.UUID,
    *,
    params: CursorParams,
    student_id: uuid.UUID | None,
) -> CursorPage[LedgerEntryItem]:
    """The family's ledger history (B3), newest first. A family without an
    account has no rows, so any cursor is ``400 invalid_cursor`` there (no
    cursor was ever issued for it)."""
    await _visible_family(session, principal, family_id)
    account = await billing.account_of_family(session, family_id)
    if account is None:
        if params.cursor is not None:
            raise InvalidCursorError()
        return CursorPage[LedgerEntryItem](items=[], next_cursor=None)
    found = await billing.ledger_entries(
        session, account.id, params=params, student_id=student_id
    )
    return CursorPage[LedgerEntryItem](
        items=[
            LedgerEntryItem(
                entry_type=e.entry_type,
                entry_id=e.entry_id,
                kind=e.kind,
                student_id=e.student_id,
                charge_id=e.charge_id,
                payment_id=e.payment_id,
                amount=e.amount,
                balance_effect=e.balance_effect,
                occurred_at=e.occurred_at,
            )
            for e in found.items
        ],
        next_cursor=found.next_cursor,
    )
