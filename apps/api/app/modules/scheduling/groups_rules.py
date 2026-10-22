"""The pure rules of ongoing tutoring groups (#56 B; spec §3, W1.5;
ADR-0005 D1, D2, D7).

No database. :mod:`~app.modules.scheduling.groups` loads the data and calls
these, so the group create, the enrollments and the withdrawals share one
implementation of each rule:

- :func:`group_capacity_conflicts`: the group maximum against the active
  enrollments (soft ``group_capacity_exceeded``, overridable with
  ``schedule.override``). It goes through #47's
  :func:`~app.modules.scheduling.capacity.capacity_violations`, so the D7
  rule has one implementation.
- :func:`room_capacity_conflicts`: each future occupying occurrence's room
  against its participant count after the change (hard
  ``room_capacity_exceeded``, never overridable). An occurrence without a
  room has no room limit.
- :func:`affected_occurrences` (**the mid-term rule**, B3): an enrollment
  change touches only the group's occurrences that **start after now**
  (``starts_at > now``; a row starting exactly now has started) in
  ``unassigned``, ``offered``, ``confirmed``, ``conflict`` or
  ``skipped_closure``. ``completed``, ``cancelled`` and started occurrences
  keep their participants (W1.5). The checks (student overlap, room
  capacity) apply only to the occupying ones (:func:`is_checked`:
  ``unassigned``, ``offered``, ``confirmed``, D2); ``conflict`` and
  ``skipped_closure`` rows take the participant without checks, because the
  ``scheduling.occurrence_conflict`` approve (#53 G2) and the closure
  restore (#232) re-check every participant.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from app.modules.scheduling.capacity import (
    GROUP_CAPACITY_EXCEEDED,
    ROOM_CAPACITY_EXCEEDED,
    capacity_violations,
)
from app.modules.scheduling.conflicts import Conflict
from app.modules.scheduling.states import (
    ROOM_OCCUPYING_STATES,
    SessionKind,
    SessionState,
)

AFFECTED_STATES: Final = frozenset(
    {
        SessionState.UNASSIGNED,
        SessionState.OFFERED,
        SessionState.CONFIRMED,
        SessionState.CONFLICT,
        SessionState.SKIPPED_CLOSURE,
    }
)
"""The states an enrollment change reaches (B3)."""

CHECKED_STATES: Final = AFFECTED_STATES & ROOM_OCCUPYING_STATES
"""The affected states whose occurrences are checked (they hold the room and
the students, ADR-0005 D2): ``unassigned``, ``offered``, ``confirmed``."""


@dataclass(frozen=True)
class GroupOccurrence:
    """What the rules need of one occurrence of a group."""

    class_session_id: uuid.UUID
    state: str
    starts_at: datetime
    local_date: date
    classroom_id: uuid.UUID | None
    room_capacity: int | None
    """The room's capacity; ``None`` without a room."""


def group_capacity_conflicts(
    active_count_after: int, *, max_students: int, group_id: uuid.UUID
) -> list[Conflict]:
    """One soft ``group_capacity_exceeded`` when ``active_count_after`` is
    above ``max_students`` (B1), else nothing."""
    codes = capacity_violations(
        SessionKind.GROUP,
        active_count_after,
        group_max=max_students,
        room_capacity=None,
    )
    if GROUP_CAPACITY_EXCEEDED not in codes:
        return []
    return [
        Conflict(
            GROUP_CAPACITY_EXCEEDED,
            {
                "group_id": str(group_id),
                "max_students": max_students,
                "enrolled_count": active_count_after,
            },
        )
    ]


def _iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def room_capacity_conflicts(
    occurrences: Iterable[tuple[GroupOccurrence, int]],
) -> list[Conflict]:
    """One hard ``room_capacity_exceeded`` per occurrence whose room holds
    fewer than its participant count after the change (B2). ``occurrences``
    pairs each occupying occurrence with that count; a room-less one never
    conflicts."""
    conflicts: list[Conflict] = []
    for occurrence, participant_count in occurrences:
        codes = capacity_violations(
            SessionKind.GROUP,
            participant_count,
            group_max=None,
            room_capacity=occurrence.room_capacity,
        )
        if ROOM_CAPACITY_EXCEEDED in codes:
            conflicts.append(
                Conflict(
                    ROOM_CAPACITY_EXCEEDED,
                    {
                        "class_session_id": str(occurrence.class_session_id),
                        "occurrence_start": _iso_z(occurrence.starts_at),
                        "date": occurrence.local_date.isoformat(),
                        "classroom_id": str(occurrence.classroom_id),
                        "room_capacity": occurrence.room_capacity,
                        "participant_count": participant_count,
                    },
                )
            )
    return conflicts


def is_affected(state: str, starts_at: datetime, now: datetime) -> bool:
    """Whether an enrollment change at ``now`` reaches an occurrence (B3):
    not started (``starts_at > now``) and in :data:`AFFECTED_STATES`."""
    return starts_at > now and state in AFFECTED_STATES


def is_checked(state: str) -> bool:
    """Whether an affected occurrence is checked (an occupying state)."""
    return state in CHECKED_STATES


def affected_occurrences[T: GroupOccurrence](
    occurrences: Sequence[T], now: datetime
) -> list[T]:
    """The occurrences an enrollment change at ``now`` reaches (B3), in
    their order."""
    return [o for o in occurrences if is_affected(o.state, o.starts_at, now)]
