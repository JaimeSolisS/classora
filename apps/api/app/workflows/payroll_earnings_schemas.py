"""A tutor's earnings (#113 C2, C3): the shapes of the three reads. No internal
ids beyond the period's, the payment's and the payslip's, no actor names, no
payment note and no reversal reason (#113 E5, E6)."""

import uuid
from datetime import date

from pydantic import BaseModel, Field

from app.core.money import Money
from app.modules.payroll.schemas import PaidState, PayFrequency
from app.modules.payroll.service import PayslipLine, PayslipSummary


class PayslipReference(BaseModel):
    """A payslip to download (``…/payslips/{id}/pdf``)."""

    id: uuid.UUID
    number: str = Field(description="P-YYYY-NNNNNN.")


class EarningsPeriodItem(BaseModel):
    """One finalized period of the tutor (#113 C2)."""

    id: uuid.UUID
    starts_on: date
    ends_on: date
    pay_frequency: PayFrequency
    finalized_on: date = Field(description="The center-local date of finalize.")
    total: Money = Field(description="The period's finalized total.")
    paid_total: Money = Field(description="The payments without a reversal.")
    paid_state: PaidState = Field(
        description="unpaid, partially_paid or paid; a 0.00 total is paid."
    )
    last_paid_on: date | None = Field(
        description="The latest paid_on of a payment without a reversal."
    )
    line_count: int
    payslip: PayslipReference | None = Field(
        description=(
            "Null for a period with no lines, or one finalized before payslips existed."
        )
    )


class EarningsPayment(BaseModel):
    """A payment of the period, as the tutor sees it (#113 E5): no note, no
    reversal reason and nobody's name."""

    id: uuid.UUID
    paid_on: date
    amount: Money
    method_code: str
    reference: str
    reversed_on: date | None = Field(
        description=(
            "The center-local date of the reversal; a reversed payment doesn't"
            " count toward paid_total."
        )
    )


class EarningsPeriodDetail(BaseModel):
    """One finalized period with its lines, summary and payments (#113 C3). The
    lines are live, through the payslip's projection: only a pay item type
    renamed after finalize reads differently from the payslip."""

    period: EarningsPeriodItem
    lines: list[PayslipLine]
    summary: PayslipSummary
    total: Money
    payments: list[EarningsPayment] = Field(
        description="Newest paid_on first; reversed ones included."
    )
    payslip: PayslipReference | None
