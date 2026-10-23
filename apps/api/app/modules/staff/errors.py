"""Staff errors.

Tutor profiles (#43 D3) answer with the shared codes only: ``404
not_found`` (:class:`~app.core.authz.RecordNotFoundError`), ``403
permission_denied`` (:class:`~app.core.authz.PermissionDeniedError`) and
``409 version_conflict`` (:class:`~app.core.models.StaleVersionError`).

Tutor qualifications (#44) add the typed errors below. None takes record
data, so no submitted value reaches a response. Cancelling a qualification
that is no longer pending reuses the approval inbox's ``409
approval_transition_invalid`` (``administration.service``).

Tutor availability change requests (M4-18 #63) add ``409
availability_change_stale`` and ``409 availability_change_not_pending``
(#45's ``availability_change_requires_approval`` is retired: such a change
is now a ``202`` request); the teaching limits and preferred classrooms
(#197) add two ``422`` field errors with the generic ``validation_error``
code.

Revoking (#198) adds ``409 qualification_not_approved``; a stale ``version``
is the shared ``409 version_conflict``.
"""

from app.core.errors import AppError


class SubjectUnknownError(AppError):
    """The requested subject doesn't exist (#44 B2): ``422`` on
    ``subject_id``, with the generic ``validation_error`` code."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "subject_id"],
                    "code": "subject_unknown",
                    "message": "Choose an existing subject.",
                }
            ]
        )


class SubjectInactiveError(AppError):
    """The subject, or its education level, is inactive: no new
    qualification can be requested for it (#44 B2)."""

    status = 409
    code = "subject_inactive"
    detail = "The subject or its education level is inactive."

    def __init__(self) -> None:
        super().__init__()


class QualificationExistsError(AppError):
    """The tutor already has a pending or approved qualification for this
    subject (#44 B2; ``uq_tutor_qualifications_active``)."""

    status = 409
    code = "qualification_exists"
    detail = "There is already a pending or approved qualification for this subject."

    def __init__(self) -> None:
        super().__init__()


class QualificationNotApprovedError(AppError):
    """Only an approved qualification can be revoked (#198 B2): pending,
    rejected, cancelled and already revoked ones can't."""

    status = 409
    code = "qualification_not_approved"
    detail = "Only an approved qualification can be revoked."

    def __init__(self) -> None:
        super().__init__()


class AvailabilityChangeStaleError(AppError):
    """``409 availability_change_stale`` (M4-18 #63 D3): the availability
    changed since a weekly request was made (its ``base_version`` is not
    the current one), or the exception a delete request names no longer
    exists. The inbox item stays pending: reject it, or the requester
    withdraws it and asks again."""

    status = 409
    code = "availability_change_stale"
    detail = "The availability changed since this request was made."

    def __init__(self) -> None:
        super().__init__()


class AvailabilityChangeNotPendingError(AppError):
    """``409 availability_change_not_pending`` (M4-18 #63 D6): the change
    request was already approved, rejected or cancelled; ``details`` is
    ``{"status": <status>}``."""

    status = 409
    code = "availability_change_not_pending"
    detail = "This change request is no longer pending."

    def __init__(self, request_status: str) -> None:
        super().__init__(details={"status": request_status})


class WeeklyLimitBelowDailyError(AppError):
    """The weekly limit would be below the daily one once the sent limits
    are combined with the stored ones (#197 B2): ``422`` on
    ``weekly_limit_minutes``, as the body check answers when both are
    sent."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "weekly_limit_minutes"],
                    "code": "weekly_below_daily",
                    "message": "The weekly limit cannot be below the daily limit.",
                }
            ]
        )


class PreferredClassroomUnavailableError(AppError):
    """A newly chosen preferred classroom doesn't exist or is inactive
    (#197 B2): ``422`` on ``preferred_classroom_ids``."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "preferred_classroom_ids"],
                    "code": "classroom_unavailable",
                    "message": "Choose only existing, active classrooms.",
                }
            ]
        )
