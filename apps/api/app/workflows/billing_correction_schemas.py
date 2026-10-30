"""Correction, reversal and refund responses (#100 C1-C6; R8).

The request bodies live with the billing module's shapes
(``app.modules.billing.schemas``: ``ManualChargeCreate``,
``ManualAdjustmentCreate``, ``PaymentReversalCreate``, ``RefundCreate``,
``PackageCancellationCreate``). For a caller without ``billing.view`` (a
guardian) every ``reason`` and actor id is ``null`` (#364 D4).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.core.money import Money
from app.modules.billing.schemas import AdjustmentKind
from app.workflows.billing_account_schemas import ChargeItem
from app.workflows.billing_payment_schemas import (
    PaymentItem,
    PaymentReversalItem,
    RefundItem,
)


class AdjustmentItem(BaseModel):
    """One adjustment of a charge: a credit is negative, a debit positive.
    ``reason`` (staff text) and ``posted_by_user_id`` are ``null`` for a
    caller without billing.view; ``posted_by_user_id`` is also ``null`` for
    a job's adjustment."""

    id: uuid.UUID
    kind: AdjustmentKind
    amount: Money
    reason: str | None
    posted_at: datetime
    posted_by_user_id: uuid.UUID | None


class ChargeAllocationItem(BaseModel):
    """One allocation to the charge; ``is_valid`` is false once a reversal
    or a credit released it."""

    id: uuid.UUID
    payment_id: uuid.UUID
    amount: Money
    created_at: datetime
    is_valid: bool


class ChargeDetailItem(ChargeItem):
    """A charge with its adjustments and allocations, oldest first (#100
    C6, R8)."""

    adjustments: list[AdjustmentItem]
    allocations: list[ChargeAllocationItem]


class ManualChargePosted(BaseModel):
    """``201`` of a manual charge (#100 C1)."""

    charge: ChargeItem


class ManualAdjustmentPosted(BaseModel):
    """``201`` of a manual adjustment (#100 C2): the adjustment, the charge
    afterwards, the allocations the credit released (newest first; the
    money becomes account credit, E5) and the account credit afterwards."""

    adjustment: AdjustmentItem
    charge: ChargeItem
    released_allocation_ids: list[uuid.UUID]
    account_credit_after: Money


class ReleasedAllocationItem(BaseModel):
    """An allocation a reversal released (its charge reopens)."""

    allocation_id: uuid.UUID
    charge_id: uuid.UUID
    amount: Money


class PaymentReversed(BaseModel):
    """``201`` of a payment reversal (#100 C3): the reversal, the payment
    afterwards, the allocations released and the account credit afterwards
    (the payment's remainder no longer counts)."""

    reversal: PaymentReversalItem
    payment: PaymentItem
    released: list[ReleasedAllocationItem]
    account_credit_after: Money


class RefundRecorded(BaseModel):
    """``201`` of a refund (#100 C4): the refund, the payment afterwards and
    the account credit afterwards. Outstanding charges don't change."""

    refund: RefundItem
    payment: PaymentItem
    account_credit_after: Money
