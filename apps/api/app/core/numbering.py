"""Gap-free yearly document numbers (#98 B1; ADR-0006 E13, I10;
data-model §3.25, §6).

:func:`next_number` hands out the next number of one series and year in
the caller's transaction:

1. ``INSERT INTO number_series (code, year) … ON CONFLICT DO NOTHING``
   (the first number of a year creates its row; the table isn't audited);
2. ``UPDATE number_series SET last_value = last_value + 1 WHERE code = :c
   AND year = :y RETURNING last_value``.

The ``UPDATE`` keeps the row locked until the transaction ends, so a
concurrent caller of the same series and year waits, and a rollback gives
the number back: the next caller gets the same value. That is why there is
no PostgreSQL sequence (a sequence never gives a value back, so a rolled
back payment would leave a gap).

**Lock order (data-model §6):** the series row is the very last lock, taken
after the billing account row ``FOR UPDATE``, and held for the insert of
the numbered document only (the caller reads everything else first).

A value above ``max_value`` (``999999`` for the six-digit receipt numbers)
raises :class:`NumberSeriesExhaustedError`: a plain exception, so the
request ends in a logged ``500`` and the whole transaction (the payment
too) rolls back (#98 B1, data-model O6).

M7 payslips reuse it with the ``P`` series. This module is shared
infrastructure (ADR-0002): it imports no domain module.
"""

from typing import Final

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.numbering_models import SERIES_CODES, YEAR_MAX, YEAR_MIN, NumberSeries

SIX_DIGITS_MAX: Final = 999_999
"""The largest six-digit number (``R-YYYY-NNNNNN``)."""


class NumberSeriesExhaustedError(RuntimeError):
    """A series ran past its largest number in one year (a ``500``)."""

    def __init__(self, code: str, year: int) -> None:
        super().__init__(f"number series {code} {year} is exhausted")
        self.code = code
        self.year = year


async def next_number(
    session: AsyncSession, code: str, year: int, *, max_value: int = SIX_DIGITS_MAX
) -> int:
    """The next number of series ``code`` in ``year`` (1 for the first),
    locked until the caller's transaction ends (see the module docstring).
    Two statements; never commits."""
    if code not in SERIES_CODES:
        raise ValueError(f"unknown number series {code!r}")
    if not YEAR_MIN <= year <= YEAR_MAX:
        raise ValueError(f"year {year} is out of range")
    await session.execute(
        insert(NumberSeries)
        .values(code=code, year=year, last_value=0)
        .on_conflict_do_nothing(index_elements=["code", "year"])
    )
    value = await session.scalar(
        update(NumberSeries)
        .where(NumberSeries.code == code, NumberSeries.year == year)
        .values(last_value=NumberSeries.last_value + 1)
        .returning(NumberSeries.last_value)
        .execution_options(synchronize_session=False)
    )
    if value is None:  # pragma: no cover - the row was inserted above
        raise RuntimeError("the number series row is missing")
    if value > max_value:
        raise NumberSeriesExhaustedError(code, year)
    return int(value)
