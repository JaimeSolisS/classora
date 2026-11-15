"""The demo seed clock (#353 G1, E2 (a)): :func:`seed_clock`, and the
demo actor of the billing writes (M6-demo G3): :func:`seed_actor`.

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

**The actor (M6-demo G3).** The billing workflows read the acting user from
the session (``app.core.audit.current_actor_id``, the audit actor and the
``…_by_user_id`` columns), and the demo's Unit of Work has none.
:func:`seed_actor` binds one user with ``app.core.audit.bind_actor`` for the
duration of a ``with`` block and always puts the previous source back (or
none), also when the block raises.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import ACTOR_INFO_KEY, bind_actor


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


@contextmanager
def seed_actor(session: AsyncSession, user_id: uuid.UUID) -> Iterator[uuid.UUID]:
    """Run the block with ``user_id`` as the session's actor (M6-demo G3);
    yields it. Restores the previous actor source (or none) on exit."""
    had = ACTOR_INFO_KEY in session.info
    saved = session.info.get(ACTOR_INFO_KEY)
    bind_actor(session, lambda: user_id)
    try:
        yield user_id
    finally:
        if had:
            session.info[ACTOR_INFO_KEY] = saved
        else:
            session.info.pop(ACTOR_INFO_KEY, None)
