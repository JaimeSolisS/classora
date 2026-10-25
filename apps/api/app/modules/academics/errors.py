"""Session closeout errors (#77 D2, E1).

The routes also answer the shared ``404 not_found``
(:class:`~app.core.authz.RecordNotFoundError`, the same body for an unknown
and a hidden session) and ``409 version_conflict``; ``409
closeout_attendance_incomplete`` is raised by
``scheduling.service.complete_class_session``. ``details`` carry codes only,
never record data.
"""

from collections.abc import Sequence

from app.core.errors import AppError, FieldError


class CloseoutTutorOnlyError(AppError):
    """``403 closeout_tutor_only`` (D2 step 4): only the session's current
    tutor submits its closeout."""

    status = 403
    code = "closeout_tutor_only"
    detail = "Only the session's tutor submits its closeout."

    def __init__(self) -> None:
        super().__init__()


class CloseoutAlreadySubmittedError(AppError):
    """``409 closeout_already_submitted`` (A5): the session is ``completed``
    or already has a closeout."""

    status = 409
    code = "closeout_already_submitted"
    detail = "This session's closeout was already submitted."

    def __init__(self) -> None:
        super().__init__()


class SessionNotClosableError(AppError):
    """``409 session_not_closable`` (A5): the session isn't ``confirmed``;
    ``details`` is ``{"state": <state>}``."""

    status = 409
    code = "session_not_closable"
    detail = "This session can't be closed out."

    def __init__(self, state: str) -> None:
        super().__init__(details={"state": state})


class SessionNotStartedError(AppError):
    """``409 session_not_started`` (A5): the class hasn't started yet."""

    status = 409
    code = "session_not_started"
    detail = "The closeout opens when the class starts."

    def __init__(self) -> None:
        super().__init__()


class CloseoutSummaryInvalidError(AppError):
    """``422 validation_error`` (D2 step 9): a required summary field is
    missing (``summary_required`` at ``body.summary.<field>``)."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class CloseoutReviewUnavailableError(AppError):
    """``409 closeout_review_unavailable`` (E3): closeouts can't be approved,
    rejected or sent back yet (#78 adds the review); the inbox item stays
    ``pending``."""

    status = 409
    code = "closeout_review_unavailable"
    detail = "Closeout review isn't available yet."

    def __init__(self) -> None:
        super().__init__()
