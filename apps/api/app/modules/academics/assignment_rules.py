"""Homework assignment rules (#82 A3, C, D, E). Pure functions, no I/O.

"Assignment" here means **homework** a tutor sets (not a tutor's current
student assignment, ``scheduling.service.student_ids_for_tutor``).

- The limits (:data:`RECIPIENTS_MAX`, :data:`TITLE_MAX`, …); the database
  CHECKs repeat the stored ones.
- :func:`deadline_instant`: the center-local ``due_on`` + ``due_time``
  (``None``: the end of that day) as a UTC instant in the center's zone,
  ``fold=0`` (ADR-0004: an ambiguous local time takes the first instant, a
  nonexistent one moves forward by the gap). No time-zone dependency (E3).
- :func:`is_late` and :func:`submission_state`: computed at read time, never
  stored, so moving the deadline changes them (E3). Exactly at the deadline
  is on time.
- :func:`validate_url` (E4): absolute ``http``/``https`` with a host, no
  user-info, no whitespace or control characters, at most 2000 characters.
  The server never fetches it.
- :func:`score_percent` (E5): ``Decimal``, ``ROUND_HALF_UP`` to two places.
- :func:`assignment_relation` (E6): how the caller relates to one
  assignment and one recipient; :func:`access_block` (the ``403`` / ``404``
  of a write, before the lock), :func:`state_block` (the ``409`` against
  the locked rows) and :func:`write_block`, which combines them for the
  ``can_*`` flags.
"""

import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Final, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

RECIPIENTS_MAX: Final = 100
TITLE_MAX: Final = 200
INSTRUCTIONS_MAX: Final = 4000
TEXT_MAX: Final = 10000
URL_MAX: Final = 2000
FEEDBACK_MAX: Final = 4000
REASON_MAX: Final = 500
DUE_MAX_DAYS: Final = 365
LIST_LIMIT_MAX: Final = 50
LIST_LIMIT_DEFAULT: Final = 20
MAX_SCORE_MIN: Final = Decimal("0.5")
MAX_SCORE_MAX: Final = Decimal("1000")

END_OF_DAY: Final = time.max
"""A ``NULL`` ``due_time``: 23:59:59.999999 local (E3)."""

PENDING: Final = "pending"
MISSING: Final = "missing"
SUBMITTED: Final = "submitted"
GRADED: Final = "graded"
STATES: Final = (PENDING, MISSING, SUBMITTED, GRADED)

VIA_STUDENT: Final = "student"
VIA_TUTOR: Final = "tutor"
SUBMITTED_VIA: Final = (VIA_STUDENT, VIA_TUTOR)

ASSIGNABLE_STATES: Final = frozenset({"confirmed", "completed"})
"""C2 step 5: a linked session is ``confirmed`` or ``completed`` (compared
as text: academics imports no scheduling; the unit test checks them
against ``scheduling.states.SessionState``)."""

TUTOR_ROLE: Final = "tutor"
ASSIGNMENT_REVIEW: Final = "assignment.review"
"""The identity catalog's codes (repeated here: ADR-0002)."""

NOT_FOUND: Final = "not_found"
ASSIGNMENT_CREATOR_ONLY: Final = "assignment_creator_only"
ASSIGNMENT_READ_ONLY: Final = "assignment_read_only"
SUBMISSION_STUDENT_ONLY: Final = "submission_student_only"
SUBMISSION_GRADED: Final = "submission_graded"
SUBMISSION_LOCKED: Final = "submission_locked"
SUBMISSION_REQUIRED: Final = "submission_required"
ASSIGNMENT_HAS_SUBMISSIONS: Final = "assignment_has_submissions"
URL_INVALID: Final = "url_invalid"

type State = Literal["pending", "missing", "submitted", "graded"]
type AssignmentAction = Literal["edit", "delete", "submit", "grade"]
type AccessBlock = Literal[
    "not_found",
    "assignment_creator_only",
    "assignment_read_only",
    "submission_student_only",
]
type StateBlock = Literal[
    "submission_graded",
    "submission_locked",
    "submission_required",
    "assignment_has_submissions",
]

ACTIONS: Final[tuple[AssignmentAction, ...]] = ("edit", "delete", "submit", "grade")

_CENT: Final = Decimal("0.01")
_HUNDRED: Final = Decimal(100)


class AssignmentRelation(Enum):
    """How the caller relates to one assignment and one of its recipients
    (E6). The first that applies wins."""

    CREATOR = "creator"
    FORMER_CREATOR = "former_creator"
    STUDENT_SELF = "student_self"
    REVIEWER = "reviewer"
    ASSIGNED_TUTOR = "assigned_tutor"
    GUARDIAN = "guardian"
    NONE = "none"


# --- E3: deadline, late, missing ------------------------------------------------------


def deadline_instant(due_on: date, due_time: time | None, zone: str) -> datetime:
    """The deadline as a UTC instant: ``due_on`` at ``due_time`` (``None``:
    the end of the day) in ``zone``, ``fold=0``. A nonexistent local time
    (spring forward) moves forward by the gap; an ambiguous one (fall back)
    takes the first instant (ADR-0004)."""
    local = datetime.combine(due_on, END_OF_DAY if due_time is None else due_time)
    return local.replace(tzinfo=ZoneInfo(zone), fold=0).astimezone(UTC)


def is_late(submitted_at: datetime | None, deadline: datetime) -> bool:
    """Submitted after the deadline (exactly at it is on time)."""
    return submitted_at is not None and submitted_at > deadline


def submission_state(
    submitted_at: datetime | None,
    graded_at: datetime | None,
    deadline: datetime,
    now: datetime,
) -> State:
    """``graded``, ``submitted``, ``missing`` (nothing submitted and the
    deadline passed) or ``pending``."""
    if graded_at is not None:
        return GRADED
    if submitted_at is not None:
        return SUBMITTED
    return MISSING if now > deadline else PENDING


def due_window(today: date) -> tuple[date, date]:
    """The ``due_on`` a create accepts: today to today + 365 days."""
    return today, today + timedelta(days=DUE_MAX_DAYS)


# --- E4: submissions ------------------------------------------------------------------


def _bad_character(value: str) -> bool:
    return any(
        ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp")
        for ch in value
    )


def validate_url(value: str) -> str | None:
    """``None`` when ``value`` is an acceptable link, else ``url_invalid``:
    absolute, scheme ``http`` or ``https`` (any case), a host, no user-info
    (``user@``), no whitespace or control characters, a valid port, at most
    :data:`URL_MAX` characters."""
    if not value or len(value) > URL_MAX or _bad_character(value):
        return URL_INVALID
    try:
        parts = urlsplit(value)
        _ = parts.port  # an invalid port raises ValueError
    except ValueError:
        return URL_INVALID
    if parts.scheme.lower() not in ("http", "https"):
        return URL_INVALID
    if not parts.netloc or "@" in parts.netloc or not parts.hostname:
        return URL_INVALID
    if "\\" in value:
        return URL_INVALID
    return None


# --- E5: grades -----------------------------------------------------------------------


def to_cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def has_cents_only(value: Decimal) -> bool:
    """At most two decimal places."""
    return value.is_finite() and value == value.quantize(_CENT)


def score_percent(score: Decimal, max_score: Decimal) -> Decimal:
    """``score / max_score × 100``, half-up to two places. ``Decimal`` only
    (a float raises ``TypeError``)."""
    if not isinstance(score, Decimal) or not isinstance(max_score, Decimal):
        raise TypeError("score_percent takes Decimal values only")
    if max_score <= 0:
        raise ValueError("max_score must be positive")
    return to_cents(score * _HUNDRED / max_score)


def average_percent(percents: list[Decimal]) -> Decimal | None:
    """The mean of ``score_percent`` values, half-up to two places
    (``None`` for none; E9)."""
    if not percents:
        return None
    return to_cents(sum(percents, Decimal(0)) / Decimal(len(percents)))


# --- E6: relation and blocks ----------------------------------------------------------


def assignment_relation(
    *,
    tutor: bool,
    is_creator: bool,
    student_assigned: bool,
    reviewer: bool,
    student_self: bool = False,
    guardian: bool = False,
) -> AssignmentRelation:
    """E6. ``student_assigned``: the recipient (for an assignment-wide
    write, any recipient) is one of the caller's current students
    (``False`` without the ``tutor`` role); ``student_self``: the recipient
    is the caller's own student profile; ``guardian``: the #67 family rule
    allows the caller as a guardian."""
    if tutor and is_creator:
        return (
            AssignmentRelation.CREATOR
            if student_assigned
            else AssignmentRelation.FORMER_CREATOR
        )
    if student_self:
        return AssignmentRelation.STUDENT_SELF
    if reviewer:
        return AssignmentRelation.REVIEWER
    if tutor and student_assigned:
        return AssignmentRelation.ASSIGNED_TUTOR
    if guardian:
        return AssignmentRelation.GUARDIAN
    return AssignmentRelation.NONE


def access_block(
    relation: AssignmentRelation, action: AssignmentAction
) -> AccessBlock | None:
    """The ``403`` / ``404`` of a write (C3 step 4), by the matrix:

    - the creator (recipient still a current student) does everything; a
      former creator gets ``assignment_read_only``;
    - the student submits for themself (``submit``), nothing else;
    - a reviewer gets ``assignment_creator_only``; another tutor of the
      student too on ``submit`` / ``grade`` and the ``404`` on ``edit`` /
      ``delete``;
    - a guardian with read access ``submission_student_only`` on
      ``submit``, the ``404`` otherwise;
    - anyone else the ``404`` of an unknown id."""
    if relation is AssignmentRelation.CREATOR:
        return None
    if relation is AssignmentRelation.FORMER_CREATOR:
        return ASSIGNMENT_READ_ONLY
    if relation is AssignmentRelation.STUDENT_SELF:
        return None if action == "submit" else NOT_FOUND
    if relation is AssignmentRelation.REVIEWER:
        return ASSIGNMENT_CREATOR_ONLY
    if relation is AssignmentRelation.ASSIGNED_TUTOR:
        return ASSIGNMENT_CREATOR_ONLY if action in ("submit", "grade") else NOT_FOUND
    if relation is AssignmentRelation.GUARDIAN:
        return SUBMISSION_STUDENT_ONLY if action == "submit" else NOT_FOUND
    return NOT_FOUND


def state_block(
    action: AssignmentAction,
    *,
    student_path: bool = False,
    submitted: bool = False,
    graded: bool = False,
    deadline_passed: bool = False,
    has_submissions: bool = False,
) -> StateBlock | None:
    """The ``409`` against the locked rows: a graded submission never
    changes; a student's existing submission locks at the deadline (a
    missing one may still be submitted late, E4); only a submitted row is
    graded; an assignment with a submission is never deleted."""
    if action == "submit":
        if graded:
            return SUBMISSION_GRADED
        if student_path and submitted and deadline_passed:
            return SUBMISSION_LOCKED
        return None
    if action == "grade":
        return None if submitted else SUBMISSION_REQUIRED
    if action == "delete":
        return ASSIGNMENT_HAS_SUBMISSIONS if has_submissions else None
    return None


def write_block(
    relation: AssignmentRelation,
    action: AssignmentAction,
    *,
    deleted: bool = False,
    submitted: bool = False,
    graded: bool = False,
    deadline_passed: bool = False,
    has_submissions: bool = False,
) -> AccessBlock | StateBlock | None:
    """Every reason a write would be refused, in the route order (a deleted
    assignment is ``not_found``). ``None``: the ``can_*`` flag is true."""
    if deleted:
        return NOT_FOUND
    blocked = access_block(relation, action)
    if blocked is not None:
        return blocked
    return state_block(
        action,
        student_path=relation is AssignmentRelation.STUDENT_SELF,
        submitted=submitted,
        graded=graded,
        deadline_passed=deadline_passed,
        has_submissions=has_submissions,
    )


def clean(value: str | None) -> str | None:
    """Trimmed; whitespace-only or missing is ``None``."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
