"""Typed administration errors with stable codes. None takes arguments, so no
submitted value or record data reaches a response."""

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
