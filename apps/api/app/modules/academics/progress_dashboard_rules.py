"""Progress dashboard rules (#84 M5-17 A3, B1, B3, E6). Pure functions, no I/O.

- :func:`progress_audience` (A3): how the caller relates to one student for
  the two dashboard reads, the first that applies: ``evaluation.manage``
  staff, a current tutor, the family (the #79 check allowed it), a former
  tutor (taught an in-scope session of the student), or nobody;
  :func:`route_block` turns the audience into the route's answer (``None``,
  ``student_consent_required`` or ``not_found``): a former tutor reads only
  the session log, never the progress summary (E4).
- :func:`percent` (E6): ``numerator / denominator × 100`` as a ``Decimal``
  half-up to two places, ``None`` for a zero denominator (never a float).
- The months (E6): :func:`month_key` (center-local ``YYYY-MM``),
  :func:`months_between` (every month of a range, oldest first) and
  :func:`month_span`.
- The range (B1): :func:`default_range` (the 12 calendar months ending at the
  center's today) and :func:`progress_range_issue` / :func:`log_range_issue`
  (the ``422`` codes, each at its query parameter).
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final, Literal

EARLIEST_DATE: Final = date(2000, 1, 1)
"""``range_invalid`` before it (B1)."""
DEFAULT_MONTHS: Final = 12
MAX_MONTHS: Final = 24
PLAN_ITEMS_MAX: Final = 20
LOG_LIMIT_DEFAULT: Final = 20
LOG_LIMIT_MAX: Final = 50

RANGE_INVALID: Final = "range_invalid"
RANGE_IN_FUTURE: Final = "range_in_future"
RANGE_TOO_LONG: Final = "range_too_long"
SUBJECT_UNKNOWN: Final = "subject_unknown"
CONSENT_REQUIRED: Final = "student_consent_required"
NOT_FOUND: Final = "not_found"

type FamilyAccess = Literal["allowed", "consent_required", "none"]
type DashboardRoute = Literal["progress", "session_log"]
type RouteBlock = Literal["student_consent_required", "not_found"]
type RangeIssue = tuple[str, str, str]
"""``(query parameter, code, message)``."""

_CENTS: Final = Decimal("0.01")
_HUNDRED: Final = Decimal(100)


class Audience(StrEnum):
    """How the caller relates to the student (A3)."""

    STAFF = "staff"
    CURRENT_TUTOR = "current_tutor"
    FAMILY = "family"
    FORMER_TUTOR = "former_tutor"
    NONE = "none"


FULL_AUDIENCES: Final = frozenset(
    {Audience.STAFF, Audience.CURRENT_TUTOR, Audience.FAMILY}
)
"""Every session of the student on the log, and the progress summary."""


# --- A3: the audience -----------------------------------------------------------------


def progress_audience(
    *,
    staff: bool,
    current_tutor: bool,
    family: FamilyAccess,
    former_tutor: bool,
) -> Audience:
    """A3, the first that applies (a user with several roles gets the union:
    each audience reads a superset of the next). ``staff``: the caller holds
    ``evaluation.manage``; ``current_tutor``: a ``tutor``-role user with the
    student in their current assignments; ``family``: the #79 family check
    (``consent_required``: a linked guardian of an adult without
    ``guardian_academic_access``); ``former_tutor``: a ``tutor``-role user who
    taught an in-scope session of the student (E6) and isn't current."""
    if staff:
        return Audience.STAFF
    if current_tutor:
        return Audience.CURRENT_TUTOR
    if family == "allowed":
        return Audience.FAMILY
    if former_tutor:
        return Audience.FORMER_TUTOR
    return Audience.NONE


def route_block(
    audience: Audience, family: FamilyAccess, route: DashboardRoute
) -> RouteBlock | None:
    """The route's answer for the audience: ``None`` (``200``), ``403
    student_consent_required`` (a linked guardian of an adult without the
    consent, when nothing else allows the caller) or the ``404`` of an
    unknown id. A former tutor reads the session log (their own sessions)
    but not the progress summary (E4)."""
    if audience in FULL_AUDIENCES:
        return None
    if audience is Audience.FORMER_TUTOR and route == "session_log":
        return None
    if family == "consent_required":
        return CONSENT_REQUIRED
    return NOT_FOUND


# --- E6: numbers ----------------------------------------------------------------------


def percent(numerator: int, denominator: int) -> Decimal | None:
    """``numerator / denominator × 100``, half-up to two places, as a
    ``Decimal`` (``None`` when ``denominator`` is 0)."""
    if denominator == 0:
        return None
    return (Decimal(numerator) * _HUNDRED / Decimal(denominator)).quantize(
        _CENTS, rounding=ROUND_HALF_UP
    )


def attendance_percent(present: int, late: int, absent: int) -> Decimal | None:
    """E6: late counts as attended; unrecorded sessions are left out."""
    return percent(present + late, present + late + absent)


def completion_percent(submitted: int, graded: int, missing: int) -> Decimal | None:
    """E6: pending work isn't counted yet."""
    return percent(submitted + graded, submitted + graded + missing)


# --- E6: months -----------------------------------------------------------------------


def month_key(day: date) -> str:
    """The ``YYYY-MM`` of a (center-local) date."""
    return f"{day.year:04d}-{day.month:02d}"


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def month_span(start: date, end: date) -> int:
    """How many calendar months ``[start, end]`` touches (1 for one month)."""
    return _month_index(end) - _month_index(start) + 1


def months_between(start: date, end: date) -> list[str]:
    """Every ``YYYY-MM`` from ``start``'s month to ``end``'s, oldest first
    (empty when ``start`` is after ``end``)."""
    return [
        f"{index // 12:04d}-{index % 12 + 1:02d}"
        for index in range(_month_index(start), _month_index(end) + 1)
    ]


def first_of_month_before(day: date, months: int) -> date:
    """The first day of the month ``months`` months before ``day``'s."""
    index = _month_index(day) - months
    return date(index // 12, index % 12 + 1, 1)


# --- B1: the range --------------------------------------------------------------------


def default_range(
    today: date, *, from_: date | None = None, to: date | None = None
) -> tuple[date, date]:
    """E6: ``to`` defaults to the center's today; ``from`` to the first day
    of the month 11 months before ``to``'s (12 calendar months)."""
    end = today if to is None else to
    start = first_of_month_before(end, DEFAULT_MONTHS - 1) if from_ is None else from_
    return start, end


def progress_range_issue(start: date, end: date, today: date) -> RangeIssue | None:
    """B1, the first that applies: ``range_invalid`` (``from`` after ``to``,
    or before 2000-01-01), ``range_in_future`` (``to`` after the center's
    today), ``range_too_long`` (more than 24 calendar months)."""
    if start > end:
        return ("from", RANGE_INVALID, "'from' is after 'to'.")
    if start < EARLIEST_DATE:
        return ("from", RANGE_INVALID, "'from' is before 2000-01-01.")
    if end > today:
        return ("to", RANGE_IN_FUTURE, "'to' is after today.")
    if month_span(start, end) > MAX_MONTHS:
        return ("from", RANGE_TOO_LONG, "At most 24 calendar months.")
    return None


def log_range_issue(start: date | None, end: date | None) -> RangeIssue | None:
    """C1: ``range_invalid`` when both are given and ``from`` is after
    ``to``."""
    if start is not None and end is not None and start > end:
        return ("from", RANGE_INVALID, "'from' is after 'to'.")
    return None
