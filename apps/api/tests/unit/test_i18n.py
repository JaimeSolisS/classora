"""Server message catalogs and formatting (#98a C2-C4; ADR-0015 E14)."""

from datetime import date

import pytest

from app.core import i18n
from app.core.i18n import format_date, format_money, message, resolve_language, t
from app.core.money import Money
from app.modules.identity.preferences import LANGUAGES as PREFERENCE_LANGUAGES


def test_the_document_languages_are_the_preference_languages():
    assert i18n.LANGUAGES == PREFERENCE_LANGUAGES
    assert i18n.DEFAULT_LANGUAGE == "es"


@pytest.mark.parametrize(
    ("lang", "expected"),
    [("es", "Página 2 de 3"), ("en", "Page 2 of 3")],
)
def test_t_substitutes_named_placeholders(lang, expected):
    assert t(lang, "common.document.page", page=2, pages=3) == expected


def test_t_returns_values_unescaped():
    # HTML escaping is Jinja2's job (D1), never the catalog's.
    assert t("en", "common.center.email") == "Email"
    assert message("es", "common.date.long") == "{day} de {month} de {year}"


@pytest.mark.parametrize("lang", ["fr", "ES", "", "es-MX"])
def test_an_unknown_language_is_a_value_error(lang):
    with pytest.raises(ValueError, match="unknown language"):
        t(lang, "common.document.page", page=1, pages=1)


@pytest.mark.parametrize(
    "key",
    [
        "common.nope",
        "common.document.nope",
        "common.document.page.deeper",
        "nope.document.page",
        "common",
        "common.",
        "../common.document.page",
        "Common.document.page",
    ],
)
def test_a_missing_key_is_a_key_error_naming_language_and_key(key):
    with pytest.raises(KeyError) as raised:
        t("en", key)
    assert key in str(raised.value)
    assert "'en'" in str(raised.value)


@pytest.mark.parametrize("key", ["common.months", "common.document", "common.date"])
def test_a_key_naming_an_object_is_a_key_error(key):
    with pytest.raises(KeyError, match="no message"):
        message("es", key)


def test_a_missing_param_is_a_key_error():
    with pytest.raises(KeyError, match="pages"):
        t("es", "common.document.page", page=1)


def test_an_extra_param_is_a_value_error():
    with pytest.raises(ValueError, match="extra"):
        t("es", "common.document.page", page=1, pages=1, extra=2)


def test_params_are_substituted_literally_without_format_semantics():
    # A value that looks like a placeholder or a format field stays as typed,
    # and is never substituted again.
    assert (
        t("en", "common.document.page", page="{pages}", pages="{0.__class__}")
        == "Page {pages} of {0.__class__}"
    )


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("es", "es"),
        ("en", "en"),
        (None, "es"),
        ("fr", "es"),
        ("", "es"),
        ("EN", "es"),
    ],
)
def test_resolve_language(stored, expected):
    assert resolve_language(stored) == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("1250.00", "$1,250.00"),
        ("0", "$0.00"),
        ("-1250", "-$1,250.00"),
        ("0.05", "$0.05"),
        ("-0.05", "-$0.05"),
        ("999.99", "$999.99"),
        ("1000", "$1,000.00"),
        ("1234567.8", "$1,234,567.80"),
        ("999999999999.99", "$999,999,999,999.99"),
    ],
)
def test_format_money(amount, expected):
    assert format_money(Money(amount)) == expected


def test_format_money_takes_money_only():
    with pytest.raises(TypeError):
        format_money(1250)  # type: ignore[arg-type]


ES_MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]
EN_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


@pytest.mark.parametrize("month", range(1, 13))
def test_format_date_names_all_twelve_months(month):
    day = date(2026, month, 5)
    assert format_date("es", day) == f"5 de {ES_MONTHS[month - 1]} de 2026"
    assert format_date("en", day) == f"{EN_MONTHS[month - 1]} 5, 2026"


def test_format_date_long_patterns():
    assert format_date("es", date(2026, 10, 28)) == "28 de octubre de 2026"
    assert format_date("en", date(2026, 10, 28)) == "October 28, 2026"


def test_format_date_refuses_an_unknown_language():
    with pytest.raises(ValueError, match="unknown language"):
        format_date("fr", date(2026, 10, 28))
