"""Calendar markers (#57 A5; ADR-0005 D6; #198): read-time flags on a
session, computed on every read and never stored.

- :data:`CENTER_CLOSED`: the session is ``unassigned``, ``offered`` or
  ``confirmed`` and its **local date** (center zone) is inside an active
  closure. This is the ADR-0005 D6 marker for one-off sessions, which a
  closure never changes (a series occurrence is ``skipped_closure`` instead).
- :data:`TUTOR_NOT_QUALIFIED`: the session is ``offered`` or ``confirmed``,
  has a tutor, starts at or after now, and the tutor holds no approved
  qualification for its subject (#198: a revocation leaves future sessions
  in place). Past and ``completed`` sessions are history and never get it.
- :data:`OVERRIDDEN` (#58 B2, handed over by #57 E1): the session has at
  least one ``class_session_overrides`` row, in **any** state: it was booked
  or moved with an override of a soft rule, a historical fact.

Pure rules: the caller loads the closures and the qualified pairs once for
the whole read (``catalog.service.closures_between``,
``staff.service.qualified_pairs``); ``overridden`` is an ``EXISTS`` in the
sessions statement.
"""

from datetime import date, datetime
from typing import Final

from app.modules.scheduling.schemas import MarkerCode
from app.modules.scheduling.states import SessionState

CENTER_CLOSED: Final = "center_closed"
TUTOR_NOT_QUALIFIED: Final = "tutor_not_qualified"
OVERRIDDEN: Final = "overridden"

MARKER_ORDER: Final[tuple[MarkerCode, ...]] = (
    CENTER_CLOSED,
    TUTOR_NOT_QUALIFIED,
    OVERRIDDEN,
)
"""The fixed order of ``markers`` on a calendar item."""

CLOSURE_MARKED_STATES: Final = frozenset(
    {SessionState.UNASSIGNED, SessionState.OFFERED, SessionState.CONFIRMED}
)
"""The states that get :data:`CENTER_CLOSED` on a closure's local date."""

QUALIFICATION_MARKED_STATES: Final = frozenset(
    {SessionState.OFFERED, SessionState.CONFIRMED}
)
"""The states that get :data:`TUTOR_NOT_QUALIFIED` (future sessions only)."""


def gets_closure_marker(state: str, local_date: date, closed: frozenset[date]) -> bool:
    """Whether a session in ``state`` on ``local_date`` (center zone) is
    marked :data:`CENTER_CLOSED`; ``closed`` holds the closed local dates."""
    return state in CLOSURE_MARKED_STATES and local_date in closed


def needs_qualification_check(
    state: str, has_tutor: bool, starts_at: datetime, now: datetime
) -> bool:
    """Whether the tutor's qualification decides :data:`TUTOR_NOT_QUALIFIED`
    for this session: ``offered``/``confirmed``, with a tutor, starting at or
    after ``now``."""
    return state in QUALIFICATION_MARKED_STATES and has_tutor and starts_at >= now


def markers_of(
    *, closed: bool, not_qualified: bool, overridden: bool
) -> list[MarkerCode]:
    """The markers in :data:`MARKER_ORDER`."""
    found = {
        CENTER_CLOSED: closed,
        TUTOR_NOT_QUALIFIED: not_qualified,
        OVERRIDDEN: overridden,
    }
    return [code for code in MARKER_ORDER if found[code]]
