"""Typed import errors with stable codes (#122 B1-B3, D1, E1).

None takes a cell value: a problem detail names a reason, a line number, the
kind's own column names or a limit, never the file's content.
"""

from collections.abc import Sequence
from typing import Final, Literal

from app.core.errors import AppError

MAX_BODY_BYTES: Final = 2_097_152
"""The largest ``POST /api/v1/imports`` body (2 MiB)."""
MAX_DATA_ROWS: Final = 1000
"""The most data rows a file may have."""

type FileInvalidReason = Literal[
    "encoding",
    "empty",
    "malformed",
    "duplicate_column",
    "missing_columns",
    "too_many_columns",
]


class ImportTooLargeError(AppError):
    """The request body is over :data:`MAX_BODY_BYTES` (by ``Content-Length``
    or while it streams)."""

    status = 413
    code = "import_too_large"
    detail = "The file is too large. The limit is 2 MiB."

    def __init__(self) -> None:
        super().__init__(details={"max_bytes": MAX_BODY_BYTES})


class ImportFileInvalidError(AppError):
    """The CSV can't be read (#122 B3). ``reason`` says why; ``line`` (for
    ``malformed``), ``columns`` (for ``missing_columns``: the kind's required
    columns that are absent) or ``max`` (for ``too_many_columns``) add
    where. Never a cell value."""

    status = 422
    code = "import_file_invalid"
    detail = "The file can't be imported."

    def __init__(
        self,
        reason: FileInvalidReason,
        *,
        line: int | None = None,
        columns: Sequence[str] | None = None,
        maximum: int | None = None,
    ) -> None:
        details: dict[str, str | int | list[str]] = {"reason": reason}
        if line is not None:
            details["line"] = line
        if columns is not None:
            details["columns"] = list(columns)
        if maximum is not None:
            details["max"] = maximum
        super().__init__(details=details)


class ImportTooManyRowsError(AppError):
    """The file has more than :data:`MAX_DATA_ROWS` data rows (#122 B2)."""

    status = 422
    code = "import_too_many_rows"
    detail = "The file has too many rows. The limit is 1,000."

    def __init__(self) -> None:
        super().__init__(details={"max": MAX_DATA_ROWS})


class ImportNotCommittableError(AppError):
    """Only a ``staged`` or ``failed`` job can be committed (#122 D1)."""

    status = 409
    code = "import_not_committable"
    detail = "This import can't be committed in its current status."

    def __init__(self) -> None:
        super().__init__()


class ImportNothingToCommitError(AppError):
    """The job has no ``ready`` row (#122 D1)."""

    status = 422
    code = "import_nothing_to_commit"
    detail = "This import has no rows ready to import."

    def __init__(self) -> None:
        super().__init__()


class ImportNotDiscardableError(AppError):
    """Only a ``staged`` or ``failed`` job can be discarded (#122 E1)."""

    status = 409
    code = "import_not_discardable"
    detail = "This import can't be discarded in its current status."

    def __init__(self) -> None:
        super().__init__()
