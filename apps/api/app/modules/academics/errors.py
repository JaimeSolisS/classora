"""Session closeout errors (#77 D2, E1; #78 C, D).

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
    missing (``summary_required`` at ``body.summary.<field>``); #78 also
    raises it for a correction request without a reason
    (``reason_required`` at ``body.reason``)."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class CloseoutReviewRouteRequiredError(AppError):
    """``409 closeout_review_route_required`` (#78 C4, E2, E3): the inbox's
    generic approve, reject or request changes can't decide a closeout;
    ``details.review_path`` is the staff session page, where the reviewer
    decides with the closeout's version. The item stays as it was."""

    status = 409
    code = "closeout_review_route_required"
    detail = "Review this closeout on its session page."

    def __init__(self, review_path: str) -> None:
        super().__init__(details={"review_path": review_path})


class CloseoutNotSubmittedError(AppError):
    """``409 closeout_not_submitted`` (#78 C1 step 8): only a ``submitted``
    closeout is approved or sent back."""

    status = 409
    code = "closeout_not_submitted"
    detail = "This closeout isn't waiting for review."

    def __init__(self) -> None:
        super().__init__()


class CloseoutWithTutorError(AppError):
    """``409 closeout_with_tutor`` (#78 C3, E5): changes were requested, so
    the tutor owns the record until they resubmit."""

    status = 409
    code = "closeout_with_tutor"
    detail = "Changes were requested: the tutor is editing this closeout."

    def __init__(self) -> None:
        super().__init__()


class CloseoutUnderReviewError(AppError):
    """``409 closeout_under_review`` (#78 D2 step 7): the closeout is
    ``submitted``; the tutor edits it only after changes are requested or
    asks for a correction after approval."""

    status = 409
    code = "closeout_under_review"
    detail = "This closeout is already under review."

    def __init__(self) -> None:
        super().__init__()


class CloseoutSelfReviewError(AppError):
    """``403 closeout_self_review`` (#78 E7): a reviewer can't approve,
    send back or correct a closeout they submitted (the Owner can)."""

    status = 403
    code = "closeout_self_review"
    detail = "You can't review your own closeout."

    def __init__(self) -> None:
        super().__init__()


class CloseoutUnchangedError(AppError):
    """``422 closeout_unchanged`` (#78 C3 step 4, D2 step 11): the
    correction changes nothing."""

    status = 422
    code = "closeout_unchanged"
    detail = "Nothing changed."

    def __init__(self) -> None:
        super().__init__()
