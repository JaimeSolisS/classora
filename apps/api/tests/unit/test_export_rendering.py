"""Rendering an export: CSV, XLSX, formula injection, grouping, labels and
the file name (#124 E1-E7, E10)."""

import csv
import io
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, get_args
from zoneinfo import ZoneInfo

import openpyxl
import pytest

from app.core.i18n import LANGUAGES, message
from app.core.money import Money
from app.modules.finance.schemas import ExpenseStatus
from app.modules.reporting.exports import render
from app.modules.reporting.exports.datasets import (
    BUILT_IN_DATASETS,
    CLASS_SESSIONS_DATASET,
    EXPENSES_DATASET,
    STUDENTS_DATASET,
)
from app.modules.scheduling.schemas import SessionKindCode, SessionStateCode

TRIGGERS = ["=", "+", "-", "@", "\t", "\r"]


def m(value: str) -> Money:
    return Money(Decimal(value))


def expense(**change: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "incurred_on": date(2026, 3, 4),
        "due_on": date(2026, 3, 31),
        "category": "Renta",
        "vendor": "Inmobiliaria Sur",
        "description": "Renta de marzo",
        "amount": m("-150.00"),
        "adjusted_amount": m("1250.50"),
        "paid_amount": m("0.00"),
        "open_amount": m("1250.50"),
        "status": "partially_paid",
        "receipt_reference": None,
        "template": None,
    }
    row.update(change)
    return row


def session(**change: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": date(2026, 1, 5),
        "start_time": time(16, 0),
        "end_time": time(17, 30),
        "duration_minutes": 90,
        "kind": "group",
        "state": "confirmed",
        "subject": "Álgebra",
        "education_level": "Secundaria",
        "tutor": "Ana Tutor",
        "classroom": None,
        "students": ["Luis Pérez", "Eva Ruiz"],
        "series": True,
    }
    row.update(change)
    return row


def csv_rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))


def sheet(data: bytes) -> Any:
    return openpyxl.load_workbook(io.BytesIO(data)).active


ALL_EXPENSE_COLUMNS = list(EXPENSES_DATASET.parameters_model.COLUMN_CODES)
ALL_SESSION_COLUMNS = list(CLASS_SESSIONS_DATASET.parameters_model.COLUMN_CODES)


# --- E2: CSV --------------------------------------------------------------------------


def test_csv_is_utf8_with_a_bom_comma_and_crlf():
    data = render.render_csv(
        "es", EXPENSES_DATASET, ["description", "amount"], [expense()]
    )

    assert data.startswith(b"\xef\xbb\xbf")
    assert data.decode("utf-8-sig") == "Descripción,Monto\r\nRenta de marzo,-150.00\r\n"


def test_csv_values_follow_their_kinds():
    data = render.render_csv("en", EXPENSES_DATASET, ALL_EXPENSE_COLUMNS, [expense()])

    header, row = csv_rows(data)
    assert header == [
        "Expense date",
        "Due date",
        "Category",
        "Vendor",
        "Description",
        "Amount",
        "Net amount",
        "Paid",
        "Open",
        "Status",
        "Receipt reference",
        "Recurring expense",
    ]
    assert row == [
        "2026-03-04",
        "2026-03-31",
        "Renta",
        "Inmobiliaria Sur",
        "Renta de marzo",
        "-150.00",
        "1250.50",
        "0.00",
        "1250.50",
        "Partly paid",
        "",
        "",
    ]


def test_csv_sessions_write_times_lists_booleans_and_enums():
    data = render.render_csv(
        "es", CLASS_SESSIONS_DATASET, ALL_SESSION_COLUMNS, [session()]
    )

    _, row = csv_rows(data)
    assert row == [
        "2026-01-05",
        "16:00",
        "17:30",
        "90",
        "Grupal",
        "Confirmada",
        "Álgebra",
        "Secundaria",
        "Ana Tutor",
        "",
        "Luis Pérez; Eva Ruiz",
        "Sí",
    ]


def test_csv_quotes_only_as_needed():
    data = render.render_csv(
        "es",
        EXPENSES_DATASET,
        ["description", "vendor"],
        [expense(description='Pago "anual", marzo', vendor="Línea\nnueva")],
    )

    text = data.decode("utf-8-sig")
    assert text.endswith('"Pago ""anual"", marzo","Línea\nnueva"\r\n')
    assert csv_rows(data)[1] == ['Pago "anual", marzo', "Línea\nnueva"]


def test_an_export_with_no_rows_is_the_header_only():
    data = render.render_csv("es", STUDENTS_DATASET, ["given_names"], [])

    assert data.decode("utf-8-sig") == "Nombres\r\n"
    assert [
        row[0].value
        for row in sheet(
            render.render_xlsx("es", STUDENTS_DATASET, ["given_names"], [])
        ).iter_rows()
    ] == ["Nombres"]


# --- E3: XLSX -------------------------------------------------------------------------


def test_xlsx_cells_are_typed_and_formatted():
    ws = sheet(
        render.render_xlsx("es", EXPENSES_DATASET, ALL_EXPENSE_COLUMNS, [expense()])
    )

    header, row = list(ws.iter_rows())
    assert [c.value for c in header][:2] == ["Fecha del gasto", "Fecha de vencimiento"]
    assert all(c.data_type == "s" for c in header)
    by_code = dict(zip(ALL_EXPENSE_COLUMNS, row, strict=True))
    assert by_code["incurred_on"].value == datetime(2026, 3, 4)
    assert by_code["incurred_on"].number_format == "yyyy-mm-dd"
    assert by_code["amount"].value == -150
    assert by_code["amount"].data_type == "n"
    assert by_code["amount"].number_format == "#,##0.00"
    assert by_code["adjusted_amount"].value == pytest.approx(1250.5)
    assert by_code["status"].value == "Pago parcial"
    assert by_code["status"].data_type == "s"
    assert by_code["receipt_reference"].value is None


def test_xlsx_has_one_sheet_named_by_the_dataset_with_a_frozen_header():
    workbook = openpyxl.load_workbook(
        io.BytesIO(render.render_xlsx("en", CLASS_SESSIONS_DATASET, ["date"], []))
    )

    assert workbook.sheetnames == ["Class sessions"]
    assert workbook.active.freeze_panes == "A2"


def test_xlsx_sessions_integers_are_numbers_and_the_rest_strings():
    ws = sheet(
        render.render_xlsx(
            "en", CLASS_SESSIONS_DATASET, ALL_SESSION_COLUMNS, [session()]
        )
    )

    row = dict(zip(ALL_SESSION_COLUMNS, list(ws.iter_rows())[1], strict=True))
    assert row["duration_minutes"].value == 90
    assert row["duration_minutes"].data_type == "n"
    for code in ("start_time", "kind", "state", "students", "series", "tutor"):
        assert row[code].data_type == "s", code
    assert row["start_time"].value == "16:00"
    assert row["students"].value == "Luis Pérez; Eva Ruiz"
    assert row["series"].value == "Yes"


def test_xlsx_is_rendered_without_a_temporary_file(monkeypatch):
    import openpyxl.worksheet._writer as writer

    def refuse(*_: Any, **__: Any) -> str:
        raise AssertionError("a temporary file was created")

    monkeypatch.setattr(writer, "create_temporary_file", refuse)

    data = render.render_xlsx("es", EXPENSES_DATASET, ["description"], [expense()])

    assert sheet(data).cell(2, 1).value == "Renta de marzo"


def test_xlsx_drops_characters_a_worksheet_cannot_hold():
    ws = sheet(
        render.render_xlsx(
            "es",
            EXPENSES_DATASET,
            ["description"],
            [expense(description="a\x01b\x0bc")],
        )
    )

    assert ws.cell(2, 1).value == "abc"


@pytest.mark.parametrize(
    ("label", "title"),
    [
        ("Gastos", "Gastos"),
        ("A/B: [C]*?\\'", "AB C"),
        ("x" * 40, "x" * 31),
        ("[]", "Export"),
    ],
)
def test_the_sheet_title_is_safe(label, title):
    assert render.sheet_title(label) == title


# --- E4: formula injection ------------------------------------------------------------


@pytest.mark.parametrize("trigger", TRIGGERS)
def test_a_text_cell_starting_with_a_trigger_is_prefixed_in_csv(trigger):
    value = f"{trigger}SUM(A1)"
    data = render.render_csv(
        "es",
        EXPENSES_DATASET,
        ["description", "vendor", "amount"],
        [expense(description=value, vendor=value)],
    )

    _, row = csv_rows(data)
    assert row == [f"'{value}", f"'{value}", "-150.00"]


@pytest.mark.parametrize("trigger", TRIGGERS)
def test_a_text_cell_starting_with_a_trigger_is_prefixed_in_xlsx(trigger):
    value = f"{trigger}SUM(A1)"
    ws = sheet(
        render.render_xlsx(
            "es",
            EXPENSES_DATASET,
            ["description", "amount"],
            [expense(description=value)],
        )
    )

    cells = [c for row in ws.iter_rows() for c in row]
    # XML parsers read a raw CR back as LF; the prefix is what matters.
    assert ws.cell(2, 1).value == f"'{value}".replace("\r", "\n")
    assert ws.cell(2, 2).value == -150
    assert not [c for c in cells if c.data_type == "f"]


@pytest.mark.parametrize("trigger", TRIGGERS)
def test_list_cells_are_escaped_too(trigger):
    row = session(students=[f"{trigger}x", "Eva"], tutor=f"{trigger}T")
    data = render.render_csv("es", CLASS_SESSIONS_DATASET, ["students", "tutor"], [row])

    assert csv_rows(data)[1] == [f"'{trigger}x; Eva", f"'{trigger}T"]


def test_a_hyperlink_formula_is_never_a_formula():
    value = '=HYPERLINK("http://x")'
    ws = sheet(
        render.render_xlsx(
            "es", EXPENSES_DATASET, ["description"], [expense(description=value)]
        )
    )

    assert ws.cell(2, 1).value == f"'{value}"
    assert ws.cell(2, 1).data_type == "s"


def test_a_text_that_merely_contains_a_trigger_is_unchanged():
    assert render.escape_formula("Renta = 1000") == "Renta = 1000"
    assert render.escape_formula("") == ""


# --- E5: grouping ---------------------------------------------------------------------


def test_grouping_is_a_stable_sort_with_nulls_last():
    rows = [
        expense(description="1", category="beta"),
        expense(description="2", category=None),
        expense(description="3", category="Alfa"),
        expense(description="4", category="beta"),
        expense(description="5", category="alfa"),
    ]

    ordered = render.grouped(rows, EXPENSES_DATASET, "category", "es")

    assert [r["description"] for r in ordered] == ["3", "5", "1", "4", "2"]


def test_grouping_by_dates_is_chronological():
    rows = [
        expense(description="1", due_on=date(2026, 12, 1)),
        expense(description="2", due_on=date(2026, 2, 1)),
        expense(description="3", due_on=None),
    ]

    ordered = render.grouped(rows, EXPENSES_DATASET, "due_on", "es")

    assert [r["description"] for r in ordered] == ["2", "1", "3"]


def test_grouping_by_an_enum_sorts_by_its_label():
    rows = [
        session(state="unassigned"),
        session(state="cancelled"),
        session(state="offered"),
    ]

    ordered = render.grouped(rows, CLASS_SESSIONS_DATASET, "state", "es")

    assert [r["state"] for r in ordered] == ["cancelled", "offered", "unassigned"]


def test_no_grouping_keeps_the_page_order():
    rows = [expense(description="b"), expense(description="a")]

    assert render.grouped(rows, EXPENSES_DATASET, None, "es") == rows


# --- E6: the file name ----------------------------------------------------------------


def test_the_file_name_names_the_dataset_and_the_local_request_time():
    at = datetime(2026, 11, 5, 3, 7, tzinfo=ZoneInfo("UTC")).astimezone(
        ZoneInfo("America/Mexico_City")
    )

    assert render.file_name("class_sessions", "xlsx", at) == (
        "classora-class_sessions-20261104-2107.xlsx"
    )


# --- E1, E10: the catalog -------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_column_dataset_and_value_has_a_label(language):
    for dataset in BUILT_IN_DATASETS:
        assert message(language, f"exports.datasets.{dataset.code}")
        for column in dataset.columns:
            assert render.header_label(language, dataset, column.code)
    values = {
        "session_kind": get_args(SessionKindCode.__value__),
        "session_state": get_args(SessionStateCode.__value__),
        "expense_status": get_args(ExpenseStatus.__value__),
        "boolean": ("true", "false"),
    }
    for group, codes in values.items():
        for code in codes:
            assert message(language, f"exports.values.{group}.{code}")


def test_every_enum_column_names_a_value_group():
    groups = {
        c.values for d in BUILT_IN_DATASETS for c in d.columns if c.kind == "enum"
    }

    assert groups == {"session_kind", "session_state", "expense_status"}
