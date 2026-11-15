"""Helpers for the #98 receipt tests (``test_receipts_*``,
``test_billing_receipts_routes``).

- :func:`receipts_of`, :func:`receipt_of_payment`, :func:`series_value`:
  the committed rows.
- :func:`record` / :func:`record_in`: a payment through
  ``billing.service.record_payment`` in its own Unit of Work.
- :func:`receipt_path`, :func:`download`: the PDF route.
- :func:`pdf_text`: the text of a PDF (pypdf, #98 P3), whitespace collapsed;
  :func:`page_count`.
"""

import io
import re
import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.receipts import receipt_number
from app.modules.billing.schemas import (
    AllocationRequest,
    PaymentDraft,
    RecordedPayment,
)
from tests.integration import payment_support as ps
from tests.integration.auth_support import SetCookie
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import in_uow

OLDEST = AllocationRequest(mode="oldest_first")
NONE = AllocationRequest(mode="none")


def receipt_number_text(year: int, value: int) -> str:
    """``R-2031-000042`` (the app's own formatter)."""
    return receipt_number(year, value)


def receipt_path(family_id: object, receipt_id: object) -> str:
    return ps.account_path(family_id, f"/receipts/{receipt_id}/pdf")


def download(
    client: TestClient, cookie: SetCookie | None, family_id: object, receipt_id: object
) -> Response:
    """The PDF route as ``cookie``'s user (anonymous for ``None``)."""
    if cookie is None:
        client.cookies.clear()
        return client.get(receipt_path(family_id, receipt_id))
    return get(client, cookie, receipt_path(family_id, receipt_id))


def pdf_text(data: bytes) -> str:
    """Every page's text, joined, with runs of whitespace as one space."""
    reader = PdfReader(io.BytesIO(data))
    text = " ".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"\s+", " ", text)


def page_count(data: bytes) -> int:
    return len(PdfReader(io.BytesIO(data)).pages)


def receipts_of(url: str, account_id: object) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM receipts WHERE billing_account_id = :a ORDER BY number",
        {"a": account_id},
    )


def receipt_of_payment(url: str, payment_id: object) -> Any | None:
    rows = run_sql(
        url, "SELECT * FROM receipts WHERE payment_id = :p", {"p": payment_id}
    )
    return rows[0] if rows else None


def series_value(url: str, year: int) -> int:
    """``number_series.last_value`` of ``R`` and ``year`` (0 without a row)."""
    rows = run_sql(
        url,
        "SELECT last_value FROM number_series WHERE code = 'R' AND year = :y",
        {"y": year},
    )
    return int(rows[0].last_value) if rows else 0


def draft(
    account_id: uuid.UUID,
    amount: str = "100.00",
    allocation: AllocationRequest = OLDEST,
    **extra: Any,
) -> PaymentDraft:
    return PaymentDraft(
        billing_account_id=account_id,
        amount=Money(amount),
        received_on=ps.TODAY,
        payment_method_code="cash",
        allocation=allocation,
        **extra,
    )


async def record_in(session: AsyncSession, payment: PaymentDraft) -> RecordedPayment:
    return await billing.record_payment(session, payment)


def record(url: str, actor: uuid.UUID, payment: PaymentDraft) -> RecordedPayment:
    """``record_payment`` in its own committed Unit of Work."""
    return in_uow(url, lambda s: billing.record_payment(s, payment), actor_id=actor)


def new_account(
    url: str, students: int = 1
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """A new family with ``students`` students and its billing account."""
    family_id, members = ps.family(url, students)
    account = in_uow(url, lambda s: billing.ensure_billing_account(s, family_id))
    return family_id, account, members
