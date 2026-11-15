"""When a reassignment flag is open (M4-18 #63 E1; ADR-0005 D2). Pure
rules and the matching SQL condition, no I/O.

A ``class_session_reassignment_flags`` row is never updated: it is **open**
while all of these hold, and closed otherwise:

- its session starts in the future (``starts_at > now``);
- the session is ``unassigned``, ``offered`` or ``confirmed``;
- the session's tutor is still the flagged tutor, or there is none
  (``unassigned``).

So re-offering the session to another tutor (#52 C2), substituting the
tutor (#55), cancelling the session (#55/#62) or the session starting
closes it, with no code in those paths, and nothing is resolved by hand.

:func:`is_flag_open` is the rule; :func:`open_flag_condition` is the same
rule as a SQL condition over ``class_sessions`` and the flags (the calendar
marker and :func:`~app.modules.scheduling.reassignment.open_reassignment_flags`
use it; the session detail applies :func:`is_flag_open`).
"""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.modules.scheduling.models import ClassSession, ClassSessionReassignmentFlag
from app.modules.scheduling.states import SessionState

OPEN_FLAG_STATES: Final = frozenset(
    {SessionState.UNASSIGNED, SessionState.OFFERED, SessionState.CONFIRMED}
)


def is_flag_open(
    *,
    flag_tutor_user_id: uuid.UUID,
    state: str,
    session_tutor_user_id: uuid.UUID | None,
    starts_at: datetime,
    now: datetime,
) -> bool:
    """Whether a flag of ``flag_tutor_user_id`` on a session in ``state``
    with ``session_tutor_user_id``, starting at ``starts_at``, is open."""
    return (
        starts_at > now
        and state in OPEN_FLAG_STATES
        and session_tutor_user_id in (None, flag_tutor_user_id)
    )


def flag_tutor_matches() -> ColumnElement[bool]:
    """The tutor part of the rule: the session's tutor is the flagged one,
    or none."""
    return or_(
        ClassSession.tutor_user_id.is_(None),
        ClassSession.tutor_user_id == ClassSessionReassignmentFlag.tutor_user_id,
    )


def open_flag_condition(now: datetime) -> ColumnElement[bool]:
    """:func:`is_flag_open` over the joined (or correlated) session and
    flag rows."""
    return and_(
        ClassSessionReassignmentFlag.class_session_id == ClassSession.id,
        ClassSession.starts_at > now,
        ClassSession.state.in_([state.value for state in OPEN_FLAG_STATES]),
        flag_tutor_matches(),
    )
