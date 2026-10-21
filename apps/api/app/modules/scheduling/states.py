"""Class session states, kinds and transitions (ADR-0005 D2, D6; #47 B1).

Pure rules, no database. The occupying sets are the single source of the
exclusion-constraint predicates (``models.py``) and of the application
checks that later issues add (student overlap, #48).
"""

from enum import StrEnum
from typing import Final


class SessionKind(StrEnum):
    INDIVIDUAL = "individual"
    GROUP = "group"
    COURSE = "course"


class SessionState(StrEnum):
    UNASSIGNED = "unassigned"
    OFFERED = "offered"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SKIPPED_CLOSURE = "skipped_closure"
    CONFLICT = "conflict"


TUTOR_OCCUPYING_STATES: Final = frozenset(
    {SessionState.OFFERED, SessionState.CONFIRMED, SessionState.COMPLETED}
)
"""The states in which a session holds its tutor's time (D2)."""

ROOM_OCCUPYING_STATES: Final = frozenset(
    {
        SessionState.UNASSIGNED,
        SessionState.OFFERED,
        SessionState.CONFIRMED,
        SessionState.COMPLETED,
    }
)
"""The states in which a session holds its classroom (D2)."""

STUDENT_OCCUPYING_STATES: Final = ROOM_OCCUPYING_STATES
"""The states in which a session holds its students' time (D2; checked by the
application under an advisory lock, #48)."""

FAMILY_VISIBLE_STATES: Final = frozenset(
    {SessionState.CONFIRMED, SessionState.COMPLETED}
)
"""The states in which a session shows on its students' and guardians'
schedules (ADR-0005 D2 family visibility; #52 B4, E3): only once a tutor has
accepted it. ``unassigned``, ``offered`` and ``conflict`` never show. #67
(M4-22) builds the family views on this set and adds its rule for sessions
cancelled or skipped after the family saw them confirmed."""

FINAL_STATES: Final = frozenset({SessionState.COMPLETED, SessionState.CANCELLED})
"""States with no way out."""

INITIAL_STATES: Final = frozenset(
    {
        SessionState.UNASSIGNED,
        SessionState.OFFERED,
        SessionState.CONFIRMED,
        SessionState.CONFLICT,
        SessionState.SKIPPED_CLOSURE,
    }
)
"""States a session may be created in: ``unassigned``/``offered`` (D2),
``confirmed`` (occurrences of an accepted series, D3), ``conflict`` (D4) and
``skipped_closure`` (D6)."""

ALLOWED_TRANSITIONS: Final[dict[SessionState, frozenset[SessionState]]] = {
    SessionState.UNASSIGNED: frozenset(
        {SessionState.OFFERED, SessionState.CANCELLED, SessionState.SKIPPED_CLOSURE}
    ),
    SessionState.OFFERED: frozenset(
        {
            SessionState.CONFIRMED,
            SessionState.UNASSIGNED,
            SessionState.CANCELLED,
            SessionState.SKIPPED_CLOSURE,
        }
    ),
    SessionState.CONFIRMED: frozenset(
        {SessionState.COMPLETED, SessionState.CANCELLED, SessionState.SKIPPED_CLOSURE}
    ),
    SessionState.CONFLICT: frozenset(
        {
            SessionState.UNASSIGNED,
            SessionState.OFFERED,
            SessionState.CONFIRMED,
            SessionState.CANCELLED,
        }
    ),
    SessionState.SKIPPED_CLOSURE: frozenset(
        {
            SessionState.UNASSIGNED,
            SessionState.OFFERED,
            SessionState.CONFIRMED,
            SessionState.CONFLICT,
        }
    ),
    SessionState.COMPLETED: frozenset(),
    SessionState.CANCELLED: frozenset(),
}
"""ADR-0005 D2 plus the D6 ``skipped_closure`` moves (in from ``unassigned``,
``offered`` and ``confirmed``; out when restored, or to ``conflict`` when
the re-check fails)."""


def can_transition(from_state: str, to_state: str) -> bool:
    """Whether a session may move from ``from_state`` to ``to_state``.
    Unknown states are never allowed."""
    try:
        source = SessionState(from_state)
        target = SessionState(to_state)
    except ValueError:
        return False
    return target in ALLOWED_TRANSITIONS[source]


def is_family_visible(state: str) -> bool:
    """Whether a session in ``state`` shows on a family schedule
    (:data:`FAMILY_VISIBLE_STATES`). Unknown states never do."""
    try:
        return SessionState(state) in FAMILY_VISIBLE_STATES
    except ValueError:
        return False
