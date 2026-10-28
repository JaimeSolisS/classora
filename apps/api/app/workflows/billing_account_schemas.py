"""Billing account read responses (#364 B; #90b F1 (a)): a family's
balance, its charges and its ledger, with the family's and the students'
names.

The actor ids (``posted_by_user_id``) are ``null`` for a caller without
``billing.view`` (a guardian); the shapes are otherwise the same for staff
and guardians.
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.core.money import Money
from app.modules.billing.schemas import ChargeStatus, LedgerEntryType


class BillingFamily(BaseModel):
    """The family the account belongs to."""

    id: uuid.UUID
    name: str
    is_active: bool


class BillingStudent(BaseModel):
    """A student of the account: id and display name."""

    id: uuid.UUID
    name: str


class StudentBalanceItem(BaseModel):
    """One student's open amounts on the account. Account credit is
    account-level only."""

    student: BillingStudent
    outstanding: Money
    pending: Money
    partially_paid: Money
    overdue: Money


class BillingAccountSummary(BaseModel):
    """A family's billing account (#364 B1). ``account_id`` is ``null`` (and
    every amount zero) while the family has no account yet. ``outstanding =
    pending + partially_paid + overdue``; ``net_balance = outstanding −
    account_credit``. ``students``: every student with a charge on the
    account and every current member of the family, by name then id.
    ``today`` is the center-local date the statuses were computed for."""

    family: BillingFamily
    account_id: uuid.UUID | None
    due_offset_days: int
    outstanding: Money
    account_credit: Money
    net_balance: Money
    pending: Money
    partially_paid: Money
    overdue: Money
    students: list[StudentBalanceItem]
    today: date


class ChargeItem(BaseModel):
    """One charge with its derived values (#364 B2). ``due_date`` is the
    effective due date; ``net = amount + adjustments_total``; ``open = net −
    allocated``. ``posted_by_user_id`` is ``null`` for a job-posted charge
    and for a caller without billing.view. At most one of the source ids is
    set (none for a manual charge)."""

    id: uuid.UUID
    student: BillingStudent
    kind: str
    service_date: date
    due_date: date
    description: str | None
    list_amount: Money
    discount_amount: Money
    amount: Money
    adjustments_total: Money
    net: Money
    allocated: Money
    open: Money
    status: ChargeStatus
    posted_at: datetime
    posted_by_user_id: uuid.UUID | None
    class_session_id: uuid.UUID | None
    group_enrollment_id: uuid.UUID | None
    cohort_enrollment_id: uuid.UUID | None


class LedgerEntryItem(BaseModel):
    """One ledger row (#364 B3; the ``billing_ledger_entries`` view):
    ``balance_effect`` is its effect on the net balance; ``kind`` is the
    charge or adjustment kind, or the payment or refund method; payment-level
    rows have no ``student_id``."""

    entry_type: LedgerEntryType
    entry_id: uuid.UUID
    kind: str | None
    student_id: uuid.UUID | None
    charge_id: uuid.UUID | None
    payment_id: uuid.UUID | None
    amount: Money
    balance_effect: Money
    occurred_at: datetime
