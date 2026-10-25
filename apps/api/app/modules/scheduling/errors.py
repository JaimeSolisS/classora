"""Scheduling domain errors (#47 C2, B2; #48 B2, C2, C3).

The #47 errors are plain exceptions raised by the shared writes and rules;
the create route maps them (``scheduling/booking.py``) into the HTTP errors
below: :class:`SchedulingConflictError` (``409 scheduling_conflict`` with
``conflicts``, ADR-0005) and :class:`SessionValidationError` (``422
validation_error`` with field errors). None carries record data beyond ids
and the rule details.
"""

import uuid
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
    detail = "No such enrollment."

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
    detail = "The student is already enrolled."

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


# --- Session requests by families (#59 G2) -----------------------------------------
#
# The family-facing bodies are closed (#59 S3): no ``conflicts``, ``errors``
# or ``details`` member, no id, never a tutor or another student.


class StudentConsentRequiredError(AppError):
    """``403 student_consent_required`` (#59 S2, E3): a linked guardian of an
    **adult** student without that student's ``guardian_academic_access``
    consent naming their profile (ADR-0007 D17)."""

    status = 403
    code = "student_consent_required"
    detail = "The student must give you consent before you can do this."

    def __init__(self) -> None:
        super().__init__()


class SlotUnavailableError(AppError):
    """``409 slot_unavailable`` (#59 C2 step 10): the time no longer passes
    the slot checks. Opaque on purpose: the reason could reveal a tutor's
    or another family's schedule."""

    status = 409
    code = "slot_unavailable"
    detail = "That time is no longer available."

    def __init__(self) -> None:
        super().__init__()


class SessionRequestOverlapError(AppError):
    """``409 session_request_overlap`` (#59 C2 step 9): a ``pending`` request
    of this student overlaps the time."""

    status = 409
    code = "session_request_overlap"
    detail = "This student already has a pending request at that time."

    def __init__(self) -> None:
        super().__init__()


class SessionRequestLimitReachedError(AppError):
    """``409 session_request_limit_reached`` (#59 S5): the student already
    has the maximum number of ``pending`` requests."""

    status = 409
    code = "session_request_limit_reached"
    detail = "This student already has the maximum number of pending requests."

    def __init__(self) -> None:
        super().__init__()


class SessionRequestNotPendingError(AppError):
    """``409 session_request_not_pending`` (#59 D3, #60 C2/D2): the request
    was already confirmed, rejected or withdrawn. The family's withdraw
    keeps the closed body (#59 S3); the staff routes add ``details.status``
    (#60)."""

    status = 409
    code = "session_request_not_pending"
    detail = "This request is no longer pending."

    def __init__(self, request_status: str | None = None) -> None:
        super().__init__(
            details=None if request_status is None else {"status": request_status}
        )


class SessionRequestStartedError(AppError):
    """``409 session_request_started`` (#60 C2 step 7): the requested time
    has already begun, so nobody could accept the offer; reject it
    instead."""

    status = 409
    code = "session_request_started"
    detail = "The requested time has already started; reject the request instead."

    def __init__(self) -> None:
        super().__init__()


class SessionRequestReviewRouteRequiredError(AppError):
    """``409 session_request_review_route_required`` (#60 E1, E2): the inbox's
    generic approve or reject can't decide a session request (it can't carry
    the tutor); ``details.review_path`` is the staff review page. The item
    stays ``pending``."""

    status = 409
    code = "session_request_review_route_required"
    detail = "Review this session request on its own page."

    def __init__(self, review_path: str) -> None:
        super().__init__(details={"review_path": review_path})


# --- Change requests by families and tutors (#62) -----------------------------------
#
# The family- and tutor-facing bodies are closed (#62 S3): no ``conflicts``
# or ``errors`` member with scheduling details, never a tutor or a room.


class ChangeRequestKindUnsupportedError(AppError):
    """``409 change_request_kind_unsupported`` (#62 B2 step 6, E2): only
    individual sessions can be cancelled or moved by request; ``details.kind``
    is the session's kind (``group`` or ``course``)."""

    status = 409
    code = "change_request_kind_unsupported"
    detail = "Only individual sessions can be changed by request."

    def __init__(self, kind: str) -> None:
        super().__init__(details={"kind": kind})


class ClassSessionNotChangeableError(AppError):
    """``409 class_session_not_changeable`` (#62 B2 step 7): the session isn't
    ``confirmed`` (a tutor's ``offered`` session, a ``completed`` one, ...);
    ``details.state`` is its state."""

    status = 409
    code = "class_session_not_changeable"
    detail = "Only a confirmed session can be changed by request."

    def __init__(self, state: str) -> None:
        super().__init__(details={"state": state})


class ChangeRequestSessionStartedError(AppError):
    """``409 change_request_session_started`` (#62 B2 step 7, C2 step 7, E6):
    the session has already started, so it can't be changed any more."""

    status = 409
    code = "change_request_session_started"
    detail = "The session has already started."

    def __init__(self) -> None:
        super().__init__()


class ChangeRequestPendingError(AppError):
    """``409 change_request_pending`` (#62 A3, B2 step 8): the session already
    has a pending request. ``details.change_request_id`` names it only when
    the caller may read it (S2)."""

    status = 409
    code = "change_request_pending"
    detail = "This session already has a pending change request."

    def __init__(self, change_request_id: str | None = None) -> None:
        super().__init__(
            details=None
            if change_request_id is None
            else {"change_request_id": change_request_id}
        )


class ChangeRequestNotPendingError(AppError):
    """``409 change_request_not_pending`` (#62 C2 step 6, C4, D3): the request
    was already approved, rejected or withdrawn. The staff routes add
    ``details.status``; the requester's withdraw keeps the body closed."""

    status = 409
    code = "change_request_not_pending"
    detail = "This request is no longer pending."

    def __init__(self, request_status: str | None = None) -> None:
        super().__init__(
            details=None if request_status is None else {"status": request_status}
        )


class ChangeRequestSessionChangedError(AppError):
    """``409 change_request_session_changed`` (#62 C2 step 7): the session is
    no longer ``confirmed`` (staff cancelled it, a closure skipped it, ...);
    ``details.state`` is its state. Reject the request instead."""

    status = 409
    code = "change_request_session_changed"
    detail = "The session changed since the request was made; reject it instead."

    def __init__(self, state: str) -> None:
        super().__init__(details={"state": state})


class ChangeRequestReviewRouteRequiredError(AppError):
    """``409 change_request_review_route_required`` (#62 C6, E3): the inbox's
    generic approve or reject can't decide a change request;
    ``details.review_path`` is the staff review page. The item stays
    ``pending``."""

    status = 409
    code = "change_request_review_route_required"
    detail = "Review this change request on its own page."

    def __init__(self, review_path: str) -> None:
        super().__init__(details={"review_path": review_path})


# --- Attendance (#76) ---------------------------------------------------------------


class AttendanceTutorOnlyError(AppError):
    """``403 attendance_tutor_only`` (#76 C2 step 4): only the session's
    current tutor marks attendance; staff correct it with a reason."""

    status = 403
    code = "attendance_tutor_only"
    detail = (
        "Only the session's tutor marks attendance. Staff correct it with a reason."
    )


class AttendanceNotRecordableError(AppError):
    """``409 attendance_not_recordable`` (#76 A3): the session is neither
    ``confirmed`` nor ``completed``; ``details`` is ``{"state": <state>}``."""

    status = 409
    code = "attendance_not_recordable"
    detail = "Attendance can't be recorded for this session."

    def __init__(self, state: str) -> None:
        super().__init__(details={"state": state})


class AttendanceNotStartedError(AppError):
    """``409 attendance_not_started`` (#76 A3): the session hasn't started."""

    status = 409
    code = "attendance_not_started"
    detail = "Attendance opens when the class starts."


class AttendanceLockedError(AppError):
    """``409 attendance_locked`` (#76 A3, E2): the session is ``completed``;
    the tutor can no longer mark, staff correct it."""

    status = 409
    code = "attendance_locked"
    detail = "Attendance is closed. Ask the center to correct it."


class AttendanceCorrectedError(AppError):
    """``409 attendance_corrected`` (#76 C2 step 9): staff corrected this
    student's mark, so the tutor can't change it; ``details`` is
    ``{"student_id": <id>}`` (the first one in the body's order)."""

    status = 409
    code = "attendance_corrected"
    detail = "The center corrected this attendance. Ask the center to change it."

    def __init__(self, student_id: str) -> None:
        super().__init__(details={"student_id": student_id})


# --- Session completion (#77 B1) ------------------------------------------------------


class AttendanceIncompleteError(AppError):
    """``409 closeout_attendance_incomplete`` (#77 B1, E4): a participant has
    no attendance mark; ``details`` is ``{"missing_student_ids": [...]}``
    (sorted). Raised by :func:`~app.modules.scheduling.closeout.
    complete_class_session`."""

    status = 409
    code = "closeout_attendance_incomplete"
    detail = "Mark every participant's attendance first."

    def __init__(self, missing_student_ids: Sequence[uuid.UUID]) -> None:
        super().__init__(
            details={"missing_student_ids": sorted(str(i) for i in missing_student_ids)}
        )
        self.missing_student_ids = sorted(missing_student_ids, key=str)


class SessionNotCompletableError(Exception):  # noqa: N818 (a plain domain outcome)
    """The session isn't ``confirmed`` (#77 B1); ``state`` is its state. The
    closeout workflow checks the state first and maps it to its own
    ``409``."""

    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state
