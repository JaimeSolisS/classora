"""Staff errors.

Tutor profiles (#43 D3) answer with the shared codes only: ``404
not_found`` (:class:`~app.core.authz.RecordNotFoundError`), ``403
permission_denied`` (:class:`~app.core.authz.PermissionDeniedError`) and
``409 version_conflict`` (:class:`~app.core.models.StaleVersionError`).

Tutor qualifications (#44) add the typed errors below. None takes record
data, so no submitted value reaches a response. Cancelling a qualification
that is no longer pending reuses the approval inbox's ``409
approval_transition_invalid`` (``administration.service``).

Tutor availability (#45) adds :class:`AvailabilityChangeRequiresApprovalError`;
the teaching limits and preferred classrooms (#197) add two ``422`` field
errors with the generic ``validation_error`` code.
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


class AvailabilityChangeRequiresApprovalError(AppError):
    """The availability change affects scheduled sessions (#45 D2; the
    ``assignment_impact`` seam): nothing changes. M4-18 (#63) turns this
    into an approval request; clients treat it as "needs approval"."""

    status = 409
    code = "availability_change_requires_approval"
    detail = "This change affects scheduled classes and needs approval."

    def __init__(self) -> None:
        super().__init__()


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
