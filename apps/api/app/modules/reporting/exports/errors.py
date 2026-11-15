"""Typed export errors with stable codes (#124 B2, C3, E6).

None carries a value of the export: a problem detail names a limit or a
request field, never a row.
"""

from typing import Final

from app.core.errors import AppError

MAX_QUEUED_PER_USER: Final = 3
"""The most ``queued`` exports one user may have (#124 E6)."""


class ExportRangeInvalidError(AppError):
    """A date range is reversed or longer than 366 days (#124 A3, E6):
    ``422 export_range_invalid`` at the offending filter."""

    status = 422
    code = "export_range_invalid"
    detail = "The export's date range is not valid."

    def __init__(self, field: str, message: str) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "filters", field],
                    "code": "export_range_invalid",
                    "message": message,
                }
            ]
        )


class ExportLimitReachedError(AppError):
    """The caller already has :data:`MAX_QUEUED_PER_USER` queued exports."""

    status = 409
    code = "export_limit_reached"
    detail = "You already have the most exports waiting. Wait for one to finish."

    def __init__(self) -> None:
        super().__init__(details={"max": MAX_QUEUED_PER_USER})


class ExportNotReadyError(AppError):
    """The export is ``queued`` or ``failed``: there is no file to download."""

    status = 409
    code = "export_not_ready"
    detail = "This export has no file to download."

    def __init__(self) -> None:
        super().__init__()


class ExportExpiredError(AppError):
    """The export's file was deleted, or is past its expiry (#124 C3)."""

    status = 410
    code = "export_expired"
    detail = "This export's file has expired. Request the export again."

    def __init__(self) -> None:
        super().__init__()
