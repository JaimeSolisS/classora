"""RFC 9457 Problem Details for every API error (architecture §4.3, step 7).

Every error response has ``Content-Type: application/problem+json`` and a body
with ``type`` (always ``"about:blank"``), ``title`` (the HTTP reason phrase),
``status``, a stable ``snake_case`` ``code``, the ``request_id`` and an optional
fixed ``detail``. Field-level errors add ``errors``; a ``409
scheduling_conflict`` adds ``conflicts`` (ADR-0005 wire format, #48); a few
state errors add ``details``, a flat map of short codes (for example ``409
offer_not_pending`` ``{"status": "accepted"}``, #52) or lists of ids (``409
closeout_attendance_incomplete`` ``{"missing_student_ids": [...]}``, #77),
never record data. One staff-only exception carries an object: ``409
withdrawal_preview_stale`` ``{"preview": {...}}`` (the fresh preview, #101
B7).

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
from typing import Any, ClassVar, Literal, NotRequired, TypedDict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, TypeAdapter, with_config
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id
from app.core.models import StaleVersionError

PROBLEM_JSON = "application/problem+json"
INTERNAL_ERROR_DETAIL = "An unexpected error occurred."
PROBLEM_DETAILS_REF = "#/components/schemas/ProblemDetails"


@with_config(ConfigDict(extra="forbid"))
class FieldError(TypedDict):
    """One field-level error: location, Pydantic error type and message."""

    path: list[str | int]
    code: str
    message: str


@with_config(ConfigDict(extra="forbid"))
class SchedulingConflict(TypedDict):
    """One broken scheduling rule (ADR-0005): its code, whether it is
    ``hard`` (never allowed) or ``soft`` (allowed with an override), and
    code-specific details."""

    code: str
    severity: Literal["hard", "soft"]
    details: dict[str, Any]


# The single source of the ProblemDetails schema in the OpenAPI document
# (install_problem_details_openapi). tests/unit/test_openapi.py validates real
# problem_response output against it. Docstrings become schema descriptions.
@with_config(ConfigDict(extra="forbid"))
class ProblemDetails(TypedDict):
    """The body of every error response (RFC 9457 Problem Details)."""

    type: Literal["about:blank"]
    title: str
    status: int
    code: str
    request_id: str
    detail: NotRequired[str]
    errors: NotRequired[list[FieldError]]
    conflicts: NotRequired[list[SchedulingConflict]]
    details: NotRequired[dict[str, str | list[str] | dict[str, Any]]]


class AppError(Exception):
    """Base class of every error that renders as Problem Details."""

    status: ClassVar[int] = 500
    code: ClassVar[str] = "internal_error"
    detail: str | None = None
    headers: Mapping[str, str] | None = None
    """Extra response headers (for example ``Retry-After`` on a ``429``)."""

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: Sequence[FieldError] | None = None,
        conflicts: Sequence[SchedulingConflict] | None = None,
        details: Mapping[str, str | list[str] | dict[str, Any]] | None = None,
    ) -> None:
        if detail is not None:
            self.detail = detail
        self.errors = list(errors) if errors is not None else None
        self.conflicts = list(conflicts) if conflicts is not None else None
        self.details = dict(details) if details is not None else None
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
    conflicts: Sequence[SchedulingConflict] | None = None,
    details: Mapping[str, str | list[str] | dict[str, Any]] | None = None,
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
    if conflicts is not None:
        body["conflicts"] = list(conflicts)
    if details is not None:
        body["details"] = dict(details)
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
    # Type narrowing only: the handler is registered for this exception type.
    assert isinstance(exc, AppError)  # noqa: S101
    return problem_response(
        exc.status,
        exc.code,
        exc.detail,
        errors=exc.errors,
        conflicts=exc.conflicts,
        details=exc.details,
        headers=exc.headers,
    )


async def _stale_version_handler(request: Request, exc: Exception) -> JSONResponse:
    # The database layer's version conflict (app.core.models) renders as 409.
    error = VersionConflictError()
    return problem_response(error.status, error.code, error.detail)


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Type narrowing only: the handler is registered for this exception type.
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    return problem_response(
        422,
        "validation_error",
        "The request is not valid.",
        errors=_validation_errors(exc),
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Type narrowing only: the handler is registered for this exception type.
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    # exc.detail is not rendered: it is free text that may carry internal data.
    return problem_response(
        exc.status_code, status_code_name(exc.status_code), headers=exc.headers
    )


def problem_details_schemas() -> dict[str, Any]:
    """``ProblemDetails``, ``FieldError`` and ``SchedulingConflict`` as OpenAPI
    component schemas."""
    schema = TypeAdapter(ProblemDetails).json_schema(
        ref_template="#/components/schemas/{model}"
    )
    definitions: dict[str, Any] = schema.pop("$defs", {})
    return {**definitions, "ProblemDetails": schema}


def problem_response_doc(description: str) -> dict[str, Any]:
    """An entry for a route's ``responses=``: a Problem Details error."""
    return {
        "description": description,
        "content": {PROBLEM_JSON: {"schema": {"$ref": PROBLEM_DETAILS_REF}}},
    }


def _use_problem_details(document: dict[str, Any]) -> dict[str, Any]:
    # FastAPI documents every 422 with its own HTTPValidationError schema, which
    # this API never returns: the handler above renders Problem Details.
    for operations in document.get("paths", {}).values():
        for operation in operations.values():
            responses = operation.get("responses", {})
            if "422" in responses:
                responses["422"] = problem_response_doc("Validation Error")
    schemas: dict[str, Any] = document.setdefault("components", {}).setdefault(
        "schemas", {}
    )
    for name in ("HTTPValidationError", "ValidationError"):
        schemas.pop(name, None)
    schemas.update(problem_details_schemas())
    document["components"]["schemas"] = dict(sorted(schemas.items()))
    return document


def install_problem_details_openapi(app: FastAPI) -> None:
    """Make the app's OpenAPI document describe errors as Problem Details."""
    build_default = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = _use_problem_details(build_default())
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StaleVersionError, _stale_version_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
