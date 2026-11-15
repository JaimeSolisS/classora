"""Tutor workload warnings (#50 A; spec §3 "Workload limits produce warnings
for daily/weekly excess"; ADR-0005 warnings; ADR-0004 local dates).

Pure rules, no database. The data comes from two readers:

- the limits: ``staff.service.teaching_limits`` (#197; ``None`` is no
  limit, no break);
- the counted sessions: ``scheduling.service.tutor_workload_sessions``
  (:mod:`~app.modules.scheduling.slot_search`), the tutor's sessions in
  ``TUTOR_OCCUPYING_STATES`` (``offered``, ``confirmed``, ``completed``)
  from the Monday of the first ISO week through the Sunday of the last,
  widened by :data:`BREAK_MAX` minutes at both ends. ``unassigned``,
  ``cancelled``, ``skipped_closure`` and ``conflict`` sessions never count.

The candidate (the session being created, or a suggested slot) is counted
once, on top of the loaded sessions. The rules, in the order of
:func:`sort_warnings`:

1. ``daily_limit_exceeded``: the minutes (``scheduled_minutes``) of the
   counted sessions on the candidate's **local date** in the center zone,
   the candidate included, are **above** ``daily_limit_minutes`` (exactly
   the limit doesn't warn). Details ``{date, limit_minutes,
   scheduled_minutes}``.
2. ``weekly_limit_exceeded``: the same over the **ISO week** (Monday to
   Sunday, local dates) holding the candidate's local date, against
   ``weekly_limit_minutes``. Details ``{week_start, limit_minutes,
   scheduled_minutes}``; ``week_start`` is the Monday.
3. ``break_too_short``: with ``required_break_minutes`` above 0, the counted
   session that ends last at or before the candidate's start, and the one
   that starts first at or after its end, each warn when the gap in elapsed
   minutes (instants, so across midnight too) is **below** the required
   break; back-to-back (gap 0) warns. At most two, the earlier neighbour
   first. Details ``{class_session_id, gap_minutes,
   required_break_minutes}``.

A session never crosses a local date (#47 B2), so a session's local date is
its start's. Warnings never block, never need an override, and come only
with ``2xx`` responses (ADR-0005). They are computed per response and never
stored, audited or notified.

**No lock (#50 A8).** Warnings are advisory: two concurrent creates for the
same tutor on the same day may each miss the other's minutes. The overlap
guarantees (the per-student advisory lock and the ``EXCLUDE`` constraints)
are unchanged.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, NamedTuple
from zoneinfo import ZoneInfo

from app.modules.scheduling.conflicts import (
    BREAK_TOO_SHORT,
    DAILY_LIMIT_EXCEEDED,
    WEEKLY_LIMIT_EXCEEDED,
)
from app.modules.scheduling.times import scheduled_minutes
from app.modules.staff.service import TeachingLimits

BREAK_MAX: Final = 240
"""The largest required break (#197: 0-240 minutes); the loader widens its
range by this much so the break neighbours are found."""

WARNING_ORDER: Final[tuple[str, ...]] = (
    DAILY_LIMIT_EXCEEDED,
    WEEKLY_LIMIT_EXCEEDED,
    BREAK_TOO_SHORT,
)
"""The ADR-0005 table order of the warning codes."""


class WorkloadSession(NamedTuple):
    """A counted session of the tutor."""

    class_session_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    scheduled_minutes: int


@dataclass(frozen=True)
class WorkloadWarning:
    """One warning; ``code`` is one of :data:`WARNING_ORDER`."""

    code: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in WARNING_ORDER:
            raise ValueError(f"not a warning code: {self.code}")

    def as_wire(self) -> dict[str, Any]:
        """The ``warnings[]`` item of a ``2xx`` body."""
        return {"code": self.code, "details": self.details}


def iso_week_start(local_date: date) -> date:
    """The Monday of the ISO week holding ``local_date``."""
    return local_date - timedelta(days=local_date.isoweekday() - 1)


def workload_range(
    first_local_date: date, last_local_date: date, zone: str
) -> tuple[datetime, datetime]:
    """The instants ``[low, high)`` whose sessions the warnings of any
    candidate on ``first_local_date``..``last_local_date`` need: local
    midnight of the first ISO week's Monday to local midnight after the last
    ISO week's Sunday, widened by :data:`BREAK_MAX` minutes at both ends."""
    tz = ZoneInfo(zone)
    monday = iso_week_start(first_local_date)
    after_sunday = iso_week_start(last_local_date) + timedelta(days=7)
    widen = timedelta(minutes=BREAK_MAX)
    low = datetime.combine(monday, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    high = datetime.combine(after_sunday, datetime.min.time(), tzinfo=tz).astimezone(
        UTC
    )
    return (low - widen, high + widen)


def _sort_key(warning: WorkloadWarning) -> int:
    return WARNING_ORDER.index(warning.code)


def sort_warnings(warnings: Iterable[WorkloadWarning]) -> list[WorkloadWarning]:
    """Daily, then weekly, then break; a stable sort, so two breaks keep the
    earlier neighbour first (:func:`workload_warnings` appends it first)."""
    return sorted(warnings, key=_sort_key)


def _minutes_between(earlier: datetime, later: datetime) -> int:
    return int(
        (later.astimezone(UTC) - earlier.astimezone(UTC)) // timedelta(minutes=1)
    )


def _break_warnings(
    required: int,
    sessions: Sequence[WorkloadSession],
    starts_at: datetime,
    ends_at: datetime,
) -> list[WorkloadWarning]:
    before = [s for s in sessions if s.ends_at <= starts_at]
    after = [s for s in sessions if s.starts_at >= ends_at]
    neighbours: list[tuple[WorkloadSession, int]] = []
    if before:
        previous = max(before, key=lambda s: (s.ends_at, str(s.class_session_id)))
        neighbours.append((previous, _minutes_between(previous.ends_at, starts_at)))
    if after:
        following = min(after, key=lambda s: (s.starts_at, str(s.class_session_id)))
        neighbours.append((following, _minutes_between(ends_at, following.starts_at)))
    return [
        WorkloadWarning(
            BREAK_TOO_SHORT,
            {
                "class_session_id": str(neighbour.class_session_id),
                "gap_minutes": gap,
                "required_break_minutes": required,
            },
        )
        for neighbour, gap in neighbours
        if gap < required
    ]


def workload_warnings(
    limits: TeachingLimits,
    tutor_sessions: Iterable[
        WorkloadSession | tuple[uuid.UUID, datetime, datetime, int]
    ],
    candidate: tuple[datetime, datetime],
    zone: str,
) -> list[WorkloadWarning]:
    """The warnings of the candidate ``(starts_at, ends_at)`` for a tutor
    with ``limits`` and the counted ``tutor_sessions`` (the candidate itself
    not among them), in :func:`sort_warnings` order. See the module
    docstring for the rules."""
    starts_at, ends_at = candidate
    sessions = [WorkloadSession(*item) for item in tutor_sessions]
    tz = ZoneInfo(zone)
    local_date = starts_at.astimezone(tz).date()
    week_start = iso_week_start(local_date)
    own = scheduled_minutes(starts_at, ends_at)
    found: list[WorkloadWarning] = []

    daily = limits.daily_limit_minutes
    if daily is not None:
        total = own + sum(
            s.scheduled_minutes
            for s in sessions
            if s.starts_at.astimezone(tz).date() == local_date
        )
        if total > daily:
            found.append(
                WorkloadWarning(
                    DAILY_LIMIT_EXCEEDED,
                    {
                        "date": local_date.isoformat(),
                        "limit_minutes": daily,
                        "scheduled_minutes": total,
                    },
                )
            )

    weekly = limits.weekly_limit_minutes
    if weekly is not None:
        total = own + sum(
            s.scheduled_minutes
            for s in sessions
            if iso_week_start(s.starts_at.astimezone(tz).date()) == week_start
        )
        if total > weekly:
            found.append(
                WorkloadWarning(
                    WEEKLY_LIMIT_EXCEEDED,
                    {
                        "week_start": week_start.isoformat(),
                        "limit_minutes": weekly,
                        "scheduled_minutes": total,
                    },
                )
            )

    required = limits.required_break_minutes
    if required:
        found += _break_warnings(required, sessions, starts_at, ends_at)
    return sort_warnings(found)
