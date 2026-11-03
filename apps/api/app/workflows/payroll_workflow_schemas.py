"""Payroll period reviews (#110 F): the module's shapes, with the names and the
tutor's state that come from ``identity``, and the count from ``academics`` and
``scheduling``."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.core.money import Money
from app.modules.administration.schemas import AccountName
from app.modules.identity.schemas import UserState
from app.modules.payroll.schemas import (
    PaidState,
    PayFrequency,
    PayrollLineItem,
    PayrollPeriodSummary,
    PeriodStatus,
)


class PayrollPeriodItem(BaseModel):
    """One row of the period list (#110 F1)."""

    id: uuid.UUID
    tutor_user_id: uuid.UUID
    tutor_display_name: str
    tutor_state: UserState
    starts_on: date
    ends_on: date
    pay_frequency: PayFrequency
    status: PeriodStatus
    line_count: int
    total: Money | None = Field(
        description=(
            "All the period's lines. Null when they sum beyond the money range:"
            " the period's own read says what to do."
        )
    )
    prepared_at: datetime | None
    finalized_at: datetime | None
    paid_total: Money | None = Field(
        description=(
            "The payments without a reversal (#112). Null unless the period is"
            " finalized."
        )
    )
    paid_state: PaidState | None = Field(
        description=(
            "unpaid, partially_paid or paid; a 0.00 total is paid. Null unless the"
            " period is finalized."
        )
    )


class PayrollPeriodDetail(PayrollPeriodSummary):
    """The head of a period's review (#110 F3)."""

    tutor_display_name: str
    tutor_state: UserState
    prepared_at: datetime | None
    prepared_by: AccountName | None
    finalized_at: datetime | None
    finalized_by: AccountName | None
    total_amount: Money | None
    returned_at: datetime | None
    returned_by: AccountName | None
    return_reason: str | None
    earlier_unfinalized_period_id: uuid.UUID | None = Field(
        description="The tutor's oldest earlier period that isn't finalized."
    )
    pending_closeouts: int = Field(
        description=(
            "The tutor's sessions starting in the period with no approved"
            " closeout, cancelled or skipped ones aside. A warning, never a block."
        )
    )
    paid_total: Money | None = Field(
        description=(
            "The payments without a reversal (#112). Null unless the period is"
            " finalized."
        )
    )
    paid_state: PaidState | None = Field(
        description=(
            "unpaid, partially_paid or paid; a 0.00 total is paid. Null unless the"
            " period is finalized."
        )
    )


class PayrollPeriodReview(BaseModel):
    """``GET /api/v1/payroll/periods/{id}/lines``. ``total`` is the sum of the
    rounded lines, and may be negative while the period is open."""

    period: PayrollPeriodDetail
    lines: list[PayrollLineItem]
    total: Money


class PayrollPaymentReversalItem(BaseModel):
    """A payment's reversal (#112 D1)."""

    reason: str
    reversed_by: AccountName
    created_at: datetime


class PayrollPaymentItem(BaseModel):
    """One payment of ``GET /api/v1/payroll/periods/{id}/payments`` (#112 D1).
    A reversed payment stays listed with its ``reversal``, and counts for
    nothing."""

    id: uuid.UUID
    paid_on: date
    amount: Money
    method_code: str
    reference: str
    note: str | None
    recorded_by: AccountName
    created_at: datetime
    reversal: PayrollPaymentReversalItem | None
