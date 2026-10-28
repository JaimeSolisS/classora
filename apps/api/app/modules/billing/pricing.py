"""The catalog price lookup (#89 C; #86 E9; data-model §3.5).

:func:`lookup_price` answers "which catalog price applies to this service on
this local date". The billing issues that post charges call it through
``app.modules.billing.service`` (#91 sessions, #92 enrollments); #95 adds
the custom student or family price as a step **above** it (precedence step
1) without changing these ranks.

**Candidates** are the items with the query's class type, pricing model and
subject, ranked (lower wins; #89 E4):

- **0:** the item scoped to the query's cohort or group (only when the
  query has a scope; scoped items have no level and no duration);
- **1:** exact level and exact duration;
- **2:** exact level and any duration;
- **3:** any level and exact duration;
- **4:** any level and any duration.

A ``None`` query level matches only the any-level items (ranks 1 and 2 are
skipped), and a ``None`` query duration only the any-duration items (ranks
1 and 3).

**The most specific candidate that has a version in effect on ``on``
decides.** The version in effect is the one with the greatest
``effective_from <= on``; a candidate whose versions all start later (or
that has none) is skipped. If the deciding version's amount is ``NULL``
("not offered"), the result is ``None``: the lookup does **not** fall
through to a less specific item, so a mistake never charges a fallback
price silently. No candidate at all is also ``None`` (the caller's
``no_price``).

**Historical truth** (spec §6, §16): the function reads only the catalog.
A posted charge stores its ``price_version_id`` and its amount (#90, #91),
so a new version never changes a charge that was already posted.

``on`` is a local calendar date: the caller converts an instant with the
center zone (ADR-0004). One ``SELECT`` in the caller's Unit of Work; no
lock, no commit.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, and_, case, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import PriceVersion, ServicePrice
from app.modules.billing.schemas import PriceInEffect

PACKAGE = "package"


def _unscoped() -> ColumnElement[bool]:
    return and_(ServicePrice.cohort_id.is_(None), ServicePrice.group_id.is_(None))


def _rank_conditions(
    *,
    education_level_id: uuid.UUID | None,
    duration_minutes: int | None,
    cohort_id: uuid.UUID | None,
    group_id: uuid.UUID | None,
) -> list[tuple[ColumnElement[bool], int]]:
    level, duration = ServicePrice.education_level_id, ServicePrice.duration_minutes
    ranks: list[tuple[ColumnElement[bool], int]] = []
    if cohort_id is not None:
        ranks.append((ServicePrice.cohort_id == cohort_id, 0))
    if group_id is not None:
        ranks.append((ServicePrice.group_id == group_id, 0))
    if education_level_id is not None and duration_minutes is not None:
        ranks.append(
            (
                and_(
                    _unscoped(),
                    level == education_level_id,
                    duration == duration_minutes,
                ),
                1,
            )
        )
    if education_level_id is not None:
        ranks.append(
            (and_(_unscoped(), level == education_level_id, duration.is_(None)), 2)
        )
    if duration_minutes is not None:
        ranks.append(
            (and_(_unscoped(), level.is_(None), duration == duration_minutes), 3)
        )
    ranks.append((and_(_unscoped(), level.is_(None), duration.is_(None)), 4))
    return ranks


def _check_arguments(
    *,
    class_type: str,
    pricing_model: str,
    cohort_id: uuid.UUID | None,
    group_id: uuid.UUID | None,
) -> None:
    if pricing_model == PACKAGE:
        raise ValueError("package prices are read through their product (#94)")
    if cohort_id is not None and group_id is not None:
        raise ValueError("give a cohort_id or a group_id, not both")
    if cohort_id is not None and class_type != "course":
        raise ValueError("cohort_id is only for class_type 'course'")
    if group_id is not None and class_type != "group":
        raise ValueError("group_id is only for class_type 'group'")


async def lookup_price(
    session: AsyncSession,
    *,
    class_type: str,
    pricing_model: str,
    subject_id: uuid.UUID,
    on: date,
    education_level_id: uuid.UUID | None = None,
    duration_minutes: int | None = None,
    cohort_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
) -> PriceInEffect | None:
    """The catalog price in effect on the local date ``on``, or ``None``
    (see the module docstring for the ranks and the ``NULL`` rule).

    Raises :class:`ValueError` for ``pricing_model='package'`` (read
    through the product, #94), for a ``cohort_id`` with a class type other
    than ``course`` or a ``group_id`` with one other than ``group``, and
    for both scopes at once. One statement."""
    _check_arguments(
        class_type=class_type,
        pricing_model=pricing_model,
        cohort_id=cohort_id,
        group_id=group_id,
    )
    ranks = _rank_conditions(
        education_level_id=education_level_id,
        duration_minutes=duration_minutes,
        cohort_id=cohort_id,
        group_id=group_id,
    )
    rank = case(*ranks).label("rank")
    in_effect = (
        select(PriceVersion.id, PriceVersion.effective_from, PriceVersion.amount)
        .where(
            PriceVersion.service_price_id == ServicePrice.id,
            PriceVersion.effective_from <= on,
        )
        .order_by(PriceVersion.effective_from.desc())
        .limit(1)
        .lateral("in_effect")
    )
    query: Any = (
        select(ServicePrice.id, in_effect.c.id, in_effect.c.effective_from)
        .add_columns(in_effect.c.amount, rank)
        .join(in_effect, true())
        .where(
            ServicePrice.class_type == class_type,
            ServicePrice.pricing_model == pricing_model,
            ServicePrice.subject_id == subject_id,
            or_(*(condition for condition, _ in ranks)),
        )
        .order_by(rank)
        .limit(1)
    )
    row = (await session.execute(query)).first()
    if row is None or row[3] is None:
        return None
    return PriceInEffect(
        service_price_id=row[0],
        price_version_id=row[1],
        effective_from=row[2],
        amount=row[3],
        rank=int(row[4]),
    )
