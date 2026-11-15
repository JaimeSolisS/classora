"""Payslip rules without a database or Pango (#113 B5, D1, D2, E3): the
snapshot validators, the summary, number formatting and range, the rate and
percent formatters, the template-version selector and the payslip HTML (both
languages, escaping, no payments or student data)."""

import re
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.money import Money, Rate
from app.modules.payroll import payslips
from app.modules.payroll.models import PAYSLIP_LANGUAGES, PAYSLIP_NUMBER_PATTERN
from app.modules.payroll.payslips import (
    PAYSLIP_SERIES,
    PAYSLIP_TEMPLATE_VERSION,
    PAYSLIP_TEMPLATES,
    PayslipLine,
    UnknownPayslipTemplateError,
    format_percent,
    format_rate,
    parse_snapshot,
    payslip_html,
    payslip_number,
    summarize,
    template_for,
)
from tests.factories import payslips as factory


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


# --- The snapshot (B5) ---------------------------------------------------------------


def test_a_valid_snapshot_parses_and_round_trips():
    data = factory.snapshot()

    parsed = parse_snapshot(data)

    assert parsed.total == Money("1250.00")
    assert parsed.model_dump(mode="json") == data


def test_the_summary_counts_class_lines_and_splits_items_by_sign():
    lines = [PayslipLine.model_validate(line) for line in factory.every_kind()]

    summary = summarize(lines)

    assert summary.class_line_count == 4
    assert summary.class_minutes == 600 + 60 + 60 + 60
    assert summary.classes == Money("1150.00")
    assert summary.salary == Money("150.00")
    assert summary.bonuses == Money("200.00")
    assert summary.deductions == Money("-300.00")
    assert summary.adjustments == Money("50.00")
    assert summary.subtotal() == Money("1250.00")


def test_a_total_that_isnt_the_sum_of_the_lines_is_refused():
    data = factory.snapshot(total="1250.01")

    with pytest.raises(ValidationError, match="sum of the lines"):
        parse_snapshot(data)


def test_subtotals_that_dont_add_up_are_refused():
    data = factory.snapshot()
    data["summary"] = {**data["summary"], "bonuses": "200.01"}

    with pytest.raises(ValidationError, match="subtotals"):
        parse_snapshot(data)


def test_a_summary_that_doesnt_describe_the_lines_is_refused():
    data = factory.snapshot()
    # The same total, moved from salary to classes.
    data["summary"] = {**data["summary"], "classes": "1300.00", "salary": "0.00"}

    with pytest.raises(ValidationError, match="summary"):
        parse_snapshot(data)


@pytest.mark.parametrize(
    "change",
    [
        {"v": 2},
        {"v": "1"},
        {"payments": []},
        {"student_name": "Ana"},
    ],
)
def test_an_unknown_version_or_field_is_refused(change):
    with pytest.raises(ValidationError):
        parse_snapshot(factory.snapshot(**change))


def test_a_line_has_no_ids_and_no_student_data():
    assert set(PayslipLine.model_fields) == {
        "kind",
        "amount",
        "service_date",
        "class_kind",
        "minutes",
        "rate_model",
        "rate",
        "covered_by_salary",
        "cumulative_sessions",
        "cohort_sessions",
        "pay_rule",
        "salary",
        "pay_item",
        "adjustment",
    }
    with pytest.raises(ValidationError):
        PayslipLine.model_validate({**factory.line(), "class_session_id": "x"})


def test_the_snapshot_is_frozen():
    parsed = parse_snapshot(factory.snapshot())

    with pytest.raises(ValidationError):
        parsed.total = Money("1.00")  # type: ignore[misc]


# --- Numbers --------------------------------------------------------------------------


def test_payslip_numbers_are_p_year_and_six_digits():
    assert PAYSLIP_SERIES == "P"
    assert payslip_number(2031, 1) == "P-2031-000001"
    assert payslip_number(2031, 999_999) == "P-2031-999999"
    assert re.fullmatch(PAYSLIP_NUMBER_PATTERN, payslip_number(2026, 123))


@pytest.mark.parametrize("value", [0, -1, 1_000_000])
def test_a_number_out_of_range_is_refused(value):
    with pytest.raises(ValueError, match="out of range"):
        payslip_number(2031, value)


# --- Formatters (D1) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "text"),
    [
        ("120", "$120.00"),
        ("120.1", "$120.10"),
        ("120.125", "$120.125"),
        ("1000.1234", "$1,000.1234"),
        ("0", "$0.00"),
        ("0.0001", "$0.0001"),
        ("1234567.5", "$1,234,567.50"),
    ],
)
def test_a_rate_shows_two_to_four_decimals(rate, text):
    assert format_rate(Rate(Decimal(rate))) == text


@pytest.mark.parametrize(
    ("percent", "text"),
    [("50", "50.00%"), ("12.5", "12.50%"), ("33.3333", "33.3333%")],
)
def test_a_percent_shows_two_to_four_decimals(percent, text):
    assert format_percent(Rate(Decimal(percent))) == text


# --- Templates (D2) ------------------------------------------------------------------


def test_the_current_version_has_a_template():
    assert template_for(PAYSLIP_TEMPLATE_VERSION) == PAYSLIP_TEMPLATES[1]
    assert PAYSLIP_TEMPLATES[1] == "payslips/v1/payslip.html"


def test_an_unknown_template_version_is_refused():
    with pytest.raises(UnknownPayslipTemplateError):
        template_for(2)
    with pytest.raises(UnknownPayslipTemplateError):
        payslip_html(factory.stored(template_version=2))


def test_the_languages_are_the_document_languages():
    assert PAYSLIP_LANGUAGES == ("es", "en")


EXPECTED: dict[str, list[str]] = {
    "es": [
        "Comprobante de pago",
        "P-2031-000123",
        "10 de marzo de 2031",
        "Del 1 de marzo de 2031 al 7 de marzo de 2031",
        "Semanal",
        "Tutora Núñez Peña",
        "Cubierta por salario",
        "$100.00 por hora",
        "$1,000.125 por curso, sesión 3 / 10",
        "Cancelación tardía (50.00% de la tarifa)",
        "7 / 28 de $600.00, contado en días naturales",
        "Bono de puntualidad",
        "Marzo sin retardos",
        "-$300.00",
        "Ajuste manual",
        "Trabajo del 20 de febrero de 2031 (Grupo)",
        "Motivo: Pago acordado con la tutora",
        "Total del periodo",
        "$1,250.00",
        "Este documento no es un comprobante fiscal (no es un CFDI de nómina).",
    ],
    "en": [
        "Payslip",
        "P-2031-000123",
        "March 10, 2031",
        "March 1, 2031 to March 7, 2031",
        "Weekly",
        "Tutora Núñez Peña",
        "Covered by salary",
        "$100.00 per hour",
        "$1,000.125 per course, session 3 / 10",
        "Late cancellation (50.00% of the rate)",
        "7 / 28 of $600.00, counted in calendar days",
        "Bono de puntualidad",
        "-$300.00",
        "Manual adjustment",
        "Work on February 20, 2031 (Group)",
        "Reason: Pago acordado con la tutora",
        "Period total",
        "$1,250.00",
        "This document is not a tax document (it is not a CFDI de nómina).",
    ],
}


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_payslip_html_in_each_language(lang):
    text = text_of(payslip_html(factory.stored(lang)))

    for expected in EXPECTED[lang]:
        assert expected in text, expected
    unexpected = {
        "es": ["Payslip", "Weekly", "Covered by salary", "Period total"],
        "en": ["Comprobante de pago", "Semanal", "Cubierta por salario", "Motivo"],
    }
    for absent in unexpected[lang]:
        assert absent not in text, absent


def test_sections_without_lines_are_left_out():
    only_classes = [factory.line()]
    data = factory.snapshot(only_classes)

    text = text_of(payslip_html(factory.stored("es", data)))

    assert "Clases" in text
    for heading in (
        "Bonos Tipo",
        "Deducciones Tipo",
        "Ajustes Tipo",
        "Salario Periodo",
    ):
        assert heading not in text, heading


def test_markup_in_text_is_escaped():
    hostile = "<b>x</b> & <script>alert(1)</script>"
    lines: list[dict[str, Any]] = [
        factory.line(
            "pay_item",
            amount="10.00",
            pay_item={
                "type_name": hostile,
                "direction": "bonus",
                "description": hostile,
            },
        ),
        factory.line(
            "adjustment",
            amount="5.00",
            adjustment={
                "kind": "manual",
                "reason": hostile,
                "service_date": None,
                "class_kind": None,
                "origin_period": None,
                "paired_period": None,
            },
        ),
    ]
    data = factory.snapshot(lines)
    data["tutor"]["display_name"] = hostile

    html = payslip_html(factory.stored("es", data))

    assert "<script>" not in html
    assert "<b>x</b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&amp;" in html


def test_the_payslip_names_no_payment_and_no_student():
    html = payslip_html(factory.stored("es"))

    for absent in ("Pagado", "Referencia", "Estudiante", "SPEI"):
        assert absent not in html, absent


def test_the_module_states_the_template_version_rule():
    doc = payslips.__doc__ or ""
    assert "v(N+1)" in doc
    assert "only styling fixes may edit" in doc
