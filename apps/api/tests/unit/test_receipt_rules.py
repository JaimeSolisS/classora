"""Receipt rules without a database or Pango (#98 J2; B1, B2, C5, D4, E1,
E3, P6, P8): the snapshot model, the label of each charge kind, the
template-version selector, number formatting and overflow, the
``next_number`` argument checks and the receipt HTML (escaping, no staff
data)."""

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
    ReceiptSnapshotV1,
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
        {"v": 2},
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
    assert RECEIPT_TEMPLATE_VERSION == 1
    assert template_for(1) == RECEIPT_TEMPLATES[1] == "receipts/v1/receipt.html"


@pytest.mark.parametrize("version", [0, 2, 99])
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
