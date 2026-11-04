"""Payslip PDF content, language and download (#113 C4, D1, E2; E6, E7;
ADR-0015; #98 P3: text assertions with pypdf, not byte snapshots).

- es and en: the number, ``$1,250.00``, the month names, the title, the
  non-fiscal notice, a session line, a salary piece, a bonus, a deduction, a
  ``manual`` adjustment with its reason, an accented tutor name, and the
  "page X of Y" footer;
- 300 lines run over several pages with "page X of Y", within the receipts'
  smoke bound;
- markup and ``&<>`` in a description and a reason appear literally, and
  nothing is fetched;
- through the route: the attachment headers, and a staff member whose UI
  language is ``en`` downloads an ``es`` payslip in Spanish.

These render PDFs: WeasyPrint needs Pango (the API image and CI; not the
owner's Mac, #383 D6). Text is compared without whitespace, because pypdf may
split kerned words (#98a D5)."""

import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll.payslips import render_payslip_document
from tests.conftest import FrozenClock, frozen_clock
from tests.factories import payslips as factory
from tests.integration import payslip_support as ps
from tests.integration import receipt_support as rs
from tests.integration.auth_support import auth_client, create_user
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import staff
from tests.integration.invitation_support import sign_in
from tests.integration.payroll_calculation_support import clear
from tests.integration.payroll_payment_support import NOW, worked_period
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import locked

SMOKE_SECONDS = 5.0


def compact(text: str) -> str:
    return "".join(text.split())


EXPECTED = {
    "es": [
        "Comprobante de pago",
        "Fecha de emisión",
        "10 de marzo de 2031",
        "Del 1 de marzo de 2031 al 7 de marzo de 2031",
        "Este documento no es un comprobante fiscal (no es un CFDI de nómina).",
        "Cubierta por salario",
        "$100.00 por hora",
        "sesión 3 / 10",
        "7 / 28 de $600.00",
        "Bono de puntualidad",
        "Préstamo",
        "Ajuste manual",
        "Motivo: Pago acordado con la tutora",
    ],
    "en": [
        "Payslip",
        "Issue date",
        "March 10, 2031",
        "March 1, 2031 to March 7, 2031",
        "This document is not a tax document (it is not a CFDI de nómina).",
        "Covered by salary",
        "$100.00 per hour",
        "session 3 / 10",
        "7 / 28 of $600.00",
        "Bono de puntualidad",
        "Préstamo",
        "Manual adjustment",
        "Reason: Pago acordado con la tutora",
    ],
}


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_payslip_in_each_language(lang):
    other = "en" if lang == "es" else "es"

    rendered = render_payslip_document(factory.stored(lang))

    assert rendered.data.startswith(b"%PDF-")
    pages = rendered.page_count
    assert pages == rs.page_count(rendered.data)
    assert rendered.refused_urls == ()
    text = compact(rs.pdf_text(rendered.data))
    footer = {"es": "Página {} de {}", "en": "Page {} of {}"}[lang]
    assert compact(footer.format(1, pages)) in text
    assert compact(footer.format(pages, pages)) in text
    for expected in [
        "P-2031-000123",
        "$1,250.00",
        "$1,000.00",
        "-$300.00",
        "Tutora Núñez Peña",
        "Centro de Tutorías Aurora",
        *EXPECTED[lang],
    ]:
        assert compact(expected) in text, expected
    for unexpected in [EXPECTED[other][i] for i in (0, 2, 4, 11)]:
        assert compact(unexpected) not in text, unexpected


def test_a_long_payslip_runs_over_several_pages():
    lines = [
        factory.line(
            amount="10.00",
            rate="10.9100",
            service_date=f"2031-03-{1 + n % 28:02d}",
            minutes=55,
        )
        for n in range(300)
    ]
    stored = factory.stored("es", factory.snapshot(lines))

    started = time.monotonic()
    rendered = render_payslip_document(stored)
    elapsed = time.monotonic() - started

    assert rendered.page_count > 1
    pages = rendered.page_count
    text = compact(rs.pdf_text(rendered.data))
    assert compact(f"Página 1 de {pages}") in text
    assert compact(f"Página {pages} de {pages}") in text
    assert compact("$10.91 por hora") in text
    assert compact("$3,000.00") in text
    assert elapsed < SMOKE_SECONDS, elapsed


def test_markup_in_the_data_is_text_and_nothing_is_fetched():
    hostile = "<img src=x onerror=alert(1)> & <b>2</b>"
    lines: list[dict[str, Any]] = [
        factory.line(
            "pay_item",
            amount="10.00",
            pay_item={
                "type_name": "Bono",
                "direction": "bonus",
                "description": hostile,
            },
        ),
        factory.line(
            "adjustment",
            amount="5.00",
            adjustment={
                "kind": "manual",
                "reason": "Libros & <útiles>",
                "service_date": None,
                "class_kind": None,
                "origin_period": None,
                "paired_period": None,
            },
        ),
    ]

    rendered = render_payslip_document(factory.stored("es", factory.snapshot(lines)))

    assert rendered.refused_urls == ()
    text = compact(rs.pdf_text(rendered.data))
    assert compact(hostile) in text
    assert compact("Motivo: Libros & <útiles>") in text


# --- Through the route ----------------------------------------------------------------


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


def test_the_route_downloads_the_stored_language_whoever_asks(
    module_seeded_url, frozen
):
    url = module_seeded_url
    set_floor(url, "10-01")
    try:
        with auth_client(url) as client:
            owner = staff(url, client, (OWNER,))
            finance = staff(url, client, (FINANCE_STAFF,))
            tutor = create_user(url, roles=(TUTOR,), display_name="Tutora Núñez")
            period = worked_period(url, client, owner, tutor)
            locked(client, url, finance, owner, period)
            payslip = ps.payslip_of(url, period)
            assert payslip.language == "es"
            reader = create_user(url, roles=(FINANCE_STAFF,))
            run_sql(
                url,
                "INSERT INTO user_preferences (user_id, language) VALUES (:u, 'en')",
                {"u": reader.id},
            )
            responses = {
                "finance_en": ps.download(
                    client, sign_in(client, reader.username), tutor.id, payslip.id
                ),
                "tutor": ps.download(
                    client, sign_in(client, tutor.username), tutor.id, payslip.id
                ),
            }
    finally:
        clear(url)
        set_floor(url, None)

    for name, response in responses.items():
        assert response.status_code == 200, (name, response.text)
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == (
            f'attachment; filename="{payslip.number}.pdf"'
        )
        assert response.headers["cache-control"] == "no-store"
        text = compact(rs.pdf_text(response.content))
        assert compact("Comprobante de pago") in text, name
        assert compact(payslip.number) in text
        assert compact("Tutora Núñez") in text
        assert compact("$110.00") in text
        assert compact("Payslip") not in text
    assert responses["finance_en"].content[:5] == b"%PDF-"
