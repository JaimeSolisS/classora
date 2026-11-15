"""Reading an import CSV in memory (#122 B2, B3; E1, E2). Pure: no I/O, no
database.

- **Text**: the request carries the file as a JSON string, so it is already
  decoded. A replacement character (``U+FFFD``, what a browser puts in place
  of bytes that are not UTF-8), a lone surrogate or a NUL is ``encoding``. A
  leading BOM is dropped.
- **Delimiter**: ``;`` when the header line has more ``;`` than ``,``,
  else ``,`` (Excel's "CSV UTF-8" uses ``;`` in Spanish locales). Quotes
  follow RFC 4180; the ``csv`` module runs in strict mode, so an unbalanced
  quote is ``malformed`` with the line where reading stopped.
- **Header**: names are matched trimmed and case-insensitive. A blank name is
  skipped; a repeated one is ``duplicate_column``; more than
  :data:`~app.modules.administration.imports.registry.MAX_COLUMNS` columns is
  ``too_many_columns``; names the kind doesn't read are listed as ignored;
  required ones that are absent are ``missing_columns``.
- **Rows**: blank lines (and lines whose cells are all blank) are skipped. A
  row's number is the file line it starts on (the header is usually line
  1). Cells are trimmed; a missing trailing cell is ``""``; extra cells are
  ignored. A cell over :data:`MAX_CELL_LENGTH` characters is kept cut to that
  length and flagged, so the pipeline reports ``too_long``. More than
  :data:`~app.modules.administration.imports.errors.MAX_DATA_ROWS` data rows
  is ``import_too_many_rows``.

Nothing here puts a cell value into an error.
"""

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.modules.administration.imports.errors import (
    MAX_DATA_ROWS,
    ImportFileInvalidError,
    ImportTooManyRowsError,
)
from app.modules.administration.imports.registry import MAX_COLUMNS, ImportColumn

MAX_CELL_LENGTH: Final = 1000
"""The longest cell, in characters after trimming (#122 B2)."""

_BOM: Final = "﻿"
_REPLACEMENT: Final = "�"


@dataclass(frozen=True)
class ParsedRow:
    """One data row: its file line, the kind's columns present in the file
    (trimmed text) and the columns whose cell was too long."""

    row_number: int
    values: dict[str, str]
    too_long: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedFile:
    columns: tuple[str, ...]
    """The kind's columns present in the header, in the kind's order."""
    ignored_columns: tuple[str, ...]
    """Header names the kind doesn't read (normalized), in file order."""
    rows: tuple[ParsedRow, ...]


def _check_text(text: str) -> None:
    if _REPLACEMENT in text or "\x00" in text:
        raise ImportFileInvalidError("encoding")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ImportFileInvalidError("encoding") from None


def detect_delimiter(header_line: str) -> str:
    """``;`` when the header has more semicolons than commas, else ``,``."""
    return ";" if header_line.count(";") > header_line.count(",") else ","


def _first_line(text: str) -> str | None:
    for line in io.StringIO(text, newline=None):
        if line.strip():
            return line
    return None


def _blank(cells: Sequence[str]) -> bool:
    return all(not cell.strip() for cell in cells)


def parse_csv(text: str, columns: Sequence[ImportColumn]) -> ParsedFile:
    """Read ``text`` for a kind with ``columns``; raise
    :class:`ImportFileInvalidError` or :class:`ImportTooManyRowsError`."""
    _check_text(text)
    if text.startswith(_BOM):
        text = text[len(_BOM) :]
    header_line = _first_line(text)
    if header_line is None:
        raise ImportFileInvalidError("empty")
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=detect_delimiter(header_line),
        strict=True,
    )
    known = {column.name for column in columns}
    try:
        header: list[str] | None = None
        consumed = 0
        positions: dict[str, int] = {}
        ignored: list[str] = []
        rows: list[ParsedRow] = []
        for cells in reader:
            start = consumed + 1
            consumed = reader.line_num
            if not cells or _blank(cells):
                continue
            if header is None:
                header = cells
                if len(cells) > MAX_COLUMNS:
                    raise ImportFileInvalidError(
                        "too_many_columns", maximum=MAX_COLUMNS
                    )
                header_names = [cell.strip().lower() for cell in cells]
                named = [name for name in header_names if name]
                if len(set(named)) != len(named):
                    raise ImportFileInvalidError("duplicate_column")
                missing = [
                    column.name
                    for column in columns
                    if column.required and column.name not in named
                ]
                if missing:
                    raise ImportFileInvalidError("missing_columns", columns=missing)
                for index, name in enumerate(header_names):
                    if name in known:
                        positions[name] = index
                    elif name:
                        ignored.append(name)
                continue
            if len(rows) == MAX_DATA_ROWS:
                raise ImportTooManyRowsError()
            values: dict[str, str] = {}
            too_long: list[str] = []
            for column in columns:
                position = positions.get(column.name)
                if position is None:
                    continue
                cell = cells[position].strip() if position < len(cells) else ""
                if len(cell) > MAX_CELL_LENGTH:
                    too_long.append(column.name)
                    cell = cell[:MAX_CELL_LENGTH]
                values[column.name] = cell
            rows.append(ParsedRow(start, values, tuple(too_long)))
    except csv.Error:
        raise ImportFileInvalidError("malformed", line=reader.line_num) from None
    if not rows:
        raise ImportFileInvalidError("empty")
    present = tuple(column.name for column in columns if column.name in positions)
    return ParsedFile(columns=present, ignored_columns=tuple(ignored), rows=tuple(rows))
