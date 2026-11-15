"""The application clock: the one source of "now" for time-based rules (#25).

Session times (creation, ``last_seen_at``, expiry, revocation, the purge
cutoff) read :func:`now` instead of the database's ``now()``, so tests can
move time with the ``clock`` fixture (``tests/conftest.py``) instead of
sleeping. New code whose rule must be testable by time travel reads
:func:`now` too.

:func:`now` calls the module attribute :data:`_source` at call time. The test
fixture replaces that attribute (not a context variable), so code running in
another thread, such as an app inside ``TestClient``, sees the frozen time.

**Dates in a zone (#33, #37; ADR-0004, ADR-0007 D17).** :func:`today_in`
is today's calendar date in a given IANA zone, from :func:`now`. The center's
zone is a business setting: rules that depend on "today" (a birthdate not in
the future, the 18th birthday) use ``administration.service.center_today``,
which passes it here, never the UTC date.

Imports nothing from ``app.modules`` (core stays below the domain modules).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

DEFAULT_CENTER_TIME_ZONE: Final = "America/Mexico_City"
"""The default center time zone (ADR-0004): the value migration 0020 stores in
the business settings (#37). Code reads the setting, never this constant.
Zone data comes from the system (``zoneinfo``; the API image and CI have it),
not from a Python package."""


def system_now() -> datetime:
    """The real time: timezone-aware UTC."""
    return datetime.now(UTC)


_source: Callable[[], datetime] = system_now
"""What :func:`now` returns; replaced only by the test fixture and the demo
seed's ``seed_clock`` (#353)."""


def now() -> datetime:
    """The current time, timezone-aware UTC."""
    return _source()


def today_in(zone: str) -> date:
    """Today's calendar date in the IANA time zone ``zone`` (:func:`now`
    there)."""
    return now().astimezone(ZoneInfo(zone)).date()
