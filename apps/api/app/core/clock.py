"""The application clock: the one source of "now" for time-based rules (#25).

Session times (creation, ``last_seen_at``, expiry, revocation, the purge
cutoff) read :func:`now` instead of the database's ``now()``, so tests can
move time with the ``clock`` fixture (``tests/conftest.py``) instead of
sleeping. New code whose rule must be testable by time travel reads
:func:`now` too.

:func:`now` calls the module attribute :data:`_source` at call time. The test
fixture replaces that attribute (not a context variable), so code running in
another thread, such as an app inside ``TestClient``, sees the frozen time.

**Center date (#33; ADR-0004, ADR-0007 D17).** :func:`center_today` is
today's calendar date in the center's time zone, from :func:`now`. Rules that
depend on "today" (a birthdate not in the future, the 18th birthday) use it,
never the UTC date.

Imports nothing from ``app.modules`` (core stays below the domain modules).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

DEFAULT_CENTER_TIME_ZONE: Final = "America/Mexico_City"
"""The center's time zone (ADR-0004 default). M3-01 replaces this constant
with the business setting, as it replaces the web app's
``DEFAULT_CENTER_TIME_ZONE`` (M1-15). Zone data comes from the system
(``zoneinfo``; the API image and CI have it), not from a Python package."""


def system_now() -> datetime:
    """The real time: timezone-aware UTC."""
    return datetime.now(UTC)


_source: Callable[[], datetime] = system_now
"""What :func:`now` returns; replaced only by the test fixture."""


def now() -> datetime:
    """The current time, timezone-aware UTC."""
    return _source()


def center_today() -> date:
    """Today's calendar date in the center's time zone (:func:`now` there)."""
    return now().astimezone(ZoneInfo(DEFAULT_CENTER_TIME_ZONE)).date()
