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
    version: int | None
    """The account's version, for ``PATCH …/billing-account`` (#97 C2);
    ``null`` without an account."""
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
    effective due date (the latest due-date change, else the posted one,
    #97); ``net = amount + adjustments_total``; ``open = net −
    allocated``. ``posted_by_user_id`` is ``null`` for a job-posted charge
    and for a caller without billing.view. At most one of the source ids is
    set (none for a manual charge)."""

    id: uuid.UUID
    student: BillingStudent
    kind: str
    service_date: date
    due_date: date
    original_due_date: date
    """The due date as posted (#97 B2): it differs from ``due_date`` once
    staff changed it."""
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


# --- Due dates and overdue accounts (#97) --------------------------------------------


class BillingAccountSettings(BaseModel):
    """The family's account settings after ``PATCH …/billing-account``
    (#97 C1). ``due_offset_days`` applies to charges posted afterwards."""

    family_id: uuid.UUID
    account_id: uuid.UUID
    due_offset_days: int
    version: int


class DueDateChangeItem(BaseModel):
    """One change of a charge's due date (#97 D2; staff only).
    ``previous_due_date`` is the effective due date before it."""

    id: uuid.UUID
    charge_id: uuid.UUID
    previous_due_date: date
    due_date: date
    reason: str
    changed_by_user_id: uuid.UUID
    created_at: datetime


class DueDateChangeRecorded(DueDateChangeItem):
    """A recorded change (#97 D1) with the charge's ``status`` after it."""

    status: ChargeStatus


class AgingBucketsItem(BaseModel):
    """Overdue open amounts by days past the effective due date (#97 E1):
    1–30, 31–60, 61–90 and more than 90 days, inclusive as named."""

    days_1_30: Money
    days_31_60: Money
    days_61_90: Money
    days_over_90: Money


class OverdueAccountItem(BaseModel):
    """One family account with overdue charges (#97 E1). ``overdue_total``
    = Σ buckets; ``days_overdue`` = today − ``oldest_due_date``.
    ``account_credit`` is shown but not subtracted from the overdue amounts
    (staff allocate it); ``net_balance = outstanding − account_credit``.
    ``last_reminded_at`` is the latest reminder sent for any of its
    charges."""

    family: BillingFamily
    account_id: uuid.UUID
    overdue_total: Money
    overdue_charges: int
    oldest_due_date: date
    days_overdue: int
    buckets: AgingBucketsItem
    outstanding: Money
    account_credit: Money
    net_balance: Money
    last_reminded_at: datetime | None


class OverdueTotalsItem(BaseModel):
    """The totals over every account that matches the filters (all pages)."""

    accounts: int
    overdue_total: Money
    buckets: AgingBucketsItem


class OverdueAccountsPage(BaseModel):
    """``GET /api/v1/billing/overdue-accounts`` (#97 E1): a page of
    accounts, the total count, the filtered totals and the center-local
    ``today`` the amounts were computed for."""

    items: list[OverdueAccountItem]
    total: int
    limit: int
    offset: int
    today: date
    totals: OverdueTotalsItem
