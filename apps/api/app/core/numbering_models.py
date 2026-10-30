"""The ``number_series`` table (#98 A1; ADR-0006 E13; data-model §3.25).

Gap-free yearly counters for numbered documents: one row per series
``code`` (:data:`SERIES_CODES`: ``R`` receipts, ``P`` payslips, reserved for
M7) and center-local ``year``; ``last_value`` is the last number handed out
(``0`` before the first). ``PRIMARY KEY (code, year)``, no ``id``. Rows are
created on first use by :func:`app.core.numbering.next_number`; there are no
seed rows.

Not audited: the numbered document is (a receipt's ``create`` event carries
its number). Not audited is also what lets :func:`next_number` create the
row with ``INSERT … ON CONFLICT DO NOTHING``, which the audit guard refuses
on audited tables.
"""

from typing import Final

from sqlalchemy import CheckConstraint, Integer, SmallInteger, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base

SERIES_CODES: Final = ("R", "P")
"""The series codes: ``R`` receipts (#98), ``P`` payslips (M7)."""

YEAR_MIN: Final = 2000
YEAR_MAX: Final = 9999


class NumberSeries(Base):
    """One gap-free counter (#98 A1)."""

    __tablename__ = "number_series"
    __table_args__ = (
        CheckConstraint(
            "code IN ({})".format(", ".join(f"'{c}'" for c in SERIES_CODES)),
            name="code",
        ),
        CheckConstraint(f"year BETWEEN {YEAR_MIN} AND {YEAR_MAX}", name="year_range"),
        CheckConstraint("last_value >= 0", name="last_value_non_negative"),
    )

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, server_default=text("0"))
