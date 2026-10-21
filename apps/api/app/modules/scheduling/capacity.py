"""Effective capacity of a class session (ADR-0005 D7; #47 B3).

Pure rules, no database. Effective capacity = ``min(group_or_cohort_max,
room_capacity)`` over the values that exist; an individual session has
exactly one student. Above the room capacity is hard
(``room_capacity_exceeded``); above the group or cohort maximum but within
the room capacity is soft (``group_capacity_exceeded``).
"""

from typing import Final

from app.modules.scheduling.states import SessionKind

ROOM_CAPACITY_EXCEEDED: Final = "room_capacity_exceeded"
GROUP_CAPACITY_EXCEEDED: Final = "group_capacity_exceeded"


def effective_capacity(
    kind: str, *, group_max: int | None, room_capacity: int | None
) -> int | None:
    """The most participants the session may have; ``None`` is no limit."""
    if kind == SessionKind.INDIVIDUAL:
        return 1
    limits = [value for value in (group_max, room_capacity) if value is not None]
    return min(limits) if limits else None


def capacity_violations(
    kind: str,
    participant_count: int,
    *,
    group_max: int | None,
    room_capacity: int | None,
) -> tuple[str, ...]:
    """The capacity codes ``participant_count`` breaks (empty when none).

    Nothing for ``individual`` sessions: their single student is a schema
    rule (#48).
    """
    if kind == SessionKind.INDIVIDUAL:
        return ()
    if room_capacity is not None and participant_count > room_capacity:
        return (ROOM_CAPACITY_EXCEEDED,)
    if group_max is not None and participant_count > group_max:
        return (GROUP_CAPACITY_EXCEEDED,)
    return ()
