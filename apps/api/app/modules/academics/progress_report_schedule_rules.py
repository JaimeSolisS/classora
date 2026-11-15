"""Progress report schedule rules (#316 A4). Pure functions, no I/O.

A **schedule** says which tutor owes a ``weekly`` (ISO week, Monday to
Sunday) or ``monthly`` (calendar month) report about one student, optionally
for one subject. Every date here is a **center-local** date: the caller turns
an instant into ``today`` with :func:`local_today` (the job reads the center's
zone on each run).

- :func:`current_period_start`, :func:`latest_ended_period` (the period the
  job fills: the last one that ended before ``today``) and
  :func:`due_period` (that period when the schedule covers it);
- :func:`starts_on_error` (S2: a Monday / the 1st, not before the current
  period, at most :data:`STARTS_ON_MAX_DAYS` days ahead) and
  :func:`reactivated_starts_on` (S3, E2: never retroactive);
- :func:`eligibility` (D3, the panel's ``ineligible_reason``): #83 C2's
  create rules, the first failing reason in the Definitions order;
- :func:`latest_status` (B3, E3): what exists for the latest ended period;
  a ``report_id`` only for a report a reviewer may open (drafts and deleted
  placeholders are author-only, #83 E5).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

from app.modules.academics.progress_report_rules import (
    APPROVED,
    CHANGES_REQUESTED,
    MONTHLY,
    RETRACTED,
    SUBMITTED,
    WEEKLY,
    month_end,
)

CADENCES: Final = (WEEKLY, MONTHLY)
STARTS_ON_MAX_DAYS: Final = 366
"""S2: ``starts_on`` is at most this many days after ``today``."""
LIST_LIMIT: Final = 100
"""S1: at most this many schedules (active first, then the newest)."""

STARTS_ON_INVALID: Final = "schedule_starts_on_invalid"

STUDENT_INACTIVE: Final = "student_inactive"
AUTHOR_NOT_TUTOR: Final = "author_not_tutor"
AUTHOR_NOT_CURRENT: Final = "author_not_current"
SUBJECT_INACTIVE: Final = "subject_inactive"
SUBJECT_NOT_ASSIGNED: Final = "subject_not_assigned"
INELIGIBLE_REASONS: Final = (
    STUDENT_INACTIVE,
    AUTHOR_NOT_TUTOR,
    AUTHOR_NOT_CURRENT,
    SUBJECT_INACTIVE,
    SUBJECT_NOT_ASSIGNED,
)

NOT_CREATED: Final = "not_created"
DELETED: Final = "deleted"
LINKED_STATUSES: Final = frozenset({SUBMITTED, CHANGES_REQUESTED, APPROVED, RETRACTED})
"""The statuses whose report a reviewer may open (``report_id`` is set)."""

type Cadence = Literal["weekly", "monthly"]
type IneligibleReason = Literal[
    "student_inactive",
    "author_not_tutor",
    "author_not_current",
    "subject_inactive",
    "subject_not_assigned",
]
type LatestStatus = Literal[
    "not_created",
    "draft",
    "deleted",
    "submitted",
    "changes_requested",
    "approved_for_family",
    "retracted",
]


def local_today(now: datetime, zone: str) -> date:
    """The center-local date of the instant ``now`` (an aware datetime)."""
    return now.astimezone(ZoneInfo(zone)).date()


def _monday(day: date) -> date:
    return day - timedelta(days=day.isoweekday() - 1)


def current_period_start(cadence: str, today: date) -> date:
    """The Monday of ``today``'s week, or the 1st of its month."""
    if cadence == WEEKLY:
        return _monday(today)
    return today.replace(day=1)


def latest_ended_period(cadence: str, today: date) -> tuple[date, date]:
    """The most recent period that ended before ``today``: the week before
    this one, or the month before this one."""
    if cadence == WEEKLY:
        start = _monday(today) - timedelta(days=7)
        return start, start + timedelta(days=6)
    first = today.replace(day=1)
    start = (first - timedelta(days=1)).replace(day=1)
    return start, month_end(start)


def due_period(cadence: str, starts_on: date, today: date) -> tuple[date, date] | None:
    """The latest ended period when the schedule covers it (``start >=
    starts_on``), else ``None``."""
    start, end = latest_ended_period(cadence, today)
    return (start, end) if start >= starts_on else None


def starts_on_error(cadence: str, starts_on: date, today: date) -> str | None:
    """S2: ``schedule_starts_on_invalid`` when ``starts_on`` isn't a Monday
    (weekly) or the 1st (monthly), is before the current period's start, or
    is more than :data:`STARTS_ON_MAX_DAYS` days after ``today``."""
    if cadence == WEEKLY and starts_on.isoweekday() != 1:
        return STARTS_ON_INVALID
    if cadence == MONTHLY and starts_on.day != 1:
        return STARTS_ON_INVALID
    if starts_on < current_period_start(cadence, today):
        return STARTS_ON_INVALID
    if (starts_on - today).days > STARTS_ON_MAX_DAYS:
        return STARTS_ON_INVALID
    return None


def reactivated_starts_on(cadence: str, starts_on: date, today: date) -> date:
    """S3 (E2): a reactivated schedule never covers a period that ended
    before it was reactivated."""
    return max(starts_on, current_period_start(cadence, today))


def eligibility(
    *,
    student_active: bool,
    author_tutor: bool,
    author_current: bool,
    subject_set: bool,
    subject_active: bool,
    subject_assigned: bool,
) -> IneligibleReason | None:
    """Why the job would skip the schedule now, or ``None`` (D3).

    ``author_tutor``: the author is an active user with the ``tutor`` role;
    ``author_current``: the student is one of the author's current
    assignments; ``subject_active`` / ``subject_assigned`` matter only when
    ``subject_set``."""
    if not student_active:
        return "student_inactive"
    if not author_tutor:
        return "author_not_tutor"
    if not author_current:
        return "author_not_current"
    if subject_set and not subject_active:
        return "subject_inactive"
    if subject_set and not subject_assigned:
        return "subject_not_assigned"
    return None


@dataclass(frozen=True)
class PeriodReport:
    """A report found for a schedule's latest period."""

    id: str
    status: str
    deleted: bool


def latest_status(
    placeholder: PeriodReport | None, manual: PeriodReport | None
) -> tuple[LatestStatus, str | None]:
    """B3: the schedule's own placeholder for the period when there is one
    (``deleted`` when the tutor deleted it), else a matching live,
    non-retracted report the tutor wrote, else ``not_created``. The
    ``report_id`` only for :data:`LINKED_STATUSES`."""
    found = placeholder if placeholder is not None else manual
    if found is None:
        return NOT_CREATED, None
    if found.deleted:
        return DELETED, None
    status: LatestStatus = found.status  # type: ignore[assignment]
    return status, found.id if found.status in LINKED_STATUSES else None
