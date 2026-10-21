"""Scheduling conflict codes and the pure conflict rules (ADR-0005 D1; #48 B3).

Pure rules, no database. The codes and their severities are exactly the
ADR-0005 invariants table:

- **hard** (never allowed): ``tutor_overlap``, ``room_overlap``,
  ``student_overlap``, ``room_capacity_exceeded``;
- **soft** (allowed with an override, #49): ``tutor_unavailable``,
  ``room_unavailable``, ``center_closed``, ``outside_operating_hours``,
  ``tutor_not_qualified``, ``group_capacity_exceeded``, ``notice_window``;
- **warnings** (never block): ``daily_limit_exceeded``,
  ``weekly_limit_exceeded``, ``break_too_short``. Defined for #50; nothing
  here emits them.

The data source of each check (``scheduling.service.create_class_session``
runs them; :mod:`app.modules.scheduling.booking`):

- ``student_overlap``: the student's sessions in
  ``STUDENT_OCCUPYING_STATES`` that overlap ``[starts_at, ends_at)``, read
  under the per-student advisory lock (:mod:`~app.modules.scheduling.locks`);
- ``tutor_overlap`` / ``room_overlap``: the same pre-check with the
  ``EXCLUDE`` predicates (#47), which stay the backstop;
- ``room_capacity_exceeded`` / ``group_capacity_exceeded``:
  :func:`~app.modules.scheduling.capacity.capacity_violations` (nothing for
  an individual session, D7);
- ``tutor_unavailable``: ``staff.service.is_tutor_available_at`` without the
  ``center_closed`` reason (:func:`tutor_unavailable_reasons`);
- ``room_unavailable``: ``catalog.service.is_classroom_available``;
- ``center_closed``: ``catalog.service.is_center_closed_on`` (one-off
  sessions only, D6);
- ``outside_operating_hours``: :func:`covers_operating_hours` over
  ``administration.service.get_business_settings``;
- ``tutor_not_qualified``: ``staff.service.is_qualified``;
- ``notice_window``: ``administration.service.violates_booking_notice``
  with the policy in force now (#61).

:func:`sort_conflicts` gives the deterministic order of a ``409`` body: hard
first, then the code order of the table above, then the details.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Final, Literal

from app.core.errors import SchedulingConflict
from app.modules.scheduling.capacity import (
    GROUP_CAPACITY_EXCEEDED,
    ROOM_CAPACITY_EXCEEDED,
)

Severity = Literal["hard", "soft"]

STUDENT_OVERLAP: Final = "student_overlap"
TUTOR_OVERLAP: Final = "tutor_overlap"
ROOM_OVERLAP: Final = "room_overlap"
TUTOR_UNAVAILABLE: Final = "tutor_unavailable"
ROOM_UNAVAILABLE: Final = "room_unavailable"
CENTER_CLOSED: Final = "center_closed"
OUTSIDE_OPERATING_HOURS: Final = "outside_operating_hours"
TUTOR_NOT_QUALIFIED: Final = "tutor_not_qualified"
NOTICE_WINDOW: Final = "notice_window"
DAILY_LIMIT_EXCEEDED: Final = "daily_limit_exceeded"
WEEKLY_LIMIT_EXCEEDED: Final = "weekly_limit_exceeded"
BREAK_TOO_SHORT: Final = "break_too_short"

CODE_ORDER: Final[tuple[str, ...]] = (
    TUTOR_OVERLAP,
    ROOM_OVERLAP,
    STUDENT_OVERLAP,
    ROOM_CAPACITY_EXCEEDED,
    TUTOR_UNAVAILABLE,
    ROOM_UNAVAILABLE,
    CENTER_CLOSED,
    OUTSIDE_OPERATING_HOURS,
    TUTOR_NOT_QUALIFIED,
    GROUP_CAPACITY_EXCEEDED,
    NOTICE_WINDOW,
)
"""Hard and soft codes in the order of the ADR-0005 table."""

HARD: Final = frozenset(
    {STUDENT_OVERLAP, TUTOR_OVERLAP, ROOM_OVERLAP, ROOM_CAPACITY_EXCEEDED}
)
SOFT: Final = frozenset(
    {
        TUTOR_UNAVAILABLE,
        ROOM_UNAVAILABLE,
        CENTER_CLOSED,
        OUTSIDE_OPERATING_HOURS,
        TUTOR_NOT_QUALIFIED,
        GROUP_CAPACITY_EXCEEDED,
        NOTICE_WINDOW,
    }
)
WARNING: Final = frozenset(
    {DAILY_LIMIT_EXCEEDED, WEEKLY_LIMIT_EXCEEDED, BREAK_TOO_SHORT}
)

_CLOSURE_REASON: Final = "center_closed"


def severity_of(code: str) -> Severity:
    """``hard`` or ``soft``; a warning or unknown code is a ``ValueError``."""
    if code in HARD:
        return "hard"
    if code in SOFT:
        return "soft"
    raise ValueError(f"not a conflict code: {code}")


@dataclass(frozen=True)
class Conflict:
    """One broken hard or soft rule; ``severity`` follows from ``code``."""

    code: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        severity_of(self.code)

    @property
    def severity(self) -> Severity:
        return severity_of(self.code)

    def as_wire(self) -> SchedulingConflict:
        """The ``conflicts[]`` member of the ``409`` body."""
        return {"code": self.code, "severity": self.severity, "details": self.details}


def _sort_key(conflict: Conflict) -> tuple[int, int, str]:
    return (
        0 if conflict.severity == "hard" else 1,
        CODE_ORDER.index(conflict.code),
        json.dumps(conflict.details, sort_keys=True, default=str),
    )


def sort_conflicts(conflicts: Iterable[Conflict]) -> list[Conflict]:
    """Hard first, then the ADR table order, then the details (as sorted
    JSON): deterministic for tests and the UI."""
    return sorted(conflicts, key=_sort_key)


def covers_operating_hours(
    hours: Iterable[tuple[int, time, time]], weekday: int, start: time, end: time
) -> bool:
    """Whether the ISO ``weekday``'s opening interval ``[opens, closes)``
    covers ``[start, end)``. ``hours`` lists ``(weekday, opens, closes)``; a
    weekday that is not listed is closed. Touching the bounds is inside; a
    local end at midnight is passed as ``time.max`` and is never covered
    (the hours close by 23:59)."""
    for day, opens, closes in hours:
        if day == weekday:
            return opens <= start and end <= closes
    return False


def tutor_unavailable_reasons(reasons: Sequence[str]) -> list[str]:
    """The ``tutor_unavailable`` reasons: the availability reasons without
    ``center_closed``, kept in their order (#45). A closure is reported once,
    as ``center_closed`` (D7)."""
    return [reason for reason in reasons if reason != _CLOSURE_REASON]
