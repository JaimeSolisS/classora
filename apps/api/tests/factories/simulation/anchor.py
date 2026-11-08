"""The simulation's fixed timeline (#543 E2 (a), owner decision 2026-11-07)
and its clock, :func:`sim_clock`.

Everything before :data:`SIM_ANCHOR` is history in a final state; from
:data:`SIM_ANCHOR` to :data:`SIM_HORIZON` records are only scheduled or
pending. The timeline is the same whatever the real date of the run: the
plan never reads the real clock, and every write runs under
:func:`sim_clock` at a planned instant.

:func:`sim_clock` works like ``demo_clock.seed_clock`` except that the
instant may also be **later** than the real time (a run on 7 Nov writes the
8-18 Nov history). It replaces :data:`app.core.clock._source` for the block
and always restores the previous source, also when the block raises and
under the test ``clock`` fixture (the frozen source, not ``system_now``).
Only ``clock.now()`` moves: database-set columns (``created_at``,
``updated_at``, ``audit_events.occurred_at``) keep the real run time (E7).
Test support only: nothing under ``app/`` imports it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from app.core import clock
from tests.factories.demo_clock import DemoPlanError

ZONE_NAME: Final = "America/Mexico_City"
"""The center time zone: the migration default, unchanged (E8)."""
ZONE: Final = ZoneInfo(ZONE_NAME)

SIM_START: Final = date(2026, 9, 1)
"""The first day with classes (a Tuesday)."""
SIM_ANCHOR: Final = date(2026, 11, 19)
"""The first day that is "future" (a Thursday)."""
SIM_HORIZON: Final = date(2026, 12, 5)
"""The last day with scheduled classes (a Saturday)."""
SIM_SETUP_DAY: Final = date(2026, 8, 24)


def local_instant(day: date, hhmm: str) -> datetime:
    """``day`` at the local wall-clock time ``HH:MM`` in :data:`ZONE`, as an
    aware UTC instant."""
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes), tzinfo=ZONE).astimezone(UTC)


SIM_NOW: Final = local_instant(SIM_ANCHOR, "08:00")
"""The simulated "now" of the pending and future writes: 19 Nov 2026 08:00
local (14:00 UTC), before the center opens."""
SIM_SETUP: Final = local_instant(SIM_SETUP_DAY, "10:00")
"""The center's setup instant (accounts, catalog, settings, staff)."""

DAY_INSTANT: Final = "08:00"
"""The default local time of a day step (the driver's ``on_day`` clock)."""


def history_days() -> Iterator[date]:
    """Every day from :data:`SIM_START` to the day before :data:`SIM_ANCHOR`."""
    day = SIM_START
    while day < SIM_ANCHOR:
        yield day
        day += timedelta(days=1)


@contextmanager
def sim_clock(instant: datetime) -> Iterator[datetime]:
    """Run the block with ``clock.now()`` fixed at ``instant`` (in UTC), which
    may be earlier or later than the real time; yields that instant. Raises
    :class:`~tests.factories.demo_clock.DemoPlanError` for a naive instant.
    Restores the previous clock source on exit."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise DemoPlanError(f"the simulation clock needs an aware instant: {instant}")
    fixed = instant.astimezone(UTC)
    saved = clock._source  # the documented test seam (#25)
    clock._source = lambda: fixed
    try:
        yield fixed
    finally:
        clock._source = saved
