"""Typed administration errors with stable codes. None takes a submitted value
or record data, so neither can reach a response."""

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
