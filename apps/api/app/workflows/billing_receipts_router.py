"""Receipt download route (#98 F1-F3; P9; ``app.workflows.billing_receipts``).

- ``GET /api/v1/families/{family_id}/billing-account/receipts/{receipt_id}/pdf``
  (``authenticated()`` + the #364 record policy): ``200
  application/pdf`` as an attachment named ``<number>.pdf``, ``Cache-Control:
  no-store``. Check order ``401`` → ``403 privacy_notice_required`` →
  ``422`` (a malformed id) → ``404 not_found`` (a hidden or unknown family,
  or a receipt that isn't on its account) → ``200``.

The PDF is rendered on every request (never stored, owner decision
2026-10-21) in the receipt's stored language, whoever downloads it. The
read transaction is committed before rendering, which runs in a worker
thread (``anyio.to_thread.run_sync``), so no connection is held while
WeasyPrint works. No audit event, no rate limit (P9).
"""

import uuid
from typing import Annotated, Final

import anyio
from fastapi import APIRouter, Response

from app.core.authz import AUTH_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.workflows import billing_receipts

PDF: Final = "application/pdf"

router = APIRouter(tags=["billing"])


@router.get(
    "/api/v1/families/{family_id}/billing-account/receipts/{receipt_id}/pdf",
    response_class=Response,
    responses={
        200: {
            "description": "The receipt as a PDF attachment.",
            "content": {PDF: {"schema": {"type": "string", "format": "binary"}}},
        },
        **AUTH_RESPONSES,
        403: problem_response_doc(
            "The privacy notice must be accepted first (privacy_notice_required)."
        ),
        404: problem_response_doc(
            "No such family or receipt, or one whose account you can't see (not_found)."
        ),
        422: problem_response_doc("An id is not a UUID (validation_error)."),
        500: problem_response_doc(
            "The receipt could not be rendered (internal_error)."
        ),
    },
)
async def download_receipt(
    principal: Annotated[Principal, authenticated()],
    family_id: uuid.UUID,
    receipt_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> Response:
    """Download a payment receipt as a PDF (non-tax: not a CFDI), in the
    language it was issued in. Staff with billing.view download any
    family's receipts; a guardian only their own family's."""
    stored = await billing_receipts.load_receipt(
        uow.session, principal, family_id, receipt_id
    )
    # End the read transaction before the CPU-bound render (#98 F3), so no
    # connection waits on it; the Unit of Work then commits nothing.
    await uow.session.commit()
    file = await anyio.to_thread.run_sync(billing_receipts.render, stored)
    return Response(
        content=file.data,
        media_type=PDF,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "Cache-Control": "no-store",
        },
    )
