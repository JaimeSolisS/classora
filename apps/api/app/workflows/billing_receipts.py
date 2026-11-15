"""Receipt downloads (#98 F; ADR-0006 E13, E15; ADR-0015): the cross-module
work behind ``app.workflows.billing_receipts_router``.

The billing module owns the receipt rows and their rendering
(``billing.service``: ``get_receipt``, ``render_receipt``); the families
module owns the family. This workflow joins them (ADR-0002).

**Access** is the #364 record policy of :mod:`app.workflows.billing_accounts`
(``_visible_family``): ``billing.view`` (Owner, Administrator, Finance
Staff) sees every family; a guardian only the family of their **active**
guardian profile (``family_guardians``, ADR-0007 D17); anyone else, an
unknown family, a family without an account and a receipt that isn't on
this family's account all get the same ``404 not_found``. At most four
statements (the guardian's own family, the family, the account, the
receipt; one more for the time zone of a reversed payment's banner).

**Rendering** (:func:`render`) is synchronous and CPU-bound: the route runs
it in a worker thread after its read transaction ended. A render failure
is logged with the receipt id only (never the snapshot) and answers the
standard ``500 internal_error``. Downloads are not audited (reads aren't)
and not rate limited (P9).
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError
from app.core.errors import AppError
from app.modules.billing import service as billing
from app.workflows.billing_accounts import _visible_family

logger = logging.getLogger(__name__)


class ReceiptRenderError(AppError):
    """The receipt could not be rendered (F3): the standard ``500``."""

    status = 500
    code = "internal_error"
    detail = "The server could not complete the request."


@dataclass(frozen=True)
class ReceiptFile:
    """A rendered receipt: its file name and PDF bytes."""

    filename: str
    data: bytes


async def load_receipt(
    session: AsyncSession,
    principal: Principal,
    family_id: uuid.UUID,
    receipt_id: uuid.UUID,
) -> billing.StoredReceipt:
    """The family's receipt under the record policy, or ``404``."""
    await _visible_family(session, principal, family_id)
    account = await billing.account_of_family(session, family_id)
    if account is None:
        raise RecordNotFoundError()
    stored = await billing.get_receipt(session, account.id, receipt_id)
    if stored is None:
        raise RecordNotFoundError()
    return stored


def render(stored: billing.StoredReceipt) -> ReceiptFile:
    """The receipt's PDF in its stored language and template version
    (synchronous; see the module docstring)."""
    try:
        data = billing.render_receipt(stored)
    except Exception as error:
        # The receipt id and the error type only: never the snapshot.
        logger.error(  # noqa: TRY400 - no traceback: it may quote the data
            "receipt %s could not be rendered (%s)", stored.id, type(error).__name__
        )
        raise ReceiptRenderError() from None
    return ReceiptFile(filename=f"{stored.number}.pdf", data=data)
