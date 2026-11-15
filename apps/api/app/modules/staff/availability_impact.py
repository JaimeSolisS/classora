"""Which scheduled sessions an availability change affects (M4-18 #63 B1,
C2; spec §3; ADR-0004). Pure functions, no I/O.

A session is **affected** by a change when all of these hold:

1. its state is ``offered`` or ``confirmed`` and it starts after ``now``
   (started, ``completed``, ``cancelled``, ``skipped_closure``,
   ``conflict`` and ``unassigned`` sessions never are; the caller reads
   only the tutor's own sessions);
2. the tutor's **current** rules make them available for it, **ignoring
   center closures** (a closure doesn't change with the tutor's
   availability);
3. the **proposed** rules (the same rules after the change) don't.

Both checks split the session's instants into local wall-clock pieces in
the center zone (:func:`~app.modules.staff.availability.evaluate_interval`,
the same as ``is_tutor_available_at``: the DST gap and the repeated hour
included). So a session already outside the tutor's availability (booked
with a ``tutor_unavailable`` override, #49) is never affected by a later
change, and a change that removes and re-adds the same time affects
nothing.

:func:`change_priority` is the inbox priority of a change request (C2):
``high`` when the earliest affected session starts within 24 hours of now.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Final, Literal

from app.modules.staff.availability import TutorAvailabilityRules, evaluate_interval

IMPACT_STATES: Final = frozenset({"offered", "confirmed"})
"""The session states a change can affect (the scheduling ``offered`` and
``confirmed``; repeated here, ADR-0002)."""

HIGH_PRIORITY_WITHIN: Final = timedelta(hours=24)

type ChangePriority = Literal["high", "normal"]


def _available(
    rules: TutorAvailabilityRules, starts_at: datetime, ends_at: datetime, zone: str
) -> bool:
    return evaluate_interval(
        rules.windows, rules.exceptions, (), starts_at, ends_at, zone
    ).available


def is_affected(
    *,
    state: str,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
    zone: str,
    current: TutorAvailabilityRules,
    proposed: TutorAvailabilityRules,
) -> bool:
    """Whether a session of the tutor is affected (see the module
    docstring). Naive datetimes raise :class:`ValueError`."""
    if state not in IMPACT_STATES or starts_at <= now:
        return False
    return _available(current, starts_at, ends_at, zone) and not _available(
        proposed, starts_at, ends_at, zone
    )


def change_priority(starts: Iterable[datetime], now: datetime) -> ChangePriority:
    """``high`` when the earliest of ``starts`` is at most 24 hours after
    ``now`` (exactly 24 hours included), otherwise ``normal``."""
    first = min(starts, default=None)
    if first is not None and first - now <= HIGH_PRIORITY_WITHIN:
        return "high"
    return "normal"
