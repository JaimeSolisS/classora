"""Billing factories (#89): catalog items and their versions.

:func:`persist_service_price` writes the rows directly (flush, never
commit), so a test can build any catalog the CHECKs allow, ``package`` and
``per_period`` rows included; the routes are tested through the API.
"""

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing.models import PriceVersion, ServicePrice


async def persist_service_price(
    session: AsyncSession,
    *,
    subject_id: uuid.UUID | None,
    class_type: str = "individual",
    pricing_model: str = "per_session",
    education_level_id: uuid.UUID | None = None,
    duration_minutes: int | None = None,
    cohort_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    versions: Sequence[tuple[date, str | None]] = (),
) -> ServicePrice:
    """An item and its ``versions`` (``(effective_from, amount)``, ``None``
    = not offered). Flushes, never commits."""
    item = ServicePrice(
        class_type=class_type,
        pricing_model=pricing_model,
        subject_id=subject_id,
        education_level_id=education_level_id,
        duration_minutes=duration_minutes,
        cohort_id=cohort_id,
        group_id=group_id,
    )
    session.add(item)
    await session.flush()
    session.add_all(
        PriceVersion(
            service_price_id=item.id,
            effective_from=effective_from,
            amount=None if amount is None else Money(amount),
        )
        for effective_from, amount in versions
    )
    await session.flush()
    return item
