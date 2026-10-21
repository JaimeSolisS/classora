"""Scheduling domain errors (#47 C2, B2).

Plain exceptions, not :class:`~app.core.errors.AppError`: nothing is
reachable over HTTP yet. M4-03 (#48) maps them into the shared ``409
scheduling_conflict`` body (ADR-0005) or a ``422``. None carries record data.
"""

from typing import Final, Literal

OverlapCode = Literal["tutor_overlap", "room_overlap"]

TIME_REASONS: Final = (
    "naive_datetime",
    "not_whole_minutes",
    "ends_before_start",
    "duration_out_of_range",
    "crosses_local_date",
)


class InvalidSessionTimesError(ValueError):
    """The session bounds break a time rule; ``reason`` is one of
    :data:`TIME_REASONS`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SessionOverlapError(Exception):
    """An exclusion constraint refused the write: the tutor or the room is
    already held at that time (``code`` ``tutor_overlap`` | ``room_overlap``,
    hard rules of ADR-0005 D1)."""

    def __init__(self, code: OverlapCode) -> None:
        super().__init__(code)
        self.code: OverlapCode = code


class InvalidSessionTransitionError(Exception):
    """The move is not in ``ALLOWED_TRANSITIONS`` (ADR-0005 D2, D6)."""

    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(f"{from_state} -> {to_state}")
        self.from_state = from_state
        self.to_state = to_state
