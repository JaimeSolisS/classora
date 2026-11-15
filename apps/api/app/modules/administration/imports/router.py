"""The import routes (#122 B-E; ``_docs/api.md`` Imports).

All five are ``authenticated()`` plus :func:`importer`: ``403
permission_denied`` unless the caller may import some registered kind
(students: ``student.manage``; tutors: ``user.invite`` and may assign
``tutor``). A job of a kind the caller may not import is ``404``.

``POST /api/v1/imports`` reads its body itself, so the 2 MiB cap applies
before anything is parsed (``Content-Length`` first, then while the body
streams): ``401`` → ``403 csrf_failed`` → ``403 privacy_notice_required`` →
``403 permission_denied`` → ``413 import_too_large`` → ``422
validation_error`` (an unknown ``kind`` on ``["body", "kind"]``) → ``403
permission_denied`` for that kind → ``422 import_file_invalid`` /
``import_too_many_rows`` → ``201``. ``openapi_extra`` documents the body.

``POST …/commit`` answers ``202`` and queues ``administration:run_imports``
after the Unit of Work commits (a background task).
"""

import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from procrastinate import App
from pydantic import ValidationError

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
)
from app.core.errors import problem_response_doc
from app.core.jobs import defer_import_run, get_job_app
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.administration.imports import service
from app.modules.administration.imports.errors import (
    MAX_BODY_BYTES,
    ImportTooLargeError,
)
from app.modules.administration.imports.registry import import_kind
from app.modules.administration.imports.schemas import (
    ImportJobCreate,
    ImportJobDetail,
    ImportJobStatus,
    ImportRowItem,
    ImportRowStatus,
    ImportVersion,
)

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

JobAppDep = Annotated[App, Depends(get_job_app)]

_JOB: Final = "/{import_id}"
_NOT_FOUND: Final = problem_response_doc(
    "No such import, or one of a kind the caller may not import (not_found)."
)
_VERSION_CONFLICT: Final = (
    "The import changed since it was loaded (version_conflict); reload and retry"
)


async def importer(principal: Annotated[Principal, authenticated()]) -> Principal:
    """``403`` unless the caller may import some kind, before the body."""
    service.ensure_may_import_any(principal)
    return principal


Importer = Annotated[Principal, Depends(importer)]


async def _capped_body(request: Request) -> bytes:
    """The body, at most :data:`MAX_BODY_BYTES` (``413`` otherwise)."""
    length = request.headers.get("content-length")
    if length is not None and length.strip().isdigit() and int(length) > MAX_BODY_BYTES:
        raise ImportTooLargeError()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ImportTooLargeError()
        chunks.append(chunk)
    return b"".join(chunks)


def _body_errors(exc: ValidationError) -> RequestValidationError:
    return RequestValidationError(
        [
            {
                "loc": ("body", *error["loc"]),
                "type": error["type"],
                "msg": error["msg"],
            }
            for error in exc.errors(include_input=False, include_url=False)
        ]
    )


def _parse_create(raw: bytes) -> ImportJobCreate:
    try:
        body = ImportJobCreate.model_validate_json(raw)
    except ValidationError as exc:
        raise _body_errors(exc) from None
    if import_kind(body.kind) is None:
        raise RequestValidationError(
            [
                {
                    "loc": ("body", "kind"),
                    "type": "import_kind_unknown",
                    "msg": "Not an import kind.",
                }
            ]
        )
    return body


_CREATE_BODY: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": ImportJobCreate.model_json_schema()}
        },
    }
}


@router.post(
    "",
    status_code=201,
    openapi_extra=_CREATE_BODY,
    responses={
        **PERMISSION_RESPONSES,
        413: problem_response_doc(
            "The body is over 2 MiB (import_too_large; details.max_bytes)."
        ),
        422: problem_response_doc(
            "The body is not valid, or kind is not an import kind"
            " (validation_error); the CSV can't be read (import_file_invalid;"
            " details.reason: encoding, empty, malformed with details.line,"
            " duplicate_column, missing_columns with details.columns, or"
            " too_many_columns with details.max); more than 1,000 data rows"
            " (import_too_many_rows; details.max)."
        ),
    },
)
async def create_import(
    principal: Importer, request: Request, uow: UnitOfWorkDep
) -> ImportJobDetail:
    """Stage a CSV: every row gets `ready`, `duplicate` or `error` with codes,
    and nothing is created until the import is committed. The file's text is
    never stored. `403` when the caller may not import that kind."""
    body = _parse_create(await _capped_body(request))
    return await service.create_import_job(
        uow.session,
        principal=principal,
        kind_code=body.kind,
        file_name=body.file_name,
        csv_text=body.csv,
    )


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc("A filter, limit or offset isn't valid."),
    },
)
async def list_imports(
    principal: Importer,
    uow: UnitOfWorkDep,
    page: OffsetPagination,
    kind: Annotated[str | None, Query(max_length=32)] = None,
    status: ImportJobStatus | None = None,
) -> OffsetPage[ImportJobDetail]:
    """The imports of the kinds the caller may import, newest first."""
    return await service.list_import_jobs(
        uow.session, principal=principal, kind=kind, status=status, page=page
    )


@router.get(_JOB, responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND})
async def get_import(
    principal: Importer, import_id: uuid.UUID, uow: UnitOfWorkDep
) -> ImportJobDetail:
    """One import with its counts."""
    return await service.get_import_job(
        uow.session, principal=principal, job_id=import_id
    )


@router.get(
    f"{_JOB}/rows",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        422: problem_response_doc("A filter, limit or offset isn't valid."),
    },
)
async def list_import_rows(
    principal: Importer,
    import_id: uuid.UUID,
    uow: UnitOfWorkDep,
    page: OffsetPagination,
    status: ImportRowStatus | None = None,
) -> OffsetPage[ImportRowItem]:
    """An import's rows in file order, with their codes. `values` is shown
    while the import is staged, committing or failed, and `null` after."""
    return await service.list_import_rows(
        uow.session, principal=principal, job_id=import_id, status=status, page=page
    )


@router.post(
    f"{_JOB}/commit",
    status_code=202,
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            f"{_VERSION_CONFLICT}; the import is not staged or failed"
            " (import_not_committable)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error); no row is ready"
            " (import_nothing_to_commit)."
        ),
    },
)
async def commit_import(
    principal: Importer,
    import_id: uuid.UUID,
    body: ImportVersion,
    uow: UnitOfWorkDep,
    background: BackgroundTasks,
    job_app: JobAppDep,
) -> ImportJobDetail:
    """Import the ready rows. The work runs in the background in batches of
    rows; poll the import until it is `committed` or `failed`. A failed
    import can be committed again: it resumes with the rows left."""
    detail = await service.request_commit(
        uow.session, principal=principal, job_id=import_id, version=body.version
    )
    # Runs after the response, so after the Unit of Work committed.
    background.add_task(defer_import_run, job_app)
    return detail


@router.post(
    f"{_JOB}/discard",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            f"{_VERSION_CONFLICT}; the import is not staged or failed"
            " (import_not_discardable)."
        ),
        422: problem_response_doc("The body is not valid (validation_error)."),
    },
)
async def discard_import(
    principal: Importer, import_id: uuid.UUID, body: ImportVersion, uow: UnitOfWorkDep
) -> ImportJobDetail:
    """Drop a staged or failed import and its staged rows. Records a failed
    import already created stay."""
    return await service.discard_job(
        uow.session, principal=principal, job_id=import_id, version=body.version
    )
