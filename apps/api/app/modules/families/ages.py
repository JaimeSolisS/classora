"""Age rules (ADR-0007 D17, D18; #33 B2). Pure functions: the caller passes
today's date in the center's time zone (``app.core.clock.center_today``)."""

from datetime import date
from typing import Final

ADULT_AGE: Final = 18


def is_minor(birthdate: date, today: date) -> bool:
    """Whether someone born on ``birthdate`` is under 18 on ``today``.

    They are an adult from their 18th birthday on, compared as (year, month,
    day), so someone born on 29 February becomes an adult on 1 March in a
    non-leap year.
    """
    adult_from = (birthdate.year + ADULT_AGE, birthdate.month, birthdate.day)
    return (today.year, today.month, today.day) < adult_from
