"""Typed identity errors with stable codes (re-exported by ``service``).

Messages never contain a submitted password or username.
"""

from app.core.errors import AppError

STUDENT_LOGIN_GATE_DOC = (
    "The account holds the student role and isn't linked to a student profile"
    " (student_profile_required), or its student is a minor without a valid"
    " consent from a linked, active guardian (guardian_consent_required);"
    " nothing changes."
)
"""The two refusals of the student-login gate (#35 B; raised by the families
module through the registered gate), for the OpenAPI docs of the routes that
activate an account or add the student role."""


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


class InvalidCredentialsError(AppError):
    """A login that did not succeed, for any reason (ADR-0007 D7).

    One fixed body for an unknown username, a wrong password, a user without a
    password and a user who is not active, so a response never reveals whether
    an account exists. Takes no arguments, so nothing else can leak.
    """

    status = 401
    code = "invalid_credentials"
    detail = "The username or password is incorrect."

    def __init__(self) -> None:
        super().__init__()


class InvalidUserStateTransitionError(AppError):
    """A user-state change the transition table forbids (#25 F2).

    Nothing goes back to ``pending``; ``pending`` and ``archived`` users cannot
    be suspended.
    """

    status = 409
    code = "user_state_transition_invalid"
    detail = "The user cannot be moved to this state."

    def __init__(self) -> None:
        super().__init__()


class UnknownRoleError(AppError):
    """A role code that is not in the role catalog (#25 F3)."""

    status = 422
    code = "role_unknown"
    detail = "This role does not exist."

    def __init__(self) -> None:
        super().__init__()


class ReauthFailedError(AppError):
    """A reauthentication that did not succeed (#26; ADR-0007 D10).

    ``403``, not ``401``: the session is still valid (the web app sends the user
    to the login page only on ``401``). One body for a wrong password and a
    user without a password. Takes no arguments.
    """

    status = 403
    code = "reauth_failed"
    detail = "The password is incorrect."

    def __init__(self) -> None:
        super().__init__()


class TokenInvalidError(AppError):
    """An account token that cannot be used (#29; ADR-0007 D11).

    One code and one fixed body for every case: unknown, malformed, used,
    replaced or expired, a token of another kind, or a user who can no longer
    redeem it. The response never reveals which case applies. Takes no
    arguments, so the token can never reach the body.
    """

    status = 410
    code = "token_invalid"
    detail = "This link is no longer valid. Ask the center for a new one."

    def __init__(self) -> None:
        super().__init__()


class UsernameTakenError(AppError):
    """The normalized username belongs to another user, in any state (#29).

    Never echoes the username. Takes no arguments.
    """

    status = 409
    code = "username_taken"
    detail = "That username is already taken."

    def __init__(self) -> None:
        super().__init__()


class CurrentPasswordInvalidError(AppError):
    """The current password of a self-service password change is wrong (#30).

    ``403``, not ``401``: the session is still valid (the web app sends the user
    to the login page only on ``401``). A code of its own, so the form shows it
    on the current-password field. One body for a wrong password and a user
    without a password. Takes no arguments.
    """

    status = 403
    code = "current_password_invalid"
    detail = "The current password is incorrect."

    def __init__(self) -> None:
        super().__init__()


class UserNotActiveError(AppError):
    """A password reset for a user who is not ``active`` (#30).

    A pending invited user gets a new invitation link (#29); a suspended or
    archived user must be reactivated first (#31). Takes no arguments.
    """

    status = 409
    code = "user_not_active"
    detail = "This account is not active."

    def __init__(self) -> None:
        super().__init__()


class RegistrationAlreadyDecidedError(AppError):
    """Approving or rejecting a registration request that is no longer
    pending, or whose user is no longer ``pending`` (#169). Takes no
    arguments.

    Since #40 only the ``identity.registration`` approval handler's defensive
    check: the inbox refuses a decided approval request first (``409
    approval_transition_invalid``), so this answers only when the account
    changed outside the inbox (for example it was archived meanwhile).
    """

    status = 409
    code = "registration_already_decided"
    detail = "This request has already been decided."

    def __init__(self) -> None:
        super().__init__()


class LastActiveOwnerError(AppError):
    """Suspending or archiving the last active Owner, or taking the ``owner``
    role from them (#31; ADR-0007 D4). Raised only inside
    ``service.change_user_state`` and ``service.remove_user_role``. Takes no
    arguments.
    """

    status = 409
    code = "last_active_owner"
    detail = (
        "This is the only active Owner account. Activate or assign another Owner first."
    )

    def __init__(self) -> None:
        super().__init__()


class PrivacyNoticeOutdatedError(AppError):
    """The accepted version is not the current privacy-notice version: older,
    unknown or not yet published (#174). Nothing is written. Takes no
    arguments.
    """

    status = 409
    code = "privacy_notice_outdated"
    detail = "The privacy notice changed. Read the current version first."

    def __init__(self) -> None:
        super().__init__()


class PrivacyNoticeUnavailableError(RuntimeError):
    """No privacy-notice version is published (#174 A5): a deployment error,
    because migration 0017 publishes version 1. Not an :class:`AppError`, so
    the request fails with ``500`` (fail closed); the gate refuses every user
    meanwhile, because nobody can have accepted a current version."""

    def __init__(self) -> None:
        super().__init__("no privacy-notice version is published")
