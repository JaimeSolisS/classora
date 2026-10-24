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
(M4-22) adds :data:`ONCE_CONFIRMED_VISIBLE_STATES` for sessions cancelled
or skipped after the family saw them confirmed (:func:`is_family_visible`)."""

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


ONCE_CONFIRMED_VISIBLE_STATES: Final = frozenset(
    {SessionState.CANCELLED, SessionState.SKIPPED_CLOSURE}
)
"""The states in which a session shows on a role-scoped schedule only when
it was once ``confirmed`` (``class_sessions.first_confirmed_at`` set; #67
A3, ADR-0005 D2: "cancelled or skipped sessions they had already seen as
confirmed")."""

TUTOR_VISIBLE_STATES: Final = frozenset(
    {SessionState.OFFERED, SessionState.CONFIRMED, SessionState.COMPLETED}
)
"""The states in which a session always shows on its tutor's schedule (#67
A3): ``offered`` too (the tutor still has to answer it, linked to
``/offers``); never ``unassigned`` or ``conflict``."""


def _state_of(state: str) -> SessionState | None:
    try:
        return SessionState(state)
    except ValueError:
        return None


def is_family_visible(state: str, *, was_confirmed: bool = False) -> bool:
    """Whether a session in ``state`` shows on a family schedule (#52 B4,
    #67 A3): :data:`FAMILY_VISIBLE_STATES` always, and
    :data:`ONCE_CONFIRMED_VISIBLE_STATES` only when ``was_confirmed``.
    ``unassigned``, ``offered`` and ``conflict`` never do, nor do unknown
    states. The one-argument form (#52) answers for a session that was
    never confirmed, so it stays ``state in FAMILY_VISIBLE_STATES``."""
    found = _state_of(state)
    if found is None:
        return False
    return found in FAMILY_VISIBLE_STATES or (
        was_confirmed and found in ONCE_CONFIRMED_VISIBLE_STATES
    )


def is_tutor_visible(state: str, *, was_confirmed: bool) -> bool:
    """Whether a session in ``state`` shows on its tutor's schedule (#67
    A3): :data:`TUTOR_VISIBLE_STATES` always, and
    :data:`ONCE_CONFIRMED_VISIBLE_STATES` only when ``was_confirmed``;
    ``unassigned``, ``conflict`` and unknown states never."""
    found = _state_of(state)
    if found is None:
        return False
    return found in TUTOR_VISIBLE_STATES or (
        was_confirmed and found in ONCE_CONFIRMED_VISIBLE_STATES
    )
