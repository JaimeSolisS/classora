"""Rendering an export (#124 E1-E7, E10): CSV and XLSX, in memory.

- The header row is the chosen columns' labels in the job's language
  (``exports.columns.<dataset>.<column>``); ``enum`` and ``boolean`` values
  are labels too (``exports.values.*``).
- ``date`` is ``YYYY-MM-DD``, ``time`` ``HH:MM`` (center-local; the dataset
  already converted it), ``money`` a plain decimal with two places, never a
  float, ``integer`` plain, ``list`` joined with ``"; "``, null empty.
- **Formula injection** (PM-NOTES #122/#123 MUST): every written text cell
  (``text``, ``enum``, ``boolean``, ``list``, ``time``) that starts with
  ``=``, ``+``, ``-``, ``@``, a tab or a CR gets a ``'`` prefix, in both
  formats. Numbers, dates and money are never prefixed, so ``-150.00`` stays
  a number.
- **CSV**: UTF-8 with a BOM, ``,``, ``QUOTE_MINIMAL``, CRLF.
- **XLSX**: ``openpyxl`` in write-only mode, one sheet named with the
  dataset's label, the header row frozen; dates are real dates
  (``yyyy-mm-dd``), money and integers numbers (money ``#,##0.00`` from
  ``Decimal``), every other cell an explicit string cell. openpyxl writes a
  worksheet into a temporary file by default; here it writes into memory, so
  the bytes never touch a disk (#124 F4).
- **Grouping** (E7): a stable sort by the ``group_by`` value, nulls last; no
  row is added.

Pure functions: nothing here reads the database or logs a value.
"""

import csv
import io
import re
import zipfile
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Final, cast

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
from openpyxl.worksheet._write_only import WriteOnlyWorksheet
from openpyxl.worksheet._writer import WorksheetWriter
from openpyxl.writer.excel import ExcelWriter

from app.core.i18n import message
from app.core.money import Money
from app.modules.reporting.exports.registry import ExportColumn, ExportDataset

FORMULA_TRIGGERS: Final = ("=", "+", "-", "@", "\t", "\r")
"""A text cell starting with one of these is prefixed with ``'``."""
LIST_SEPARATOR: Final = "; "
CSV_MEDIA_TYPE: Final = "text/csv; charset=utf-8"
XLSX_MEDIA_TYPE: Final = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
DATE_FORMAT: Final = "yyyy-mm-dd"
MONEY_FORMAT: Final = "#,##0.00"
SHEET_TITLE_MAX: Final = 31
_SHEET_UNSAFE: Final = re.compile(r"[\[\]:*?/\\']")


def escape_formula(text: str) -> str:
    """``text`` with a ``'`` prefix when a spreadsheet would read it as a
    formula."""
    return f"'{text}" if text.startswith(FORMULA_TRIGGERS) else text


def header_label(language: str, dataset: ExportDataset, column: str) -> str:
    return message(language, f"exports.columns.{dataset.code}.{column}")


def dataset_label(language: str, code: str) -> str:
    return message(language, f"exports.datasets.{code}")


def _boolean(language: str, value: bool) -> str:
    return message(language, f"exports.values.boolean.{'true' if value else 'false'}")


def _enum(language: str, column: ExportColumn, value: str) -> str:
    return message(language, f"exports.values.{column.values}.{value}")


def _money(value: Money | Decimal) -> Decimal:
    return value.amount if isinstance(value, Money) else value


def text_of(language: str, column: ExportColumn, value: Any) -> str | None:
    """The written text of a non-numeric, non-date value (unescaped), or
    ``None`` for null."""
    if value is None:
        return None
    match column.kind:
        case "boolean":
            return _boolean(language, bool(value))
        case "enum":
            return _enum(language, column, str(value))
        case "list":
            return LIST_SEPARATOR.join(str(item) for item in value)
        case "time" if isinstance(value, time):
            return value.strftime("%H:%M")
        case _:
            return str(value)


def csv_cell(language: str, column: ExportColumn, value: Any) -> str:
    """One CSV field (#124 E1, E4)."""
    if value is None:
        return ""
    match column.kind:
        case "date" if isinstance(value, date):
            return value.isoformat()
        case "money":
            return f"{_money(value):.2f}"
        case "integer":
            return str(int(value))
    text = text_of(language, column, value)
    return "" if text is None else escape_formula(text)


def _sort_key(
    language: str, column: ExportColumn
) -> Callable[[dict[str, Any]], tuple[bool, Any]]:
    def key(row: dict[str, Any]) -> tuple[bool, Any]:
        value = row.get(column.code)
        if value is None:
            return (True, 0)
        match column.kind:
            case "date" | "time" | "integer":
                return (False, value)
            case "money":
                return (False, _money(value))
        return (False, (text_of(language, column, value) or "").casefold())

    return key


def grouped(
    rows: Sequence[dict[str, Any]],
    dataset: ExportDataset,
    group_by: str | None,
    language: str,
) -> list[dict[str, Any]]:
    """``rows`` stably sorted by ``group_by`` (nulls last); no row added."""
    if group_by is None:
        return list(rows)
    return sorted(rows, key=_sort_key(language, dataset.column(group_by)))


def render_csv(
    language: str,
    dataset: ExportDataset,
    columns: Sequence[str],
    rows: Iterable[dict[str, Any]],
) -> bytes:
    """UTF-8 with a BOM, comma-separated, ``QUOTE_MINIMAL``, CRLF."""
    chosen = [dataset.column(code) for code in columns]
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(
        escape_formula(header_label(language, dataset, c.code)) for c in chosen
    )
    for row in rows:
        writer.writerow(csv_cell(language, c, row.get(c.code)) for c in chosen)
    return out.getvalue().encode("utf-8-sig")


# --- XLSX -----------------------------------------------------------------------------


class _MemoryWorksheetWriter(WorksheetWriter):
    """openpyxl's worksheet writer on a ``BytesIO`` instead of a temporary
    file."""

    def __init__(self, ws: WriteOnlyWorksheet) -> None:
        super().__init__(ws, out=io.BytesIO())

    def cleanup(self) -> None:
        """Nothing to delete: there is no temporary file."""


class _MemoryZip(zipfile.ZipFile):
    """A zip archive that also takes a worksheet's ``BytesIO`` in
    :meth:`write` (openpyxl passes the worksheet file's path there)."""

    def write(
        self, filename: Any, arcname: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        if isinstance(filename, io.BytesIO):
            self.writestr(arcname, filename.getvalue())
            return
        super().write(filename, arcname, *args, **kwargs)


def sheet_title(label: str) -> str:
    """The dataset's label as a sheet name: no ``[]:*?/\\'``, at most 31
    characters."""
    return _SHEET_UNSAFE.sub("", label).strip()[:SHEET_TITLE_MAX] or "Export"


def _string_cell(ws: WriteOnlyWorksheet, text: str) -> Cell:
    cell = WriteOnlyCell(ws, value=ILLEGAL_CHARACTERS_RE.sub("", escape_formula(text)))
    cell.data_type = "s"
    return cell


def _xlsx_cell(
    ws: WriteOnlyWorksheet, language: str, column: ExportColumn, value: Any
) -> Cell | None:
    if value is None:
        return None
    match column.kind:
        case "date":
            cell = WriteOnlyCell(ws, value=value)
            cell.number_format = DATE_FORMAT
            return cell
        case "money":
            # openpyxl writes a Decimal as a number; its stubs only name float.
            cell = WriteOnlyCell(ws, value=cast(Any, _money(value)))
            cell.number_format = MONEY_FORMAT
            return cell
        case "integer":
            return WriteOnlyCell(ws, value=int(value))
    text = text_of(language, column, value)
    return None if text is None else _string_cell(ws, text)


def render_xlsx(
    language: str,
    dataset: ExportDataset,
    columns: Sequence[str],
    rows: Iterable[dict[str, Any]],
) -> bytes:
    """One write-only sheet, header frozen, typed cells, all in memory."""
    chosen = [dataset.column(code) for code in columns]
    workbook = Workbook(write_only=True)
    ws = workbook.create_sheet(sheet_title(dataset_label(language, dataset.code)))
    ws.freeze_panes = "A2"
    ws._writer = _MemoryWorksheetWriter(ws)
    ws._writer.write_top()
    ws.append(
        [_string_cell(ws, header_label(language, dataset, c.code)) for c in chosen]
    )
    for row in rows:
        ws.append([_xlsx_cell(ws, language, c, row.get(c.code)) for c in chosen])
    workbook.properties.creator = "Classora"
    out = io.BytesIO()
    with _MemoryZip(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        ExcelWriter(workbook, archive).write_data()
    return out.getvalue()


def file_name(dataset_code: str, export_format: str, requested_at: datetime) -> str:
    """``classora-<dataset>-<YYYYMMDD>-<HHMM>.<csv|xlsx>`` (#124 E6), with the
    request time already in the center's zone."""
    return (
        f"classora-{dataset_code}-{requested_at:%Y%m%d}-{requested_at:%H%M}"
        f".{export_format}"
    )
