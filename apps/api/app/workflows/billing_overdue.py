"""Overdue accounts across families (#97 E1, E2; N7 (a); spec §6 "track
pending, partially paid and overdue amounts"): the cross-module read behind
``app.workflows.billing_overdue_router``.

The billing module computes the per-account aggregate
(``billing.service.overdue_accounts``: counted charges, aging buckets,
outstanding, account credit, last reminder); the families module gives the
families' names and active flags, filtered by status
(``families.service.family_status_columns``, an unexecuted select joined
into the same statement); administration the center's local today. This
workflow joins them (ADR-0002).

Statements: the center's today, then one per page (two when the page is
past the end), whatever the number of accounts or charges. No lock; reads
never write.
"""

from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import OffsetParams
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.families import service as families
from app.modules.families.schemas import FamilyStatus
from app.workflows.billing_account_schemas import (
    AgingBucketsItem,
    BillingFamily,
    OverdueAccountItem,
    OverdueAccountsPage,
    OverdueTotalsItem,
)

MIN_DAYS_OVERDUE: Final = 1
MAX_DAYS_OVERDUE: Final = 3650


def _buckets(buckets: billing.AgingBuckets) -> AgingBucketsItem:
    return AgingBucketsItem(
        days_1_30=buckets.days_1_30,
        days_31_60=buckets.days_31_60,
        days_61_90=buckets.days_61_90,
        days_over_90=buckets.days_over_90,
    )


async def list_overdue_accounts(
    session: AsyncSession,
    *,
    min_days_overdue: int,
    family_status: FamilyStatus,
    page: OffsetParams,
) -> OverdueAccountsPage:
    """``GET /api/v1/billing/overdue-accounts`` (#97 E1): the accounts with
    a charge at least ``min_days_overdue`` days past its effective due
    date, most overdue first."""
    today = await administration.center_today(session)
    found = await billing.overdue_accounts(
        session,
        families=families.family_status_columns(family_status),
        today=today,
        min_days=min_days_overdue,
        page=page,
    )
    return OverdueAccountsPage(
        items=[
            OverdueAccountItem(
                family=BillingFamily(
                    id=row.family_id,
                    name=row.family_name,
                    is_active=row.family_is_active,
                ),
                account_id=row.account_id,
                overdue_total=row.overdue_total,
                overdue_charges=row.overdue_charges,
                oldest_due_date=row.oldest_due_date,
                days_overdue=row.days_overdue,
                buckets=_buckets(row.buckets),
                outstanding=row.outstanding,
                account_credit=row.account_credit,
                net_balance=row.net_balance,
                last_reminded_at=row.last_reminded_at,
            )
            for row in found.items
        ],
        total=found.totals.accounts,
        limit=page.limit,
        offset=page.offset,
        today=today,
        totals=OverdueTotalsItem(
            accounts=found.totals.accounts,
            overdue_total=found.totals.overdue_total,
            buckets=_buckets(found.totals.buckets),
        ),
    )
