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


# --- Guardian profiles and links (#34 H2) ----------------------------------------


class GuardianAccountUnavailableError(AppError):
    """The account to link is unknown, archived or doesn't hold the
    ``guardian`` role (#34 B2). Field error on ``["body", "user_id"]``."""

    status = 422
    code = "guardian_account_unavailable"
    detail = "Choose an active or pending account with the guardian role."

    def __init__(self, *, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class GuardianAccountInUseError(AppError):
    """The account is already linked to another guardian profile."""

    status = 409
    code = "guardian_account_in_use"
    detail = "That account is already linked to another guardian."


class GuardianUnavailableError(AppError):
    """The guardian to link is unknown or inactive. Field error on
    ``["body", "guardian_id"]``."""

    status = 422
    code = "guardian_unavailable"
    detail = "Choose an active guardian."

    def __init__(self, *, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class GuardianAlreadyLinkedError(AppError):
    """The guardian is already linked to this student."""

    status = 409
    code = "guardian_already_linked"
    detail = "That guardian is already linked to this student."


# --- Family groupings (#172 G2) ----------------------------------------------------


class FamilyMemberConflictError(AppError):
    """The student or guardian already belongs to **another** family (owner
    decision #172 E2 (a): one family each). Remove them from it first."""

    status = 409
    code = "family_member_conflict"
    detail = "Already belongs to another family. Remove them from it first."


class FamilyInactiveError(AppError):
    """An inactive family can't receive new members."""

    status = 409
    code = "family_inactive"
    detail = "Reactivate the family to add members."
