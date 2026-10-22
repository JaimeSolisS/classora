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
    detail = (
        "There is still no tutor for this session or series. Offer it to a tutor first."
    )


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


# --- Editing sessions and series (#54) -------------------------------------------


class SessionNotEditableError(AppError):
    """``409 session_not_editable`` (#54 C1 step 6): the session can't be
    moved. ``details`` is ``{"reason": "started"}`` when it has begun
    (``starts_at <= now``), else ``{"state": <state>}`` for a
    ``completed``, ``cancelled`` or ``skipped_closure`` session."""

    status = 409
    code = "session_not_editable"
    detail = "This session can no longer be changed."

    def __init__(self, *, state: str | None = None, started: bool = False) -> None:
        details = {"reason": "started"} if started else {"state": state or ""}
        super().__init__(details=details)


class SeriesOfferPendingError(AppError):
    """``409 series_offer_pending`` (#54 C3 step 6): the series' tutor offer
    is waiting for an answer; answer or withdraw it first."""

    status = 409
    code = "series_offer_pending"
    detail = (
        "This series has a tutor offer waiting for an answer. It must be"
        " answered or withdrawn first."
    )


class SeriesEndedError(AppError):
    """``409 series_ended`` (#54 C3 step 8, C5): the date is after the
    series' last occurrence (``last_occurrence_on``)."""

    status = 409
    code = "series_ended"
    detail = "This series has no occurrence on or after that date."


class OccurrenceStartedError(AppError):
    """``409 occurrence_started`` (#54 C3 step 9): an occurrence of the series
    on the date has already begun, so the change can't start there."""

    status = 409
    code = "occurrence_started"
    detail = "An occurrence on that date has already begun."


# --- Series overrides (#236) ------------------------------------------------------


class SessionNotInConflictError(AppError):
    """``409 session_not_in_conflict`` (#236 G3 step 7): only a ``conflict``
    occurrence can have its conflicts overridden this way; ``details`` is
    ``{"state": <state>}``."""

    status = 409
    code = "session_not_in_conflict"
    detail = "This session is not waiting on a scheduling conflict."

    def __init__(self, state: str) -> None:
        super().__init__(details={"state": state})


# --- Substitutions and cancellations (#55) -----------------------------------------


class ClassSessionNotSubstitutableError(AppError):
    """``409 class_session_not_substitutable`` (#55 B2 step 5):
    ``details.reason`` is ``state`` (with ``details.state``; only a
    ``confirmed`` session can get a substitute), ``started``, or
    ``substitution_pending`` (with ``details.substitution_id``)."""

    status = 409
    code = "class_session_not_substitutable"
    detail = "This session can't be given to a substitute tutor now."

    def __init__(
        self,
        reason: str,
        *,
        state: str | None = None,
        substitution_id: str | None = None,
    ) -> None:
        details = {"reason": reason}
        if state is not None:
            details["state"] = state
        if substitution_id is not None:
            details["substitution_id"] = substitution_id
        super().__init__(details=details)


class SubstitutionNoLongerValidError(AppError):
    """``409 substitution_no_longer_valid`` (#55 D1): the session is no longer
    ``confirmed`` with the original tutor at the offered times, so the
    substitute can't take it. Nothing is written."""

    status = 409
    code = "substitution_no_longer_valid"
    detail = "This session changed since the substitution was offered."


class ClassSessionNotCancellableError(AppError):
    """``409 class_session_not_cancellable`` (#55 C1 step 6):
    ``details.reason`` is ``state`` (with ``details.state``: ``completed``,
    ``cancelled`` or ``skipped_closure``) or ``started``."""

    status = 409
    code = "class_session_not_cancellable"
    detail = "This session can't be cancelled."

    def __init__(self, reason: str, *, state: str | None = None) -> None:
        details = {"reason": reason}
        if state is not None:
            details["state"] = state
        super().__init__(details=details)


# --- Ongoing tutoring groups (#56) --------------------------------------------------


class GroupNotFoundError(AppError):
    """``404 group_not_found``: no such group, or one the caller can't see
    (#56 H2: never ``403``)."""

    status = 404
    code = "group_not_found"
    detail = "No such group."

    def __init__(self) -> None:
        super().__init__()


class EnrollmentNotFoundError(AppError):
    """``404 enrollment_not_found`` (#56 E2): no such enrollment in this
    group."""

    status = 404
    code = "enrollment_not_found"
    detail = "No such enrollment in this group."

    def __init__(self) -> None:
        super().__init__()


class GroupNameTakenError(AppError):
    """``409 group_name_taken`` (#56 D2 step 6, E4): another group has this
    name (compared case-insensitively)."""

    status = 409
    code = "group_name_taken"
    detail = "Another group already has this name."

    def __init__(self) -> None:
        super().__init__()


class GroupEndedError(AppError):
    """``409 group_ended`` (#56 E1 step 6): no series of the group is still
    producing occurrences."""

    status = 409
    code = "group_ended"
    detail = "This group has ended."

    def __init__(self) -> None:
        super().__init__()


class AlreadyEnrolledError(AppError):
    """``409 already_enrolled`` (#56 E1 step 8): the student has an active
    enrollment in the group (``details.enrollment_id``)."""

    status = 409
    code = "already_enrolled"
    detail = "The student is already enrolled in this group."

    def __init__(self, enrollment_id: str | None) -> None:
        super().__init__(
            details=None if enrollment_id is None else {"enrollment_id": enrollment_id}
        )


class EnrollmentNotActiveError(AppError):
    """``409 enrollment_not_active`` (#56 E2): the enrollment was already
    withdrawn."""

    status = 409
    code = "enrollment_not_active"
    detail = "This enrollment was already withdrawn."

    def __init__(self) -> None:
        super().__init__()
