"""Recently deleted record routes (#129 B1, B2; :mod:`app.workflows
.deleted_records` gives the record policies and the order).

The routes live here, next to the workflow they call, because a module's
router may not import ``app.workflows`` (ADR-0002). Both are
``authenticated()``: anyone may ask, and only the records they deleted and
may still restore are listed or restored (everything else is the ``404`` of
an unknown id).

- ``GET /api/v1/deleted-records``: the caller's restorable records, newest
  first, at most 100.
- ``POST /api/v1/deleted-records/{kind}/{record_id}/restore``: restore one
  (body ``{version}``).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.workflows import deleted_records as workflow
from app.workflows.deleted_record_schemas import (
    DeletedRecordKind,
    DeletedRecordList,
    DeletedRecordRestore,
    RestoredRecord,
)

router = APIRouter(prefix="/api/v1/deleted-records", tags=["deleted-records"])

_NOT_FOUND: Final = problem_response_doc(
    "No such deleted record, it isn't one you deleted, or its 30 days have"
    " passed (not_found); also the kind's own 404 when you no longer relate"
    " to its student."
)


@router.get("", responses={**PERMISSION_RESPONSES})
async def list_deleted_records(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
) -> DeletedRecordList:
    """The records you deleted in the last 30 days and may still restore,
    newest first (at most 100): notes, evaluation drafts, learning plan
    drafts, homework and progress reports. No content, only what names
    them."""
    return await workflow.list_deleted(uow.session, principal)


@router.post(
    "/{kind}/{record_id}/restore",
    responses={
        **PERMISSION_RESPONSES,
        403: problem_response_doc(
            "The kind's own delete check fails now (for example"
            " note_read_only, evaluation_read_only, plan_subject_not_assigned,"
            " assignment_read_only, progress_report_read_only); a failed"
            " Origin or X-CSRF-Token check (csrf_failed);"
            " privacy_notice_required."
        ),
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The record changed since it was listed (version_conflict), or a"
            " live record already takes its place"
            " (deleted_record_restore_conflict)."
        ),
        422: problem_response_doc(
            "An unknown kind, or the body is not valid (validation_error)."
        ),
    },
)
async def restore_deleted_record(
    principal: Annotated[Principal, authenticated()],
    kind: DeletedRecordKind,
    record_id: uuid.UUID,
    body: DeletedRecordRestore,
    uow: UnitOfWorkDep,
) -> RestoredRecord:
    """Restore a record you deleted: it is back exactly as it was (internal
    or draft), with a new version. Nobody is notified."""
    return await workflow.restore(
        uow.session,
        principal=principal,
        kind=kind,
        record_id=record_id,
        version=body.version,
    )
