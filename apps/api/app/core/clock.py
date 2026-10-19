"""The application clock: the one source of "now" for time-based rules (#25).

Session times (creation, ``last_seen_at``, expiry, revocation, the purge
cutoff) read :func:`now` instead of the database's ``now()``, so tests can
move time with the ``clock`` fixture (``tests/conftest.py``) instead of
sleeping. New code whose rule must be testable by time travel reads
:func:`now` too.

:func:`now` calls the module attribute :data:`_source` at call time. The test
fixture replaces that attribute (not a context variable), so code running in
another thread, such as an app inside ``TestClient``, sees the frozen time.

Imports nothing from ``app.modules`` (core stays below the domain modules).
"""

from collections.abc import Callable
from datetime import UTC, datetime


def system_now() -> datetime:
    """The real time: timezone-aware UTC."""
    return datetime.now(UTC)


_source: Callable[[], datetime] = system_now
"""What :func:`now` returns; replaced only by the test fixture."""


def now() -> datetime:
    """The current time, timezone-aware UTC."""
    return _source()
