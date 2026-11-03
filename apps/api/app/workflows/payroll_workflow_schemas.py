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


class PayrollPeriodReview(BaseModel):
    """``GET /api/v1/payroll/periods/{id}/lines``. ``total`` is the sum of the
    rounded lines, and may be negative while the period is open."""

    period: PayrollPeriodDetail
    lines: list[PayrollLineItem]
    total: Money
