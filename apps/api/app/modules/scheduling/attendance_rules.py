"""Attendance rules (#76 A3, B1, E2; spec §3 "Attendance, session
completion and changes"; ADR-0005 D2, D9).

Pure functions, no I/O.

- :func:`attendance_block`: when attendance can be written. Only a
  ``confirmed`` or ``completed`` session that has started
  (``starts_at <= now``; no deadline afterwards). Once a session is
  ``completed`` (only closeout, #77, does that) the tutor can no longer
  mark; staff corrections still can (E2).
- :func:`attendance_access`: the record policy of ``GET`` / ``PUT
  /api/v1/class-sessions/{id}/attendance``. The session's **current** tutor
  (``tutor_user_id``, while the caller holds the ``tutor`` role: after an
  accepted #55 substitution only the substitute) marks; a holder of
  ``schedule.view`` reads; anyone else gets the ``404`` of an unknown id.
  Marking attendance never changes the session's state (E2).
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Final, Literal

from app.core.authz import Principal
from app.modules.scheduling.permissions import teaching_tutor_id
from app.modules.scheduling.states import SessionState

ATTENDANCE_STATUSES: Final = ("present", "late", "absent")
"""The statuses a participant can be marked with (no "excused")."""

type AttendanceStatus = Literal["present", "late", "absent"]

RECORDABLE_STATES: Final = frozenset({SessionState.CONFIRMED, SessionState.COMPLETED})
"""The states in which attendance can be written."""

NOT_RECORDABLE: Final = "attendance_not_recordable"
NOT_STARTED: Final = "attendance_not_started"
LOCKED: Final = "attendance_locked"

type AttendanceBlock = Literal[
    "attendance_not_recordable", "attendance_not_started", "attendance_locked"
]

SCHEDULE_VIEW: Final = "schedule.view"
ATTENDANCE_CORRECT: Final = "attendance.correct"


def attendance_block(
    state: str,
    starts_at: datetime,
    now: datetime,
    *,
    by_tutor: bool,
    tutor_unlocked: bool = False,
) -> AttendanceBlock | None:
    """Why attendance can't be written now, or ``None`` (A3), in this
    order: a state outside :data:`RECORDABLE_STATES`; a session that hasn't
    started; for the tutor (``by_tutor``), a ``completed`` session unless
    ``tutor_unlocked`` (#78 E6: its closeout came back with changes
    requested)."""
    if state not in RECORDABLE_STATES:
        return NOT_RECORDABLE
    if starts_at > now:
        return NOT_STARTED
    if by_tutor and state == SessionState.COMPLETED and not tutor_unlocked:
        return LOCKED
    return None


class AttendanceAccess(Enum):
    """Who the caller is for a session's attendance (B1)."""

    TUTOR = "tutor"
    """The current tutor: reads and marks."""
    STAFF = "staff"
    """A holder of ``schedule.view`` who isn't the current tutor: reads;
    ``PUT`` answers ``403 attendance_tutor_only``."""
    HIDDEN = "hidden"
    """Anyone else: the ``404`` of an unknown id."""


def is_current_tutor(principal: Principal, tutor_user_id: uuid.UUID | None) -> bool:
    """The caller holds the ``tutor`` role and is the session's current
    tutor."""
    tutor_id = teaching_tutor_id(principal)
    return tutor_id is not None and tutor_id == tutor_user_id


def attendance_access(
    principal: Principal, tutor_user_id: uuid.UUID | None
) -> AttendanceAccess:
    """B1 (any session state): the current tutor first, then a
    ``schedule.view`` holder; otherwise hidden."""
    if is_current_tutor(principal, tutor_user_id):
        return AttendanceAccess.TUTOR
    if principal.has(SCHEDULE_VIEW):
        return AttendanceAccess.STAFF
    return AttendanceAccess.HIDDEN


def can_correct(principal: Principal) -> bool:
    """The caller holds ``attendance.correct`` (Owner, Administrator,
    Academic Coordinator; #76 E1)."""
    return principal.has(ATTENDANCE_CORRECT)
