"""Typed errors of student profiles with stable codes (#33 H2). None takes
an argument other than its field ``errors`` (fixed paths and messages), so no
submitted value or record data reaches a response."""

from collections.abc import Sequence

from app.core.errors import AppError, FieldError


class EducationLevelUnavailableError(AppError):
    """The chosen education level is unknown or inactive."""

    status = 422
    code = "education_level_unavailable"
    detail = "Choose an active education level."

    def __init__(self, *, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class EmergencyContactUnknownError(AppError):
    """A contact ``id`` is not one of this student's contacts, or is sent
    twice."""

    status = 422
    code = "emergency_contact_unknown"
    detail = "One of the emergency contacts no longer exists. Reload and try again."

    def __init__(self, *, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class BirthdateInFutureError(AppError):
    """A birthdate after today's date in the center's time zone (C3). Uses the
    generic ``validation_error`` code: the rule needs the clock, so it is
    checked by the service, after the version check (D2)."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "birthdate"],
                    "code": "birthdate_in_future",
                    "message": "The birthdate can't be in the future.",
                }
            ]
        )
