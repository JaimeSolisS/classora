"""Tutor eligibility and the workload ranking rule (#51 B, C; spec §3, §15
W1.3; ADR-0005 D1, D2; ADR-0004 local dates).

Pure rules, no database (the data comes from
:mod:`~app.modules.scheduling.tutor_suggestions`).

**Eligible.** A tutor can teach a slot (subject, start, end, optional
classroom) when they hold the ``tutor`` role with an ``active`` account,
hold an **approved** qualification for the subject (pending, rejected,
cancelled and revoked ones never count; the subject belongs to one
education level, so this is also the level check), are **available** for
the whole slot (their weekly windows and dated exceptions; a center closure
is a slot conflict, not a tutor one, and doesn't drop anyone) and have **no
overlapping** session as tutor in ``offered``, ``confirmed`` or
``completed`` (back-to-back is fine). For a replacement, the session itself
is left out of the overlap and the workload, and its current tutor is never
listed. Teaching limits, breaks and the student's level are not applied.

**Workload ranking rule.** Each eligible tutor's workload is the sum of
``scheduled_minutes`` of their sessions in ``offered``, ``confirmed`` or
``completed`` whose **local start date** (center zone) falls in the ISO week
(Monday to Sunday) of the slot's local date (``weekly_minutes``), and on
that same local date (``daily_minutes``). Tutors are ordered by
``weekly_minutes`` ascending, then ``daily_minutes`` ascending, then those
who prefer the slot's classroom first, then display name (case-folded),
then user id. Teaching limits are shown but never drop or reorder a tutor.

**Why the others aren't listed** (HF-05 B1, :func:`suggestion_counts`): the
qualified candidates (active tutors with the qualification) split into
eligible, busy (an overlapping session; a tutor both busy and unavailable
counts as busy) and unavailable (no overlap, not available), so
``qualified = eligible + busy + unavailable``.

**A series** (HF-05 C4, :func:`series_is_busy`, :func:`series_is_available`):
a tutor must be free and available for **every** checked occurrence.
"""

import uuid
from bisect import bisect_left
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import accumulate
from typing import Final
from zoneinfo import ZoneInfo

from app.modules.staff import service as staff

NO_CLOSED_DATES: Final[frozenset[date]] = frozenset()
"""Center closures never drop a tutor (#51 B2): the create/offer path
reports them as ``center_closed``."""

type Interval = tuple[datetime, datetime]
"""An instant interval ``[start, end)``."""


@dataclass(frozen=True)
class Candidate:
    """An eligible tutor with the values the ranking uses."""

    user_id: uuid.UUID
    display_name: str
    weekly_minutes: int
    daily_minutes: int
    prefers_classroom: bool


def ranking_key(candidate: Candidate) -> tuple[int, int, bool, str, str]:
    """The workload ranking rule as one sort key (module docstring):
    weekly minutes, daily minutes, preferred room first, case-folded name,
    id. Total and deterministic."""
    return (
        candidate.weekly_minutes,
        candidate.daily_minutes,
        not candidate.prefers_classroom,
        candidate.display_name.casefold(),
        str(candidate.user_id),
    )


def rank_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    """The candidates in :func:`ranking_key` order."""
    return sorted(candidates, key=ranking_key)


def _local_midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(UTC)


def local_day_bounds(local_date: date, zone: str) -> tuple[datetime, datetime]:
    """The instants ``[start, end)`` of the local date in ``zone``: its local
    midnight and the next one (23 or 25 real hours on a DST change)."""
    tz = ZoneInfo(zone)
    return (
        _local_midnight(local_date, tz),
        _local_midnight(local_date + timedelta(days=1), tz),
    )


def local_week_bounds(local_date: date, zone: str) -> tuple[datetime, datetime]:
    """The instants ``[start, end)`` of the ISO week (Monday to Sunday)
    holding the local date in ``zone``: Monday's local midnight and the next
    Monday's (167 or 169 real hours in a DST week)."""
    tz = ZoneInfo(zone)
    monday = local_date - timedelta(days=local_date.isoweekday() - 1)
    return (
        _local_midnight(monday, tz),
        _local_midnight(monday + timedelta(days=7), tz),
    )


# --- Why the others aren't listed (HF-05 B1) ----------------------------------------


@dataclass(frozen=True)
class SuggestionCounts:
    """The buckets of the qualified candidates (module docstring)."""

    qualified: int
    eligible: int
    busy: int
    unavailable: int


def suggestion_counts(
    qualified: Collection[uuid.UUID],
    busy: Collection[uuid.UUID],
    eligible: Collection[uuid.UUID],
) -> SuggestionCounts:
    """The counts of the module docstring from the sets the suggestions
    build: ``busy`` and ``eligible`` are taken within ``qualified`` (and an
    eligible tutor is never busy); every other qualified tutor is
    unavailable."""
    pool = set(qualified)
    busy_set = pool & set(busy)
    eligible_set = (pool & set(eligible)) - busy_set
    return SuggestionCounts(
        qualified=len(pool),
        eligible=len(eligible_set),
        busy=len(busy_set),
        unavailable=len(pool) - len(busy_set) - len(eligible_set),
    )


# --- A series (HF-05 C4) ------------------------------------------------------------


def series_is_busy(
    occurrences: Sequence[Interval], sessions: Iterable[Interval]
) -> bool:
    """Whether any of the tutor's ``sessions`` overlaps any occurrence
    (``start < other end AND end > other start``: back-to-back is fine).
    ``O((n + m) log m)``: the sessions by start, with the running maximum of
    their ends."""
    ordered = sorted(sessions)
    if not ordered or not occurrences:
        return False
    starts = [start for start, _ in ordered]
    max_ends = list(accumulate((end for _, end in ordered), max))
    for start, end in occurrences:
        # The sessions starting before the occurrence ends; one of them
        # overlaps when the latest end among them is after its start.
        before = bisect_left(starts, end)
        if before and max_ends[before - 1] > start:
            return True
    return False


def series_is_available(
    rules: staff.TutorAvailabilityRules, occurrences: Iterable[Interval], zone: str
) -> bool:
    """Whether the tutor is available for every occurrence (weekly windows
    and dated exceptions, no closed dates: #51 B2)."""
    return all(
        staff.evaluate_interval(
            rules.windows, rules.exceptions, NO_CLOSED_DATES, start, end, zone
        ).available
        for start, end in occurrences
    )
