"""Course errors (#64).

The routes answer with the shared codes ``404 not_found``
(:class:`~app.core.authz.RecordNotFoundError`), ``409 version_conflict``
(:class:`~app.core.models.StaleVersionError`) and the two below. None takes
record data beyond field errors, so no submitted value reaches a response.

:class:`CourseTemplateNotFound` and :class:`CourseTemplateInactive` are plain
exceptions of :func:`app.modules.courses.service.snapshot_course_template`:
the cohort module (#65) maps them to its own responses.

The cohorts (#65) answer ``404 cohort_not_found``, ``409
cohort_name_taken`` and ``422 validation_error`` with field errors
(:class:`CohortValidationError`); ``409 version_conflict`` is shared.
"""

from collections.abc import Sequence

from app.core.errors import AppError, FieldError


class CourseTemplateNameTakenError(AppError):
    """Another template already has this name, ignoring case (inactive
    templates count)."""

    status = 409
    code = "name_taken"
    detail = "That name is already in use, possibly by an inactive record."

    def __init__(self) -> None:
        super().__init__()


class CourseTemplateInvalidError(AppError):
    """The document breaks a template rule (``courses.templates``), names an
    unknown or inactive subject, or an item id that is not this template's:
    the generic ``validation_error`` code with every field error."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class CourseTemplateNotFound(Exception):  # noqa: N818 (a plain lookup outcome)
    """No template with this id (for the cohort module)."""


class CourseTemplateInactive(Exception):  # noqa: N818 (a plain lookup outcome)
    """The template is inactive: no cohort can be created from it."""


# --- Cohorts (#65) -------------------------------------------------------------------


class CohortNotFoundError(AppError):
    """No cohort with this id (#65 E1, F3)."""

    status = 404
    code = "cohort_not_found"
    detail = "The cohort doesn't exist."

    def __init__(self) -> None:
        super().__init__()


class CohortNameTakenError(AppError):
    """Another cohort already has this name, ignoring case (#65 D2 step 6,
    E1)."""

    status = 409
    code = "cohort_name_taken"
    detail = "Another cohort already has this name."

    def __init__(self) -> None:
        super().__init__()


class CohortValidationError(AppError):
    """``422 validation_error`` with the field errors of a cohort write
    (#65 D2 step 5): the template, the subject and every scheduling field,
    all in one response."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)
