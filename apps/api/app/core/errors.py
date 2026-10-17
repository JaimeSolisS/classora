"""RFC 9457 Problem Details for every API error (architecture §4.3, step 7).

Every error response has ``Content-Type: application/problem+json`` and a body
with ``type`` (always ``"about:blank"``), ``title`` (the HTTP reason phrase),
``status``, a stable ``snake_case`` ``code``, the ``request_id`` and an optional
fixed ``detail``. Field-level errors add ``errors``.

Routes and dependencies raise :class:`AppError` (or a subclass); they never
build Problem JSON by hand. A domain error defines only its class attributes::

    class ThingNotFoundError(AppError):
        status = 404
        code = "thing_not_found"
        detail = "The thing does not exist."

Responses never contain internal details (exception class, message, traceback,
submitted values) in any environment; unexpected exceptions are logged with
their traceback instead (see ``app.core.middleware``).
"""

import re
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, ClassVar, TypedDict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id
from app.core.models import StaleVersionError

PROBLEM_JSON = "application/problem+json"
INTERNAL_ERROR_DETAIL = "An unexpected error occurred."


class FieldError(TypedDict):
    """One field-level error: location, Pydantic error type and message."""

    path: list[str | int]
    code: str
    message: str


class AppError(Exception):
    """Base class of every error that renders as Problem Details."""

    status: ClassVar[int] = 500
    code: ClassVar[str] = "internal_error"
    detail: str | None = None

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: Sequence[FieldError] | None = None,
    ) -> None:
        if detail is not None:
            self.detail = detail
        self.errors = list(errors) if errors is not None else None
        super().__init__(self.code)


class VersionConflictError(AppError):
    """The record changed since the client read it (optimistic concurrency).

    Takes no arguments, so no record data can reach the response.
    """

    status = 409
    code = "version_conflict"
    detail = "The resource was changed by another request. Reload it and try again."

    def __init__(self) -> None:
        super().__init__()


def status_title(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def status_code_name(status: int) -> str:
    """``snake_case`` form of the reason phrase: 405 -> ``method_not_allowed``."""
    return re.sub(r"[^a-z0-9]+", "_", status_title(status).lower()).strip("_")


def problem_response(
    status: int,
    code: str,
    detail: str | None = None,
    *,
    errors: Sequence[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """The single renderer for Problem Details responses."""
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": status_title(status),
        "status": status,
        "code": code,
        "request_id": get_request_id(),
    }
    if detail is not None:
        body["detail"] = detail
    if errors is not None:
        body["errors"] = list(errors)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type=PROBLEM_JSON,
        headers=dict(headers) if headers else None,
    )


def internal_error_response() -> JSONResponse:
    return problem_response(500, "internal_error", INTERNAL_ERROR_DETAIL)


def _validation_errors(exc: RequestValidationError) -> list[FieldError]:
    # Only loc, type and msg: never input, ctx or url (the submitted value).
    return [
        {
            "path": [part if isinstance(part, int) else str(part) for part in e["loc"]],
            "code": str(e["type"]),
            "message": str(e["msg"]),
        }
        for e in exc.errors()
    ]


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return problem_response(exc.status, exc.code, exc.detail, errors=exc.errors)


async def _stale_version_handler(request: Request, exc: Exception) -> JSONResponse:
    # The database layer's version conflict (app.core.models) renders as 409.
    error = VersionConflictError()
    return problem_response(error.status, error.code, error.detail)


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return problem_response(
        422,
        "validation_error",
        "The request is not valid.",
        errors=_validation_errors(exc),
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    # exc.detail is not rendered: it is free text that may carry internal data.
    return problem_response(
        exc.status_code, status_code_name(exc.status_code), headers=exc.headers
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StaleVersionError, _stale_version_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
