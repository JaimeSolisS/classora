"""Saved export setup errors with stable codes (#125 B3, B4, E5)."""

from typing import Final

from app.core.errors import AppError

MAX_SETUPS_PER_USER: Final = 50
"""The most setups one user may have (#125 E5)."""


class ReportSetupLimitReachedError(AppError):
    """``409 report_setup_limit_reached``: the caller already has
    :data:`MAX_SETUPS_PER_USER` setups (``details.max``)."""

    status = 409
    code = "report_setup_limit_reached"
    detail = "You already have the most saved setups. Delete one first."

    def __init__(self, maximum: int) -> None:
        super().__init__(details={"max": maximum})


class ReportSetupNameTakenError(AppError):
    """``409 report_setup_name_taken``: another setup of the caller has this
    name (compared case-insensitively)."""

    status = 409
    code = "report_setup_name_taken"
    detail = "You already have a saved setup with this name."

    def __init__(self) -> None:
        super().__init__()
