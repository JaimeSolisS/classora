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


class PeriodOverlapError(AppError):
    """Another active academic period of the same type shares at least one
    day with these dates (#38 B5)."""

    status = 409
    code = "period_overlap"
    detail = "Another active period of this type overlaps these dates."

    def __init__(self) -> None:
        super().__init__()


class DateRangeInvalidError(AppError):
    """A ``PATCH`` that sends one date makes the stored range invalid (the
    end before the start, or too long). The generic ``validation_error``
    code with a field error on ``["body", "ends_on"]``, as the body check
    gives when both dates are sent; it needs the stored record, so the
    service checks it after ``404`` and the version."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, message: str) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "ends_on"],
                    "code": "date_range_invalid",
                    "message": message,
                }
            ]
        )


class WindowOverlapError(AppError):
    """Two windows of one room on one weekday overlap: the backstop when the
    exclusion constraint ``ex_classroom_windows_no_overlap`` rejects a flush
    the body check let through (#39 B5). The generic ``validation_error``
    code with a field error on ``["body", "availability"]``."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "availability"],
                    "code": "window_overlap",
                    "message": "Two windows of the same day overlap.",
                }
            ]
        )
