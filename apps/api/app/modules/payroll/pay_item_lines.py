"""Which period a bonus or deduction lands in (#109 B; ADR-0017 E8, its #109
notes; data-model §11.8).

Pure functions on frozen values: no I/O, no ``datetime``, no float. The workflow
:mod:`app.workflows.payroll_calculation` reads the items and the periods; this
module decides the lines. Nothing is rounded: an amount is copied, with the sign
of the type's direction.

- **Recurring:** the full amount in every not-finalized period overlapping
  ``[effective_from, effective_to]``, both ends inclusive, no end open. No
  proration, a stub of one day included.
- **One-time (#109 E3):** an item with a line in a finalized period is done.
  Otherwise its period is the tutor's earliest not-finalized period with
  ``ends_on >= effective_from``. That gives one answer whatever the period's
  status: a date before the first period, in a gap or in a finalized period
  lands in the next one, and a date after the last period has none yet.

Nothing here touches a ``Decimal`` or an ``.amount``:
``tests/unit/test_payroll_calculation_pure.py`` reads this file's syntax tree too.
"""

import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from operator import attrgetter
from typing import Final

from app.core.money import Money
from app.modules.payroll.calculation import LineDraft

PAY_ITEM: Final = "pay_item"
BONUS: Final = "bonus"
RECURRING: Final = "recurring"


@dataclass(frozen=True)
class PayItemFact:
    """What the placement needs of one ``pay_items`` row. ``pay`` is the
    positive amount, and ``direction`` is its type's."""

    id: uuid.UUID
    direction: str
    pay: Money
    recurrence: str
    effective_from: date
    effective_to: date | None


@dataclass(frozen=True)
class PeriodDates:
    """A period, as the placement reads it."""

    id: uuid.UUID
    starts_on: date
    ends_on: date


def placed_period[T: PeriodDates](
    item: PayItemFact,
    unfinalized_periods: Iterable[T],
    finalized_item_ids: Collection[uuid.UUID],
) -> T | None:
    """The period of a one-time item (see the module docstring), ``None`` when
    it has none or has been paid. A recurring item has no single period."""
    if item.recurrence == RECURRING or item.id in finalized_item_ids:
        return None
    return min(
        (p for p in unfinalized_periods if p.ends_on >= item.effective_from),
        key=attrgetter("starts_on"),
        default=None,
    )


def _overlaps(item: PayItemFact, period: PeriodDates) -> bool:
    return item.effective_from <= period.ends_on and (
        item.effective_to is None or item.effective_to >= period.starts_on
    )


def pay_item_drafts(
    *,
    period: PeriodDates,
    items: Iterable[PayItemFact],
    unfinalized_periods: Collection[PeriodDates],
    finalized_item_ids: Collection[uuid.UUID],
) -> list[LineDraft]:
    """The period's item lines, one per item, by ``pay_item_id``. ``period`` is
    itself not finalized."""

    def lands(item: PayItemFact) -> bool:
        if item.recurrence == RECURRING:
            return _overlaps(item, period)
        placed = placed_period(item, unfinalized_periods, finalized_item_ids)
        return placed is not None and placed.id == period.id

    return [
        LineDraft(
            kind=PAY_ITEM,
            amount=item.pay if item.direction == BONUS else -item.pay,
            pay_item_id=item.id,
        )
        for item in sorted(items, key=attrgetter("id"))
        if lands(item)
    ]
