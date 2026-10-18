"""Typed identity errors with stable codes (re-exported by ``service``).

Messages never contain a submitted password or username.
"""

from app.core.errors import AppError


class UsernameInvalidError(AppError):
    status = 422
    code = "username_invalid"
    detail = (
        "A username has 3 to 64 characters from a-z, 0-9, '.', '_' and '-'"
        " (letters are not case-sensitive)."
    )


class PasswordPolicyError(AppError):
    """Base of the password-policy violations (ADR-0007, D6)."""

    status = 422
    code = "password_invalid"


class PasswordTooShortError(PasswordPolicyError):
    code = "password_too_short"
    detail = "The password must have at least 12 characters."


class PasswordTooLongError(PasswordPolicyError):
    code = "password_too_long"
    detail = "The password must have at most 128 characters."


class PasswordEqualsUsernameError(PasswordPolicyError):
    code = "password_equals_username"
    detail = "The password must not be the same as the username."


class DelegationForbiddenError(AppError):
    """The actor may not manage delegations (only an active Owner may)."""

    status = 403
    code = "delegation_forbidden"
    detail = "Only an Owner can grant or revoke delegated permissions."


class PermissionNotDelegableError(AppError):
    status = 422
    code = "permission_not_delegable"
    detail = "This permission cannot be delegated."


class DelegationTargetInvalidError(AppError):
    status = 422
    code = "delegation_target_invalid"
    detail = (
        "Delegated permissions are granted only to Administrators who are not archived."
    )
