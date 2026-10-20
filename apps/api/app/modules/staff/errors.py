"""Staff errors.

Tutor profiles (#43 D3) answer with the shared codes only: ``404
not_found`` (:class:`~app.core.authz.RecordNotFoundError`), ``403
permission_denied`` (:class:`~app.core.authz.PermissionDeniedError`) and
``409 version_conflict`` (:class:`~app.core.models.StaleVersionError`).

Tutor qualifications (#44) add the typed errors below. None takes record
data, so no submitted value reaches a response. Cancelling a qualification
that is no longer pending reuses the approval inbox's ``409
approval_transition_invalid`` (``administration.service``).
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
