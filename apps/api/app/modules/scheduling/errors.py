"""Scheduling domain errors (#47 C2, B2; #48 B2, C2, C3).

The #47 errors are plain exceptions raised by the shared writes and rules;
the create route maps them (``scheduling/booking.py``) into the HTTP errors
below: :class:`SchedulingConflictError` (``409 scheduling_conflict`` with
``conflicts``, ADR-0005) and :class:`SessionValidationError` (``422
validation_error`` with field errors). None carries record data beyond ids
and the rule details.
"""

from collections.abc import Sequence
from typing import Final, Literal

from app.core.errors import AppError, FieldError, SchedulingConflict

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


class SchedulingConflictError(AppError):
    """``409 scheduling_conflict``: the session breaks hard or soft rules;
    ``conflicts`` lists every one (ADR-0005 wire format). Nothing is
    stored."""

    status = 409
    code = "scheduling_conflict"
    detail = "The session conflicts with existing rules."

    def __init__(self, conflicts: Sequence[SchedulingConflict]) -> None:
        super().__init__(conflicts=conflicts)


class SessionValidationError(AppError):
    """``422 validation_error`` with field errors: invalid bounds (#47
    reasons) or invalid references (``student_unknown`` …
    ``classroom_inactive``). Data validity is never a conflict (ADR-0005)."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class ClassSessionNotAssignableError(AppError):
    """``409 class_session_not_assignable`` (#51 E3): the session is
    ``completed``, ``cancelled`` or ``skipped_closure`` (final, or holding
    nothing), so no tutor can be suggested for it."""

    status = 409
    code = "class_session_not_assignable"
    detail = "This session can no longer take a tutor."


# --- Assignment offers (#52) -----------------------------------------------------


class ClassSessionNotOfferableError(AppError):
    """``409 class_session_not_offerable`` (#52 C2): the session can't be
    offered now. ``details`` is ``{"state": <state>}`` when it is not
    ``unassigned``, or ``{"reason": "started"}`` when it has already begun."""

    status = 409
    code = "class_session_not_offerable"
    detail = "This session can't be offered to a tutor now."

    def __init__(self, *, state: str | None = None, started: bool = False) -> None:
        details = {"reason": "started"} if started else {"state": state or ""}
        super().__init__(details=details)


class OfferNotPendingError(AppError):
    """``409 offer_not_pending`` (#52 D1): the offer was already answered,
    expired or withdrawn; ``details`` is ``{"status": <status>}``."""

    status = 409
    code = "offer_not_pending"
    detail = "This offer has already been answered."

    def __init__(self, offer_status: str) -> None:
        super().__init__(details={"status": offer_status})


class OfferExpiredError(AppError):
    """``409 offer_expired`` (#52 B3, D1): the deadline has passed (the
    expiry task closes the offer within five minutes)."""

    status = 409
    code = "offer_expired"
    detail = "The deadline to answer this offer has passed."


class ReassignmentStillNeededError(AppError):
    """``409 reassignment_still_needed`` (#52 E2): "Mark resolved" while a
    session of the declined or expired offer is still ``unassigned``."""

    status = 409
    code = "reassignment_still_needed"
    detail = "The session still has no tutor. Offer it to a tutor first."


class SeriesNotOfferableError(AppError):
    """``409 series_not_offerable`` (#233 C2 step 6): ``details.reason`` is
    ``assigned`` (the series already has a tutor, pending or accepted) or
    ``ended`` (no occurrence on or after the offer's ``from_date`` is still
    to come)."""

    status = 409
    code = "series_not_offerable"
    detail = "This series can't be offered to a tutor now."

    def __init__(self, reason: str) -> None:
        super().__init__(details={"reason": reason})
