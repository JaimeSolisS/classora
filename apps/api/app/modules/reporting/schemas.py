"""Financial report shapes (#117 E2, E9, E10). Money values are strings
(#87). No description, reference, note, reason, template name, tutor, student
or family is ever returned (#117 B3)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.core.money import Money
from app.modules.finance.schemas import ExpenseCategoryRef, VendorRef

type ReportBasis = Literal["accrual", "cash"]
type ReportClassType = Literal["individual", "group", "course", "unallocated"]


class ClassRow(BaseModel):
    """Accrual profitability of one class type (ADR-0017 E18)."""

    class_type: ReportClassType
    revenue: Money
    direct_tutor_cost: Money
    gross_profit: Money = Field(description="revenue - direct_tutor_cost.")


class CategoryAmount(BaseModel):
    """Operating expenses of one category: its **current** label (inactive
    categories included)."""

    category: ExpenseCategoryRef
    amount: Money


class VendorAmount(BaseModel):
    """Operating expenses of one vendor; ``vendor`` is ``null`` for the
    expenses with no vendor."""

    vendor: VendorRef | None
    amount: Money


class OperatingExpenses(BaseModel):
    """The range's operating expenses: accrual by ``incurred_on`` at their
    current net, cash by unreversed payment ``paid_on``. Only non-zero rows,
    by amount descending, then id."""

    total: Money
    by_category: list[CategoryAmount]
    by_vendor: list[VendorAmount]


class Profitability(BaseModel):
    """``GET /api/v1/reports/finance/profitability`` (#117 E2)."""

    basis: ReportBasis
    from_: date = Field(alias="from", serialization_alias="from")
    to: date
    today: date = Field(description="The center's today when it was computed.")
    class_rows: list[ClassRow] = Field(
        description="Accrual: individual, group, course, unallocated, in this"
        " order. Cash: empty (cash profitability is center-wide only)."
    )
    revenue_total: Money = Field(
        description="Accrual: recognized revenue. Cash: unreversed payments"
        " received minus refunds paid out."
    )
    direct_tutor_cost_total: Money = Field(
        description="Accrual: finalized payroll. Cash: unreversed payroll payments."
    )
    gross_profit: Money
    operating_expenses: OperatingExpenses
    net_result: Money = Field(description="gross_profit - operating_expenses.total.")

    model_config = {"populate_by_name": True}


class Receivables(BaseModel):
    """Σ open charges over every account, of which overdue (effective due
    date), and the account credit (shown, never netted)."""

    outstanding: Money
    overdue: Money
    account_credit: Money
    accounts_with_outstanding: int


class Payables(BaseModel):
    """Open non-void expenses (of which overdue) and unpaid finalized payroll."""

    expenses_open: Money
    expenses_overdue: Money
    payroll_unpaid: Money
    payroll_periods_unpaid: int
    total: Money = Field(description="expenses_open + payroll_unpaid.")


class DeferredRevenue(BaseModel):
    """What the accrual rules recognize after today, from current nets."""

    total: Money
    packages: Money
    courses: Money
    scheduled_sessions: Money
    other: Money


class Balances(BaseModel):
    """``GET /api/v1/reports/finance/balances`` (#117 E9): as of the center's
    today only."""

    today: date
    receivables: Receivables
    payables: Payables
    deferred_revenue: DeferredRevenue


__all__ = [
    "Balances",
    "CategoryAmount",
    "ClassRow",
    "DeferredRevenue",
    "OperatingExpenses",
    "Payables",
    "Profitability",
    "Receivables",
    "ReportBasis",
    "VendorAmount",
]
