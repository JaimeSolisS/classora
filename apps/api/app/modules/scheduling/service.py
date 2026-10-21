"""The scheduling module's public API (ADR-0002; #47 M4-02, ADR-0005).

Pure rules: the states, kinds, occupying sets and transitions
(:mod:`~app.modules.scheduling.states`), the time bounds
(:mod:`~app.modules.scheduling.times`) and the effective capacity
(:mod:`~app.modules.scheduling.capacity`). Writes
(:mod:`~app.modules.scheduling.sessions`): :func:`flush_class_sessions`
(exclusion violations → :class:`SessionOverlapError`),
:func:`transition_class_session` and the time-zone check
:func:`has_class_sessions`, registered as :data:`TIME_ZONE_DEPENDENCY`
(``scheduling.class_sessions``) with
``administration.service.register_time_zone_dependency`` in ``app/wiring.py``.

One-off individual sessions (#48, implemented in
:mod:`~app.modules.scheduling.booking`): :func:`create_class_session` (the
per-student lock, the zone ``FOR SHARE``, the ``422`` bounds and
references, every ADR-0005 conflict check, then ``offered`` / ``unassigned``)
and :func:`get_class_session`. The conflict codes, severities and pure
rules are in :mod:`~app.modules.scheduling.conflicts` (with each check's data
source); :func:`find_conflicts` runs them for one body. The student lock
(:func:`lock_students`, :mod:`~app.modules.scheduling.locks`) must be taken
first by every writer that adds a participant or moves a session in time.

The #47 errors are plain exceptions; the create maps them into ``409
scheduling_conflict`` (:class:`SchedulingConflictError`) or ``422``
(:class:`SessionValidationError`). Every function runs in the caller's Unit
of Work and never commits.
"""

from app.modules.scheduling.booking import (
    create_class_session as create_class_session,
)
from app.modules.scheduling.booking import find_conflicts as find_conflicts
from app.modules.scheduling.booking import (
    get_class_session as get_class_session,
)
from app.modules.scheduling.capacity import (
    GROUP_CAPACITY_EXCEEDED as GROUP_CAPACITY_EXCEEDED,
)
from app.modules.scheduling.capacity import (
    ROOM_CAPACITY_EXCEEDED as ROOM_CAPACITY_EXCEEDED,
)
from app.modules.scheduling.capacity import (
    capacity_violations as capacity_violations,
)
from app.modules.scheduling.capacity import (
    effective_capacity as effective_capacity,
)
from app.modules.scheduling.conflicts import HARD as HARD
from app.modules.scheduling.conflicts import SOFT as SOFT
from app.modules.scheduling.conflicts import WARNING as WARNING
from app.modules.scheduling.conflicts import Conflict as Conflict
from app.modules.scheduling.conflicts import sort_conflicts as sort_conflicts
from app.modules.scheduling.errors import (
    InvalidSessionTimesError as InvalidSessionTimesError,
)
from app.modules.scheduling.errors import (
    InvalidSessionTransitionError as InvalidSessionTransitionError,
)
from app.modules.scheduling.errors import (
    SchedulingConflictError as SchedulingConflictError,
)
from app.modules.scheduling.errors import (
    SessionOverlapError as SessionOverlapError,
)
from app.modules.scheduling.errors import (
    SessionValidationError as SessionValidationError,
)
from app.modules.scheduling.locks import lock_students as lock_students
from app.modules.scheduling.sessions import (
    TIME_ZONE_DEPENDENCY as TIME_ZONE_DEPENDENCY,
)
from app.modules.scheduling.sessions import (
    flush_class_sessions as flush_class_sessions,
)
from app.modules.scheduling.sessions import (
    has_class_sessions as has_class_sessions,
)
from app.modules.scheduling.sessions import (
    transition_class_session as transition_class_session,
)
from app.modules.scheduling.states import (
    ALLOWED_TRANSITIONS as ALLOWED_TRANSITIONS,
)
from app.modules.scheduling.states import FINAL_STATES as FINAL_STATES
from app.modules.scheduling.states import INITIAL_STATES as INITIAL_STATES
from app.modules.scheduling.states import (
    ROOM_OCCUPYING_STATES as ROOM_OCCUPYING_STATES,
)
from app.modules.scheduling.states import (
    STUDENT_OCCUPYING_STATES as STUDENT_OCCUPYING_STATES,
)
from app.modules.scheduling.states import (
    TUTOR_OCCUPYING_STATES as TUTOR_OCCUPYING_STATES,
)
from app.modules.scheduling.states import SessionKind as SessionKind
from app.modules.scheduling.states import SessionState as SessionState
from app.modules.scheduling.states import can_transition as can_transition
from app.modules.scheduling.times import (
    scheduled_minutes as scheduled_minutes,
)
from app.modules.scheduling.times import (
    validate_session_bounds as validate_session_bounds,
)
