"""Typed catalog errors with stable codes. None takes arguments, so no
submitted name or record data reaches a response."""

from app.core.errors import AppError


class NameTakenError(AppError):
    """Another record of the same catalog already has this name, ignoring case
    (inactive records count). One generic code that later catalogs reuse."""

    status = 409
    code = "name_taken"
    detail = "That name is already in use, possibly by an inactive record."

    def __init__(self) -> None:
        super().__init__()


class EducationLevelHasActiveSubjectsError(AppError):
    """A level with at least one active subject can't be deactivated."""

    status = 409
    code = "education_level_has_active_subjects"
    detail = "Deactivate this education level's active subjects first."

    def __init__(self) -> None:
        super().__init__()


class EducationLevelInactiveError(AppError):
    """A subject can't be created or reactivated under an inactive level."""

    status = 409
    code = "education_level_inactive"
    detail = "The education level is inactive. Reactivate it first."

    def __init__(self) -> None:
        super().__init__()
