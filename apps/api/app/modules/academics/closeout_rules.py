"""Session closeout rules (#77 A5, C1, E4, E5, E7). Pure functions, no I/O.

- :data:`ACTUAL_MINUTES_RANGE` and :data:`SUMMARY_MAX`: the submission
  bounds (the database CHECK on ``class_sessions.actual_minutes`` stays
  0–1440).
- :func:`summary_errors`: ``topics``, ``performance`` and ``next_steps`` are
  required (``homework`` is optional) unless every participant is
  ``absent`` (a no-show), when all four are optional.
- :func:`closeout_block`: why a session can't be closed out now, from plain
  values (the state is compared as text; ``tests/unit/test_closeout_rules.py``
  checks the strings against ``scheduling.states.SessionState``), so this
  module needs no scheduling import (E2).
- :func:`on_behalf_block` (#301 A3): why a reviewer can't close a session
  out on its tutor's behalf now (the same text states, plus an ended
  class).
- :func:`closeout_access`: the record policy of ``GET`` / ``POST
  /api/v1/class-sessions/{id}/closeout`` (the session's **current** tutor
  while they hold the ``tutor`` role, or a ``schedule.view`` holder; anyone
  else the ``404`` of an unknown id) and :func:`sees_summary` (the tutor and
  ``teaching_hours.approve`` holders, E5).
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Final, Literal

from app.core.authz import Principal

ACTUAL_MINUTES_RANGE: Final = (0, 720)
SUMMARY_MAX: Final = 2000
SUMMARY_FIELDS: Final = ("topics", "performance", "homework", "next_steps")
REQUIRED_SUMMARY_FIELDS: Final = ("topics", "performance", "next_steps")

SUMMARY_REQUIRED: Final = "summary_required"
TOO_LONG: Final = "too_long"

type SummaryErrorCode = Literal["summary_required", "too_long"]

# Session states, as text (ADR-0005 D2; the drift test pins them).
CONFIRMED: Final = "confirmed"
COMPLETED: Final = "completed"
NOT_CLOSABLE_STATES: Final = frozenset(
    {"unassigned", "offered", "conflict", "cancelled", "skipped_closure"}
)

ALREADY_SUBMITTED: Final = "closeout_already_submitted"
NOT_CLOSABLE: Final = "session_not_closable"
NOT_STARTED: Final = "session_not_started"
TUTOR_ONLY: Final = "closeout_tutor_only"

type CloseoutBlock = Literal[
    "closeout_already_submitted",
    "session_not_closable",
    "session_not_started",
    "closeout_tutor_only",
]

TUTOR_ROLE: Final = "tutor"
SCHEDULE_VIEW: Final = "schedule.view"
TEACHING_HOURS_APPROVE: Final = "teaching_hours.approve"
"""The identity catalog's codes (repeated here: ADR-0002)."""


def clean_text(value: str | None) -> str | None:
    """A summary field trimmed; whitespace-only or missing is ``None``."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def summary_errors(
    summary: Mapping[str, str | None], *, all_absent: bool
) -> list[tuple[str, SummaryErrorCode]]:
    """``(field, code)`` per summary field, in :data:`SUMMARY_FIELDS` order:
    ``too_long`` over :data:`SUMMARY_MAX` characters after trimming,
    ``summary_required`` for a missing required field unless
    ``all_absent``."""
    errors: list[tuple[str, SummaryErrorCode]] = []
    for field in SUMMARY_FIELDS:
        value = clean_text(summary.get(field))
        if value is None:
            if field in REQUIRED_SUMMARY_FIELDS and not all_absent:
                errors.append((field, SUMMARY_REQUIRED))
        elif len(value) > SUMMARY_MAX:
            errors.append((field, TOO_LONG))
    return errors


def closeout_block(
    state: str, starts_at: datetime, now: datetime, *, has_closeout: bool
) -> CloseoutBlock | None:
    """Why the session can't be closed out now, or ``None`` (A5), in this
    order: a ``completed`` session or an existing closeout; a state other
    than ``confirmed``; a session that hasn't started."""
    if state == COMPLETED or has_closeout:
        return ALREADY_SUBMITTED
    if state != CONFIRMED:
        return NOT_CLOSABLE
    if starts_at > now:
        return NOT_STARTED
    return None


def all_marked_absent(statuses: Mapping[uuid.UUID, str], participants: int) -> bool:
    """Every participant is marked and each is ``absent`` (a no-show)."""
    return (
        participants > 0
        and len(statuses) == participants
        and all(status == "absent" for status in statuses.values())
    )


class CloseoutAccess(Enum):
    """Who the caller is for a session's closeout (C1)."""

    TUTOR = "tutor"
    """The current tutor: reads and submits."""
    STAFF = "staff"
    """A ``schedule.view`` holder who isn't the current tutor: reads;
    ``POST`` answers ``403 closeout_tutor_only``."""
    HIDDEN = "hidden"
    """Anyone else: the ``404`` of an unknown id."""


def is_current_tutor(principal: Principal, tutor_user_id: uuid.UUID | None) -> bool:
    """The caller holds the ``tutor`` role and is the session's current
    tutor (an accepted #55 substitute; never the original tutor after it)."""
    return (
        tutor_user_id is not None
        and TUTOR_ROLE in principal.roles
        and principal.user_id == tutor_user_id
    )


def closeout_access(
    principal: Principal, tutor_user_id: uuid.UUID | None
) -> CloseoutAccess:
    """C1 (any state): the current tutor first, then a ``schedule.view``
    holder; otherwise hidden."""
    if is_current_tutor(principal, tutor_user_id):
        return CloseoutAccess.TUTOR
    if principal.has(SCHEDULE_VIEW):
        return CloseoutAccess.STAFF
    return CloseoutAccess.HIDDEN


def sees_summary(principal: Principal, tutor_user_id: uuid.UUID | None) -> bool:
    """E5: the summary text is shown to the session's current tutor and to
    ``teaching_hours.approve`` holders (Owner, Administrator, Academic
    Coordinator); Finance sees status and minutes only."""
    return is_current_tutor(principal, tutor_user_id) or principal.has(
        TEACHING_HOURS_APPROVE
    )


# --- Review (#78 A3, E7) ---------------------------------------------------------

SUBMITTED: Final = "submitted"
CHANGES_REQUESTED: Final = "changes_requested"
APPROVED: Final = "approved"

OWNER_ROLE: Final = "owner"
"""The identity catalog's code (repeated here: ADR-0002)."""

REASON_MAX: Final = 1000
CORRECTABLE_FIELDS: Final = ("actual_minutes", *SUMMARY_FIELDS)

type ReviewAction = Literal["approve", "request_changes", "correct", "tutor_update"]
"""``tutor_update``: the tutor's ``PUT`` (a resubmission from
``changes_requested`` or a correction request from ``approved``)."""

REVIEW_ACTIONS: Final[Mapping[str, frozenset[ReviewAction]]] = {
    SUBMITTED: frozenset({"approve", "request_changes", "correct"}),
    CHANGES_REQUESTED: frozenset({"tutor_update"}),
    APPROVED: frozenset({"correct", "tutor_update"}),
}
"""The actions each closeout status takes (the #78 status machine)."""

NOT_SUBMITTED: Final = "closeout_not_submitted"
WITH_TUTOR: Final = "closeout_with_tutor"
UNDER_REVIEW: Final = "closeout_under_review"

type ReviewBlock = Literal[
    "closeout_not_submitted", "closeout_with_tutor", "closeout_under_review"
]

type KindOfReview = Literal["first", "resubmission", "correction"]


def review_block(status: str, action: ReviewAction) -> ReviewBlock | None:
    """Why ``action`` can't run on a closeout in ``status``, or ``None``:
    ``closeout_not_submitted`` (approve or request changes when not
    ``submitted``), ``closeout_with_tutor`` (a staff correction while
    ``changes_requested``), ``closeout_under_review`` (the tutor's ``PUT``
    while ``submitted``)."""
    if action in REVIEW_ACTIONS.get(status, frozenset()):
        return None
    if action in ("approve", "request_changes"):
        return NOT_SUBMITTED
    if action == "correct":
        return WITH_TUTOR
    return UNDER_REVIEW


def is_self_review(
    principal: Principal,
    tutor_user_id: uuid.UUID,
    on_behalf_by_user_id: uuid.UUID | None = None,
) -> bool:
    """E7: the caller submitted the closeout (the tutor of record, or the
    reviewer who made the latest submission on the tutor's behalf, #301 E5)
    and isn't the Owner (the final payroll authority, so a one-person center
    still works)."""
    return (
        principal.user_id in (tutor_user_id, on_behalf_by_user_id)
        and OWNER_ROLE not in principal.roles
    )


def corrected_fields(
    current: Mapping[str, int | str | None], proposed: Mapping[str, int | str | None]
) -> list[str]:
    """The fields of :data:`CORRECTABLE_FIELDS` that ``proposed`` (only the
    given members) changes against ``current``, in that order."""
    return [
        field
        for field in CORRECTABLE_FIELDS
        if field in proposed and proposed[field] != current.get(field)
    ]


def attendance_changed(
    snapshot: Mapping[str, str], live: Mapping[uuid.UUID, str]
) -> bool:
    """The live statuses differ from an approval's snapshot (a student
    marked, unmarked or with another status)."""
    return dict(snapshot) != {str(student): status for student, status in live.items()}


def kind_of_review(
    *, has_approval: bool, changes_requested_before: bool
) -> KindOfReview:
    """What a pending inbox item asks (C4): a correction of an approved
    closeout, a resubmission after changes were requested, or a first
    review."""
    if has_approval:
        return "correction"
    if changes_requested_before:
        return "resubmission"
    return "first"


# --- On behalf of the tutor (#301 A3) ----------------------------------------------

ON_BEHALF_OWN_SESSION: Final = "closeout_on_behalf_own_session"
NOT_ENDED: Final = "session_not_ended"

type OnBehalfBlock = Literal[
    "closeout_on_behalf_own_session",
    "closeout_already_submitted",
    "session_not_closable",
    "session_not_ended",
]


def on_behalf_block(
    state: str,
    ends_at: datetime,
    now: datetime,
    *,
    has_closeout: bool,
    caller_is_tutor: bool,
) -> OnBehalfBlock | None:
    """Why a reviewer can't close the session out on its tutor's behalf now,
    or ``None`` (#301 A3, E2), in this order: the caller tutors the session;
    a ``completed`` session or an existing closeout; a state other than
    ``confirmed``; a class that hasn't ended (``ends_at > now``)."""
    if caller_is_tutor:
        return ON_BEHALF_OWN_SESSION
    if state == COMPLETED or has_closeout:
        return ALREADY_SUBMITTED
    if state != CONFIRMED:
        return NOT_CLOSABLE
    if ends_at > now:
        return NOT_ENDED
    return None
