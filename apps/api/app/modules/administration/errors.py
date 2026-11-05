"""Typed administration errors with stable codes. None takes a submitted value
or record data, so neither can reach a response."""

from collections.abc import Sequence
from typing import ClassVar

from app.core.errors import AppError


class TzChangeRequiresMigrationError(AppError):
    """The center time zone can't change: scheduled data exists whose stored
    instants were computed from the current zone (#37 D; ADR-0004). The body
    never names the data."""

    status = 409
    code = "tz_change_requires_migration"
    detail = "The time zone can't be changed because classes are already scheduled."

    def __init__(self) -> None:
        super().__init__()


# --- Approval requests (#40) -------------------------------------------------------


class ApprovalTransitionInvalidError(AppError):
    """The request's current status doesn't allow the action (#40 B2): it was
    already decided, cancelled, or is waiting for the requester's changes.
    Names neither the request nor its status."""

    status = 409
    code = "approval_transition_invalid"
    detail = "This request can't take that action in its current status."

    def __init__(self) -> None:
        super().__init__()


class ApprovalActionUnsupportedError(AppError):
    """The request's type doesn't support the action (#40 B2; today only
    ``request-changes`` can be unsupported)."""

    status = 409
    code = "approval_action_unsupported"
    detail = "This type of request doesn't support that action."

    def __init__(self) -> None:
        super().__init__()


class ApprovalReasonInvalidError(AppError):
    """The decision's reason is missing where the action needs one, or is
    longer than 1000 characters (#40 B3). The generic ``validation_error``
    code with a field error on ``["body", "reason"]``; checked by the service
    after ``404`` and the transition check."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(
            errors=[
                {"path": ["body", "reason"], "code": error_code, "message": message}
            ]
        )


class ApprovalRequestClosedError(AppError):
    """The request is ``approved``, ``rejected`` or ``cancelled``: only an open
    request can be assigned (#118 E3). Names neither the request nor its
    status."""

    status = 409
    code = "approval_request_closed"
    detail = "This request is already closed, so it can't be assigned."

    def __init__(self) -> None:
        super().__init__()


class ApprovalAssigneeIneligibleError(AppError):
    """The chosen assignee is unknown, not active, or doesn't hold the
    request type's review permission now (#118 E2). Says nothing about which
    of these, nor about the user."""

    status = 422
    code = "approval_assignee_ineligible"
    detail = "This person can't be assigned to the request."

    def __init__(self) -> None:
        super().__init__()


# --- Live notifications (#42) ------------------------------------------------------

STREAM_RETRY_AFTER_SECONDS = 30


class TooManyStreamsError(AppError):
    """The caller already holds the most live notification streams one user
    may open in this API process (#42 C5). ``Retry-After`` says when to try
    again; the web app polls meanwhile."""

    status = 429
    code = "too_many_streams"
    detail = "Too many open notification streams. Close a tab or try again later."

    def __init__(self) -> None:
        super().__init__()
        self.headers = {"Retry-After": str(STREAM_RETRY_AFTER_SECONDS)}


# --- Notification preferences (#119 C2) --------------------------------------------


class _NotificationPreferenceError(AppError):
    """A ``PATCH /api/v1/notifications/preferences`` item the service refuses:
    one field error per offending item, on ``["body", "items", i,
    "notification_type"]`` (never the submitted value)."""

    status = 422
    message: ClassVar[str] = ""

    def __init__(self, indexes: Sequence[int]) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "items", index, "notification_type"],
                    "code": self.code,
                    "message": self.message,
                }
                for index in indexes
            ]
        )


class NotificationTypeDuplicateError(_NotificationPreferenceError):
    """The same type appears more than once in the body (each repeat after
    the first is named)."""

    code = "notification_type_duplicate"
    detail = "A notification type appears more than once."
    message = "This notification type is already in the list."


class NotificationTypeUnknownError(_NotificationPreferenceError):
    """The type isn't in the caller's catalogue: unregistered, ``dev.demo``
    or not for the caller's roles (the response doesn't say which)."""

    code = "notification_type_unknown"
    detail = "A notification type isn't one you can change."
    message = "Unknown notification type."


class NotificationTypeMandatoryError(_NotificationPreferenceError):
    """``enabled: false`` for a mandatory type (``enabled: true`` on one is a
    no-op)."""

    code = "notification_type_mandatory"
    detail = "A mandatory notification type can't be turned off."
    message = "This notification type can't be turned off."


# --- Scheduling policy (#61) ---------------------------------------------------------


class InvalidEffectiveFromError(AppError):
    """A new scheduling policy version would take effect in the past, or not
    after every existing version (#61 D2)."""

    status = 422
    code = "invalid_effective_from"
    detail = (
        "A new policy must take effect now or later, and after every upcoming change."
    )

    def __init__(self) -> None:
        super().__init__()


class PolicyVersionInEffectError(AppError):
    """Only an upcoming scheduling policy version can be deleted; this one is
    in force or in the past (#61 D3)."""

    status = 409
    code = "policy_version_in_effect"
    detail = "This policy is already in effect, so it can't be deleted."

    def __init__(self) -> None:
        super().__init__()
