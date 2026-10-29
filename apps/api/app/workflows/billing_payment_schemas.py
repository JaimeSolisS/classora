"""Payment responses (#96 C3-C6): the payment methods, a family's payments
with their allocations, and the results of recording a payment and of
allocating account credit.

For a caller without ``billing.view`` (a guardian) ``note``,
``recorded_by_user_id`` and each allocation's ``created_by_user_id`` are
``null`` (#96 M7, #364 D4); ``reference`` and ``payer_name`` stay visible
(the family gave them, and the receipt shows them).
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.core.money import Money


class PaymentMethodItem(BaseModel):
    """A payment method (#96 M8). Labels come from the client catalogs
    (``billing.paymentMethods.<code>``); only active ones are offered."""

    code: str
    is_active: bool


class PaymentItem(BaseModel):
    """A payment. ``unallocated_amount = amount − allocated_amount −
    refunded_amount`` (valid allocations only); it is account credit only
    while ``is_reversed`` is false. ``recorded_at`` is when it was
    entered; ``received_on`` the center-local day the money came in."""

    id: uuid.UUID
    amount: Money
    received_on: date
    payment_method_code: str
    reference: str | None
    payer_name: str | None
    note: str | None
    recorded_by_user_id: uuid.UUID | None
    recorded_at: datetime
    allocated_amount: Money
    refunded_amount: Money
    unallocated_amount: Money
    is_reversed: bool


class PaymentAllocationItem(BaseModel):
    """One allocation of a payment to a charge. ``is_valid`` is false once
    a reversal or a credit released it (#90 E5); ``created_by_user_id`` is
    ``null`` for a re-inserted remainder."""

    id: uuid.UUID
    charge_id: uuid.UUID
    student_id: uuid.UUID
    amount: Money
    created_at: datetime
    created_by_user_id: uuid.UUID | None
    is_valid: bool


class PaymentDetail(PaymentItem):
    """A payment with every allocation it has had, oldest first."""

    allocations: list[PaymentAllocationItem]


class RecordedAllocationItem(BaseModel):
    """An allocation written by the request."""

    id: uuid.UUID
    charge_id: uuid.UUID
    student_id: uuid.UUID
    amount: Money


class PaymentRecorded(BaseModel):
    """``201`` of recording a payment (#96 C3): the payment, the
    allocations written now, the part left as account credit and the
    account credit afterwards."""

    payment: PaymentItem
    allocations: list[RecordedAllocationItem]
    unallocated_amount: Money
    account_credit_after: Money


class CreditAllocationItem(RecordedAllocationItem):
    """An allocation of account credit, with the payment it came from."""

    payment_id: uuid.UUID


class CreditAllocated(BaseModel):
    """``201`` of allocating account credit (#96 C4): the rows written (one
    chosen charge may get several, one per source payment, oldest payment
    first), their total and the account credit afterwards."""

    allocations: list[CreditAllocationItem]
    allocated_total: Money
    account_credit_after: Money
