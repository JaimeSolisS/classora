"""The demo seed clock (#353 G1, E2 (a)): :func:`seed_clock`.

No service creates a class that has already started, marks its attendance
before it starts or closes it out "back then". So the #353 generators run
the route-level services under a fixed **past** instant: :func:`seed_clock`
replaces :data:`app.core.clock._source` (what ``clock.now()`` returns) for
the duration of a ``with`` block and always puts the previous source back,
also when the block raises. Under the test ``clock`` fixture it restores
the frozen source, not ``system_now``.

Only ``clock.now()`` moves: database-set columns (``created_at``,
``audit_events.occurred_at``) keep the real run time.

The demo never moves time forward: the instant must be timezone-aware and
earlier than the current ``clock.now()`` (:class:`DemoPlanError` otherwise).
Generators use it only around past steps, never across a generator
boundary. Test support only: nothing under ``app/`` imports it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from app.core import clock


class DemoPlanError(RuntimeError):
    """A demo plan instant the seed clock refuses (naive or not in the
    past): the plan has a bug (#353 G1)."""


@contextmanager
def seed_clock(instant: datetime) -> Iterator[datetime]:
    """Run the block with ``clock.now()`` fixed at ``instant`` (in UTC);
    yields that instant. Restores the previous clock source on exit."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise DemoPlanError(f"the seed clock needs an aware instant: {instant}")
    fixed = instant.astimezone(UTC)
    if fixed >= clock.now():
        raise DemoPlanError(f"the seed clock only moves back: {fixed}")
    saved = clock._source  # the documented test seam (#25)
    clock._source = lambda: fixed
    try:
        yield fixed
    finally:
        clock._source = saved
