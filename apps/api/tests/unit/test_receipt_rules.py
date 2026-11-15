"""Receipt rules without a database or Pango (#98 J2; B1, B2, C5, D4, E1,
E3, P6, P8; #121 B4, B6): the snapshot models (v1 and v2), the label of
each charge kind, the template-version selector and the snapshot-version
match, number formatting and overflow, the ``next_number`` argument checks
and the receipt HTML (escaping, no staff data, the v2 message)."""

import asyncio
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.core import numbering
from app.core.money import Money
from app.core.numbering_models import SERIES_CODES
from app.modules.billing import receipts
from app.modules.billing.models import CHARGE_KINDS, RECEIPT_LANGUAGES
from app.modules.billing.receipts import (
    RECEIPT_TEMPLATE_VERSION,
    RECEIPT_TEMPLATES,
    ReceiptSnapshotMismatchError,
    ReceiptSnapshotV1,
    ReceiptSnapshotV2,
    SnapshotAllocation,
    StoredReceipt,
    UnknownReceiptTemplateError,
    allocation_label,
    parse_snapshot,
    receipt_html,
    receipt_number,
    template_for,
)
from app.modules.identity.preferences import LANGUAGES


def _line(**overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "charge_id": str(uuid.uuid4()),
        "student_id": str(uuid.uuid4()),
        "student_name": "Ana Peña",
        "kind": "session",
        "service_date": "2031-03-03",
        "service_period_end": None,
        "scheduled_minutes": 60,
        "description": None,
        "amount": "100.00",
    }
    line.update(overrides)
    return line


def _snapshot(**overrides: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "v": 1,
        "center": {
            "name": "Centro Aurora",
            "address": None,
            "phone": None,
            "email": None,
        },
        "family": {"id": str(uuid.uuid4()), "name": "Familia Peña"},
        "payer_name": None,
        "payment": {
            "id": str(uuid.uuid4()),
            "amount": "150.00",
            "received_on": "2031-03-10",
            "method_code": "cash",
            "reference": None,
        },
        "issued_on": "2031-03-10",
        "allocations": [_line()],
        "totals": {"allocated_total": "100.00", "unallocated": "50.00"},
    }
    snapshot.update(overrides)
    return snapshot


def _stored(snapshot: dict[str, Any] | None = None, **overrides: Any) -> StoredReceipt:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "number": "R-2031-000007",
        "payment_id": uuid.uuid4(),
        "issued_at": datetime(2031, 3, 10, 18, tzinfo=UTC),
        "language": "es",
        "template_version": 1,
        "snapshot": _snapshot() if snapshot is None else snapshot,
        "reversed_on": None,
    }
    values.update(overrides)
    return StoredReceipt(**values)


def _snapshot_v2(message: str | None = "Gracias por su pago.", **overrides: Any):
    return _snapshot(v=2, message=message, **overrides)


def _stored_v2(
    message: str | None = "Gracias por su pago.", **overrides: Any
) -> StoredReceipt:
    return _stored(_snapshot_v2(message), template_version=2, **overrides)


# --- The snapshot (C5, P6) -----------------------------------------------------------


def test_the_snapshot_round_trips_through_json_with_money_as_strings():
    parsed = parse_snapshot(_snapshot())

    dumped = parsed.model_dump(mode="json")

    assert dumped["payment"]["amount"] == "150.00"
    assert dumped["allocations"][0]["amount"] == "100.00"
    assert dumped["totals"] == {"allocated_total": "100.00", "unallocated": "50.00"}
    assert parse_snapshot(dumped) == parsed
    assert parsed.payment.amount == Money("150.00")


@pytest.mark.parametrize(
    "overrides",
    [
        {"v": 3},
        {"v": 2},  # v2 needs its message (null for none)
        {"message": "v1 has no message"},
        {"totals": {"allocated_total": "90.00", "unallocated": "60.00"}},
        {"totals": {"allocated_total": "100.00", "unallocated": "40.00"}},
        {
            "allocations": [_line(amount="200.00")],
            "totals": {"allocated_total": "200.00", "unallocated": "-50.00"},
        },
        {"allocations": [_line(kind="lunch")]},
        {"note": "staff only"},
        {"payment": {"amount": 150.0}},
    ],
)
def test_a_malformed_snapshot_is_refused(overrides):
    with pytest.raises(ValidationError):
        parse_snapshot(_snapshot(**overrides))


@pytest.mark.parametrize("message", ["Gracias.\nVuelva pronto.", None])
def test_a_v2_snapshot_round_trips_with_its_message(message):
    parsed = parse_snapshot(_snapshot_v2(message))

    assert isinstance(parsed, ReceiptSnapshotV2)
    assert parsed.message == message
    dumped = parsed.model_dump(mode="json")
    assert (dumped["v"], dumped["message"]) == (2, message)
    assert parse_snapshot(dumped) == parsed


def test_a_v1_snapshot_parses_as_v1():
    parsed = parse_snapshot(_snapshot())

    assert isinstance(parsed, ReceiptSnapshotV1)
    assert parsed.v == 1
    assert not hasattr(parsed, "message")


def test_a_v2_snapshot_checks_its_totals_too():
    with pytest.raises(ValidationError):
        parse_snapshot(
            _snapshot_v2(totals={"allocated_total": "90.00", "unallocated": "60.00"})
        )


def test_the_snapshot_kinds_are_the_charge_kinds():
    field = SnapshotAllocation.model_fields["kind"].annotation
    assert set(field.__value__.__args__) == set(CHARGE_KINDS)  # type: ignore[union-attr]


def test_the_receipt_languages_are_the_ui_languages():
    assert RECEIPT_LANGUAGES == LANGUAGES


# --- Labels (E1) ---------------------------------------------------------------------


def _allocation(**overrides: Any) -> SnapshotAllocation:
    return SnapshotAllocation.model_validate(_line(**overrides))


@pytest.mark.parametrize(
    ("overrides", "es", "en"),
    [
        (
            {},
            "Sesión del 3 de marzo de 2031 (60 min)",
            "Session on March 3, 2031 (60 min)",
        ),
        (
            {"scheduled_minutes": None},
            "Sesión del 3 de marzo de 2031",
            "Session on March 3, 2031",
        ),
        ({"kind": "group_enrollment"}, "Inscripción a grupo", "Group enrollment"),
        ({"kind": "cohort_enrollment"}, "Inscripción a curso", "Course enrollment"),
        (
            {"kind": "tuition_period", "service_period_end": "2031-03-31"},
            "Colegiatura del 3 de marzo de 2031 al 31 de marzo de 2031",
            "Tuition from March 3, 2031 to March 31, 2031",
        ),
        (
            {"kind": "tuition_period"},
            "Colegiatura desde el 3 de marzo de 2031",
            "Tuition from March 3, 2031",
        ),
        (
            {"kind": "package_purchase"},
            "Compra de paquete del 3 de marzo de 2031",
            "Package purchase on March 3, 2031",
        ),
        (
            {"kind": "manual", "description": "Libros de texto"},
            "Libros de texto",
            "Libros de texto",
        ),
        ({"kind": "manual"}, "Cargo manual", "Manual charge"),
    ],
)
def test_each_kind_has_a_label_in_both_languages(overrides, es, en):
    line = _allocation(**overrides)

    assert allocation_label("es", line) == es
    assert allocation_label("en", line) == en


def test_every_charge_kind_has_a_label():
    for kind in CHARGE_KINDS:
        line = _allocation(kind=kind, description="x", service_period_end="2031-03-31")
        for lang in LANGUAGES:
            assert allocation_label(lang, line)


# --- Template versions (D4, P8) ------------------------------------------------------


def test_the_stored_version_picks_the_template():
    # New receipts are v2 since #121 (B4); v1 receipts keep their template.
    assert RECEIPT_TEMPLATE_VERSION == 2
    assert template_for(1) == RECEIPT_TEMPLATES[1] == "receipts/v1/receipt.html"
    assert template_for(2) == RECEIPT_TEMPLATES[2] == "receipts/v2/receipt.html"
    assert set(RECEIPT_TEMPLATES) == {1, 2}


@pytest.mark.parametrize(
    ("template_version", "snapshot"),
    [(1, _snapshot_v2()), (2, _snapshot())],
    ids=["v1_template_v2_snapshot", "v2_template_v1_snapshot"],
)
def test_a_template_and_snapshot_version_mismatch_is_an_error(
    template_version, snapshot
):
    stored = _stored(snapshot, template_version=template_version)

    with pytest.raises(ReceiptSnapshotMismatchError):
        receipt_html(stored)
    with pytest.raises(ReceiptSnapshotMismatchError):
        receipts.receipt_context(stored)


@pytest.mark.parametrize("version", [0, 3, 99])
def test_an_unknown_template_version_is_an_error(version):
    with pytest.raises(UnknownReceiptTemplateError):
        template_for(version)
    with pytest.raises(UnknownReceiptTemplateError):
        receipt_html(_stored(template_version=version))


def test_a_stored_receipt_in_an_unknown_language_is_refused():
    with pytest.raises(ValueError, match="unknown language"):
        receipt_html(_stored(language="fr"))


# --- Numbers (B1, B2) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "value", "number"),
    [
        (2026, 1, "R-2026-000001"),
        (2026, 123, "R-2026-000123"),
        (2031, 999999, "R-2031-999999"),
    ],
)
def test_receipt_numbers_have_six_digits(year, value, number):
    assert receipt_number(year, value) == number


@pytest.mark.parametrize("value", [0, -1, 1_000_000])
def test_a_number_out_of_range_is_refused(value):
    with pytest.raises(ValueError):
        receipt_number(2026, value)


def test_the_series_codes_and_the_limit():
    assert SERIES_CODES == ("R", "P")
    assert numbering.SIX_DIGITS_MAX == 999_999
    assert receipts.RECEIPT_SERIES == "R"


@pytest.mark.parametrize(
    ("code", "year"), [("X", 2026), ("r", 2026), ("R", 1999), ("R", 10000)]
)
def test_next_number_refuses_bad_arguments_before_any_statement(code, year):
    class NoSession:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"session.{name} used")

    with pytest.raises(ValueError):
        asyncio.run(numbering.next_number(NoSession(), code, year))  # type: ignore[arg-type]


def test_exhaustion_names_the_series():
    error = numbering.NumberSeriesExhaustedError("R", 2031)

    assert (error.code, error.year) == ("R", 2031)
    assert "R 2031" in str(error)


# --- The HTML (E1, E3) ---------------------------------------------------------------


def test_the_html_escapes_the_data_and_shows_no_staff_data():
    snapshot = _snapshot(
        payer_name="<img src=x onerror=alert(1)>",
        allocations=[_line(kind="manual", description="Libros & <útiles>")],
    )

    html = receipt_html(_stored(snapshot))

    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "Libros &amp; &lt;útiles&gt;" in html
    assert "Este recibo no es un comprobante fiscal (CFDI)." in html
    assert "R-2031-000007" in html


def test_the_html_shows_the_family_when_there_is_no_payer_and_no_credit_line():
    snapshot = _snapshot(
        payment={**_snapshot()["payment"], "amount": "100.00"},
        totals={"allocated_total": "100.00", "unallocated": "0.00"},
    )

    html = receipt_html(_stored(snapshot, language="en"))

    assert "<th>Received from</th><td>Familia Peña</td>" in html
    assert "Left as account credit" not in html
    assert "REVERSED" not in html


def test_the_html_shows_the_reversal_banner():
    html = receipt_html(_stored(reversed_on=date(2031, 4, 2)))

    assert "ANULADO el 2 de abril de 2031" in html


def test_the_snapshot_model_is_frozen():
    parsed = ReceiptSnapshotV1.model_validate(_snapshot())

    with pytest.raises(ValidationError):
        parsed.payer_name = "otro"  # type: ignore[misc]


# --- The v2 message (#121 B6) --------------------------------------------------------


def test_v2_prints_the_message_before_the_notice():
    html = receipt_html(_stored_v2("Gracias por su pago."))

    message = html.index('<p class="message">Gracias por su pago.</p>')
    assert message < html.index("Este recibo no es un comprobante fiscal (CFDI).")
    assert "white-space: pre-line" in html


def test_the_v2_context_carries_the_message():
    assert receipts.receipt_context(_stored_v2("Hola"))["message"] == "Hola"
    assert receipts.receipt_context(_stored_v2(None))["message"] is None
    assert receipts.receipt_context(_stored())["message"] is None


def test_v2_without_a_message_has_no_message_block():
    html = receipt_html(_stored_v2(None))

    assert 'class="message"' not in html
    assert "Este recibo no es un comprobante fiscal (CFDI)." in html


def test_the_message_is_data_never_template_source():
    html = receipt_html(_stored_v2("{{ 7*7 }} <b>x</b> {% if true %}y{% endif %}"))

    assert (
        '<p class="message">{{ 7*7 }} &lt;b&gt;x&lt;/b&gt;'
        " {% if true %}y{% endif %}</p>"
    ) in html
    assert "49" not in html
    assert "<b>x</b>" not in html


def test_the_message_keeps_its_line_breaks():
    html = receipt_html(_stored_v2("Línea uno\nLínea dos"))

    assert '<p class="message">Línea uno\nLínea dos</p>' in html


def test_v2_is_v1_plus_the_message():
    """Same snapshot fields, same text: v2 only adds the message block (and
    its style) to v1 (B6)."""
    v1 = receipt_html(_stored())
    v2 = receipt_html(_stored_v2(None))

    def body(html: str) -> str:
        return " ".join(html[html.index("<body") :].split())

    assert body(v1) == body(v2)


def test_v1_receipts_render_as_before_and_ignore_a_message():
    html = receipt_html(_stored())

    assert 'class="message"' not in html
    assert "white-space: pre-line" not in html
