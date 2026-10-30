"""Receipt PDF content and language (#98 E1-E4; ADR-0015, E14, E15; P3:
text assertions with pypdf, not byte snapshots).

- E2: es and en: the number, ``$1,250.00``, the title, the method label,
  the month name, the CFDI notice, a student label line, an accented
  family name, one page; 120 allocation lines → several pages with the
  "page X of Y" footer, rendered within a smoke bound;
- E3: a payer name with markup and a description with ``&<>`` appear
  literally, and nothing is fetched;
- E4: a staff member whose UI language is ``en`` downloads an ``es``
  receipt in Spanish.

These render PDFs: WeasyPrint needs Pango (the API image and CI; not the
owner's Mac, #383 D6). Text is compared without whitespace, because pypdf
may split kerned words (#98a D5)."""

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.identity.permissions import FINANCE_STAFF
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import payment_support as ps
from tests.integration import receipt_support as rs
from tests.integration.auth_support import auth_client, create_user
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in

SMOKE_SECONDS = 5.0


def compact(text: str) -> str:
    return "".join(text.split())


def _line(**overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "charge_id": str(uuid.uuid4()),
        "student_id": str(uuid.uuid4()),
        "student_name": "Ana Peña Núñez",
        "kind": "session",
        "service_date": "2031-03-03",
        "service_period_end": None,
        "scheduled_minutes": 60,
        "description": None,
        "amount": "1000.00",
    }
    line.update(overrides)
    return line


def _stored(
    lang: str,
    lines: list[dict[str, Any]] | None = None,
    *,
    amount: str = "1250.00",
    allocated: str = "1000.00",
    payer_name: str | None = None,
    reversed_on: date | None = None,
) -> billing.StoredReceipt:
    lines = [_line()] if lines is None else lines
    unallocated = str(Money(amount) - Money(allocated))
    snapshot = {
        "v": 1,
        "center": {
            "name": "Centro de Tutorías Aurora",
            "address": "Av. Siempre Viva 742, CDMX",
            "phone": "+52 55 1234 5678",
            "email": "hola@aurora.example",
        },
        "family": {"id": str(uuid.uuid4()), "name": "Familia Peña"},
        "payer_name": payer_name,
        "payment": {
            "id": str(uuid.uuid4()),
            "amount": amount,
            "received_on": "2031-03-10",
            "method_code": "transfer",
            "reference": "SPEI-77",
        },
        "issued_on": "2031-03-10",
        "allocations": lines,
        "totals": {"allocated_total": allocated, "unallocated": unallocated},
    }
    return billing.StoredReceipt(
        id=uuid.uuid4(),
        number="R-2031-000123",
        payment_id=uuid.uuid4(),
        issued_at=datetime(2031, 3, 10, 18, 0, tzinfo=UTC),
        language=lang,
        template_version=1,
        snapshot=snapshot,
        reversed_on=reversed_on,
    )


EXPECTED = {
    "es": [
        "Recibo de pago",
        "Transferencia",
        "10 de marzo de 2031",
        "Este recibo no es un comprobante fiscal (CFDI).",
        "Sesión del 3 de marzo de 2031 (60 min)",
        "Recibido de",
        "Saldo a favor de la cuenta",
        "Página 1 de 1",
    ],
    "en": [
        "Payment receipt",
        "Transfer",
        "March 10, 2031",
        "This receipt is not a tax invoice (CFDI).",
        "Session on March 3, 2031 (60 min)",
        "Received from",
        "Left as account credit",
        "Page 1 of 1",
    ],
}


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_receipt_in_each_language(lang):
    other = "en" if lang == "es" else "es"

    rendered = billing.render_receipt_document(_stored(lang))

    assert rendered.data.startswith(b"%PDF-")
    assert rendered.page_count == 1 == rs.page_count(rendered.data)
    assert rendered.refused_urls == ()
    text = compact(rs.pdf_text(rendered.data))
    for expected in [
        "R-2031-000123",
        "$1,250.00",
        "$1,000.00",
        "$250.00",
        "Familia Peña",
        "Ana Peña Núñez",
        "Centro de Tutorías Aurora",
        "SPEI-77",
        *EXPECTED[lang],
    ]:
        assert compact(expected) in text, expected
    # The other language's title, date, notice and label ("Transfer" is part
    # of "Transferencia", so the method is left out).
    for unexpected in [EXPECTED[other][i] for i in (0, 2, 3, 4)]:
        assert compact(unexpected) not in text, unexpected


def test_a_long_receipt_runs_over_several_pages():
    lines = [
        _line(
            student_name=f"Estudiante {n:03d}",
            amount="10.00",
            kind="manual",
            description=f"Concepto {n:03d}",
            scheduled_minutes=None,
        )
        for n in range(120)
    ]
    stored = _stored("es", lines, amount="1200.00", allocated="1200.00")

    started = time.monotonic()
    rendered = billing.render_receipt_document(stored)
    elapsed = time.monotonic() - started

    assert rendered.page_count > 1
    text = compact(rs.pdf_text(rendered.data))
    pages = rendered.page_count
    assert compact(f"Página 1 de {pages}") in text
    assert compact(f"Página {pages} de {pages}") in text
    for n in (0, 59, 119):
        assert compact(f"Concepto {n:03d}") in text
    assert compact("Saldo a favor") not in text  # nothing left as credit
    assert elapsed < SMOKE_SECONDS, elapsed


def test_markup_in_the_data_is_text_and_nothing_is_fetched():
    stored = _stored(
        "es",
        [
            _line(
                kind="manual", description="Libros & <útiles> 2", scheduled_minutes=None
            )
        ],
        payer_name="<img src=x onerror=alert(1)>",
    )

    rendered = billing.render_receipt_document(stored)

    assert rendered.refused_urls == ()
    text = compact(rs.pdf_text(rendered.data))
    assert compact("<img src=x onerror=alert(1)>") in text
    assert compact("Libros & <útiles> 2") in text


def test_the_reversed_banner():
    rendered = billing.render_receipt_document(
        _stored("en", reversed_on=date(2031, 4, 2))
    )

    text = compact(rs.pdf_text(rendered.data))
    assert compact("REVERSED on April 2, 2031") in text
    assert compact("R-2031-000123") in text


# --- E4: through the route, the stored language wins ---------------------------------


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


def test_an_english_speaking_cashier_downloads_a_spanish_receipt(
    module_seeded_url, frozen
):
    url = module_seeded_url
    cashier = create_user(url, roles=(FINANCE_STAFF,))
    run_sql(
        url,
        "INSERT INTO user_preferences (user_id, language) VALUES (:u, 'en')",
        {"u": cashier.id},
    )
    fam, [s1] = ps.family(url)
    ps.charges(url, (s1, "1250.00", ps.TODAY))
    with auth_client(url) as client:
        staff = sign_in(client, cashier.username)
        paid = ps.pay(client, staff, fam, ps.payment_body("1250.00"))
        assert paid.status_code == 201, paid.text
        receipt = paid.json()["receipt"]

        response = rs.download(client, staff, fam, receipt["id"])

    assert response.status_code == 200, response.text
    text = compact(rs.pdf_text(response.content))
    assert compact("Recibo de pago") in text
    assert compact("Efectivo") in text
    assert compact("10 de marzo de 2031") in text
    assert compact("$1,250.00") in text
    assert compact("Payment receipt") not in text
    assert rs.receipt_of_payment(url, paid.json()["payment"]["id"]).language == "es"
