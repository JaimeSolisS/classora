"""The import CSV reader (#122 B2, B3; ``administration/imports/parsing.py``)."""

import pytest

from app.modules.administration.imports.errors import (
    ImportFileInvalidError,
    ImportTooManyRowsError,
)
from app.modules.administration.imports.parsing import (
    MAX_CELL_LENGTH,
    detect_delimiter,
    parse_csv,
)
from app.modules.administration.imports.registry import ImportColumn

COLUMNS = (
    ImportColumn("name", required=True),
    ImportColumn("email"),
    ImportColumn("phone"),
)


def reason(exc: pytest.ExceptionInfo[ImportFileInvalidError]) -> object:
    assert exc.value.details is not None
    return exc.value.details["reason"]


def test_semicolons_bom_crlf_and_header_case():
    text = "﻿Name ; EMAIL;Extra\r\nAna;ana@example.com;x\r\n\r\nLuis;;\r\n"

    parsed = parse_csv(text, COLUMNS)

    assert parsed.columns == ("name", "email")
    assert parsed.ignored_columns == ("extra",)
    assert [(row.row_number, row.values) for row in parsed.rows] == [
        (2, {"name": "Ana", "email": "ana@example.com"}),
        (4, {"name": "Luis", "email": ""}),
    ]


def test_commas_quotes_and_multiline_cells_keep_file_line_numbers():
    text = 'name,phone\n"Pérez, Ana","55\n12"\nLuis,5512345678\n'

    parsed = parse_csv(text, COLUMNS)

    assert [(row.row_number, row.values) for row in parsed.rows] == [
        (2, {"name": "Pérez, Ana", "phone": "55\n12"}),
        (4, {"name": "Luis", "phone": "5512345678"}),
    ]


def test_delimiter_detection():
    assert detect_delimiter("a;b;c,d") == ";"
    assert detect_delimiter("a,b;c") == ","
    assert detect_delimiter("a") == ","


def test_blank_lines_and_all_blank_rows_are_skipped_and_cells_trimmed():
    parsed = parse_csv("name;email\n ;  \n  Ana  ;\n\n", COLUMNS)

    assert [(row.row_number, row.values) for row in parsed.rows] == [
        (3, {"name": "Ana", "email": ""})
    ]


def test_missing_cells_are_blank_and_extra_cells_ignored():
    parsed = parse_csv("name,email\nAna\nLuis,l@e.mx,extra,more\n", COLUMNS)

    assert [row.values for row in parsed.rows] == [
        {"name": "Ana", "email": ""},
        {"name": "Luis", "email": "l@e.mx"},
    ]


def test_a_long_cell_is_cut_and_flagged():
    text = f"name,email\n{'a' * (MAX_CELL_LENGTH + 1)},x@y.mx\n"

    [row] = parse_csv(text, COLUMNS).rows

    assert row.too_long == ("name",)
    assert len(row.values["name"]) == MAX_CELL_LENGTH


@pytest.mark.parametrize(
    "text",
    ["caf�,x\nA,b\n", "name\nA\x00\n", "name\n\ud800\n"],
    ids=["replacement", "nul", "surrogate"],
)
def test_text_that_was_not_utf8_is_encoding(text):
    with pytest.raises(ImportFileInvalidError) as caught:
        parse_csv(text, COLUMNS)

    assert reason(caught) == "encoding"


@pytest.mark.parametrize("text", ["", "\n \n", "name;email\n", "﻿name\n\n"])
def test_no_header_or_no_data_row_is_empty(text):
    with pytest.raises(ImportFileInvalidError) as caught:
        parse_csv(text, COLUMNS)

    assert reason(caught) == "empty"


def test_an_unbalanced_quote_is_malformed_with_the_line():
    with pytest.raises(ImportFileInvalidError) as caught:
        parse_csv('name,email\nAna,a@b.mx\n"Luis,l@b.mx\nPaco,p@b.mx\n', COLUMNS)

    assert caught.value.details == {"reason": "malformed", "line": 4}


def test_duplicate_and_missing_columns():
    with pytest.raises(ImportFileInvalidError) as duplicate:
        parse_csv("name,Name\nA,B\n", COLUMNS)
    with pytest.raises(ImportFileInvalidError) as missing:
        parse_csv("email,phone\na@b.mx,5512345678\n", COLUMNS)

    assert reason(duplicate) == "duplicate_column"
    assert missing.value.details == {"reason": "missing_columns", "columns": ["name"]}


def test_more_than_30_columns():
    header = ",".join(f"c{i}" for i in range(31))

    with pytest.raises(ImportFileInvalidError) as caught:
        parse_csv(f"name,{header}\nA\n", COLUMNS)

    assert caught.value.details == {"reason": "too_many_columns", "max": 30}


def test_at_most_1000_data_rows():
    ok = parse_csv("name\n" + "A\n" * 1000, COLUMNS)

    with pytest.raises(ImportTooManyRowsError) as caught:
        parse_csv("name\n" + "A\n" * 1001, COLUMNS)

    assert len(ok.rows) == 1000
    assert caught.value.details == {"max": 1000}


def test_errors_never_carry_a_cell_value():
    secret = "Secretísimo"
    for text in (
        f'name\n"{secret}\n',
        f"{secret},{secret}\nA,B\n",
        f"email\n{secret}\n",
    ):
        with pytest.raises(ImportFileInvalidError) as caught:
            parse_csv(text, COLUMNS)
        assert secret.lower() not in repr(caught.value.details).lower()
        assert secret.lower() not in str(caught.value.detail).lower()
