"""Course errors (#64).

The routes answer with the shared codes ``404 not_found``
(:class:`~app.core.authz.RecordNotFoundError`), ``409 version_conflict``
(:class:`~app.core.models.StaleVersionError`) and the two below. None takes
record data beyond field errors, so no submitted value reaches a response.

:class:`CourseTemplateNotFound` and :class:`CourseTemplateInactive` are plain
exceptions of :func:`app.modules.courses.service.snapshot_course_template`:
the cohort module (#65) maps them to its own responses.
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
