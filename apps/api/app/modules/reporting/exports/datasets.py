"""The three export datasets (#124 A3, E2).

Each one is a staff list page made exportable. Its rows come from **the
page's own service function**, called with the requester's principal (the
worker reloads it at run time), and its columns are fields the page's item
already returns, flattened. No other field is read, so the page's record
filter, masking and minimization hold by construction (#124 E4):

- ``students`` (``/students``, ``student.view``, personal):
  :func:`app.modules.families.service.list_students`, 100 per page, in its
  default order (family names, given names). No birthdate and no contact data
  (``StudentListItem`` has none; spec §16).
- ``class_sessions`` (the calendar, ``schedule.view``, personal):
  :func:`app.modules.scheduling.service.list_calendar_sessions` over
  consecutive windows of at most 45 days covering ``[from 00:00, to + 1 day
  00:00)`` in the center's zone; a session seen in two windows is kept once;
  a ``truncated`` window fails the job (``row_limit_exceeded``). By start,
  then id.
- ``expenses`` (``/finance/expenses``, ``expense.view``, not personal):
  :func:`app.modules.finance.service.list_expenses` with sort
  ``-incurred_on``, 100 per page; the figures use the run's today, as the page
  does.

Dates and times are the center's local ones; values are raw (labels and
formats are the renderer's, :mod:`.render`).
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, ClassVar, Final, Literal, TypeAlias
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.i18n import message
from app.core.pagination import OffsetParams
from app.modules.administration import service as administration
from app.modules.families import service as families
from app.modules.families.schemas import StudentStatus
from app.modules.finance import service as finance
from app.modules.finance.schemas import ExpenseStatus
from app.modules.reporting.exports.errors import ExportRangeInvalidError
from app.modules.reporting.exports.registry import (
    ExportColumn,
    ExportDataset,
    ExportFormat,
    ExportParametersBase,
    RowLimitExceeded,
)
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import ClassSessionRangeQuery, SessionKindCode

PAGE_SIZE: Final = 100
"""Rows read per page of a paged source (#124 A3)."""
CALENDAR_WINDOW: Final = timedelta(days=45)
"""The calendar range read's longest range (#57)."""
MAX_SPAN_DAYS: Final = 366
"""A date range covers at most 366 days, both ends included (#124 E6)."""

STUDENTS: Final = "students"
CLASS_SESSIONS: Final = "class_sessions"
EXPENSES: Final = "expenses"


def _span_check(start: date, end: date, *, start_field: str, end_field: str) -> None:
    """``end`` before ``start`` (at the end field), then a span over 366 days
    (at the start field), the #117 report order."""
    if end < start:
        raise ExportRangeInvalidError(
            end_field, "The last date must not be before the first."
        )
    if (end - start).days >= MAX_SPAN_DAYS:
        raise ExportRangeInvalidError(
            start_field, "The range must not exceed 366 days."
        )


def _too_many(total: int, limit: int) -> None:
    if total > limit:
        raise RowLimitExceeded()


# --- students -------------------------------------------------------------------------

type StudentsColumn = Literal[
    "family_names",
    "given_names",
    "is_active",
    "is_minor",
    "school_grade",
    "education_level",
]
type StudentsGroupable = Literal[
    "is_active", "is_minor", "school_grade", "education_level"
]


class StudentsExportFilters(BaseModel):
    """The ``/students`` filters an export can use."""

    model_config = ConfigDict(extra="forbid")

    status: StudentStatus = "active"
    education_level_id: uuid.UUID | None = None


class StudentsExportParameters(ExportParametersBase):
    """A ``students`` export."""

    COLUMN_CODES: ClassVar[tuple[str, ...]] = (
        "family_names",
        "given_names",
        "is_active",
        "is_minor",
        "school_grade",
        "education_level",
    )

    dataset: Literal["students"]
    format: ExportFormat
    columns: list[StudentsColumn] | None = Field(default=None, min_length=1)
    group_by: StudentsGroupable | None = None
    filters: StudentsExportFilters = Field(default_factory=StudentsExportFilters)


async def student_rows(
    session: AsyncSession,
    principal: Principal,
    filters: StudentsExportFilters,
    *,
    limit: int,
    language: str,
) -> list[dict[str, Any]]:
    """``families.service.list_students`` with the requester's principal,
    page by page."""
    source = families.get_tutor_assignment_source()
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await families.list_students(
            session,
            principal=principal,
            source=source,
            q=None,
            status=filters.status,
            education_level_id=filters.education_level_id,
            page=OffsetParams(limit=PAGE_SIZE, offset=offset),
        )
        _too_many(page.total, limit)
        rows.extend(
            {
                "family_names": item.family_names,
                "given_names": item.given_names,
                "is_active": item.is_active,
                "is_minor": item.is_minor,
                "school_grade": item.school_grade,
                "education_level": (
                    item.education_level.name if item.education_level else None
                ),
            }
            for item in page.items
        )
        offset += PAGE_SIZE
        if not page.items or offset >= page.total:
            return rows


STUDENTS_DATASET: Final = ExportDataset(
    code=STUDENTS,
    permission="student.view",
    personal=True,
    columns=(
        ExportColumn("family_names", "text"),
        ExportColumn("given_names", "text"),
        ExportColumn("is_active", "boolean", groupable=True),
        ExportColumn("is_minor", "boolean", groupable=True),
        ExportColumn("school_grade", "text", groupable=True),
        ExportColumn("education_level", "text", groupable=True),
    ),
    filters_model=StudentsExportFilters,
    parameters_model=StudentsExportParameters,
    rows=student_rows,
    date_filter="none",
)


# --- class_sessions -------------------------------------------------------------------

type ClassSessionsColumn = Literal[
    "date",
    "start_time",
    "end_time",
    "duration_minutes",
    "kind",
    "state",
    "subject",
    "education_level",
    "tutor",
    "classroom",
    "students",
    "series",
]
type ClassSessionsGroupable = Literal[
    "date", "kind", "state", "subject", "education_level", "tutor", "classroom"
]


class ClassSessionsExportFilters(BaseModel):
    """The calendar's filters (``ClassSessionRangeQuery``) with local dates:
    ``from`` and ``to`` are required, ``from ≤ to``, at most 366 days."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: date = Field(alias="from", description="The first local date.")
    to: date = Field(description="The last local date (included).")
    tutor_user_id: uuid.UUID | None = None
    student_id: uuid.UUID | None = None
    classroom_id: uuid.UUID | None = None
    kind: SessionKindCode | None = None
    include_cancelled: bool = False


class ClassSessionsExportParameters(ExportParametersBase):
    """A ``class_sessions`` export."""

    COLUMN_CODES: ClassVar[tuple[str, ...]] = (
        "date",
        "start_time",
        "end_time",
        "duration_minutes",
        "kind",
        "state",
        "subject",
        "education_level",
        "tutor",
        "classroom",
        "students",
        "series",
    )

    dataset: Literal["class_sessions"]
    format: ExportFormat
    columns: list[ClassSessionsColumn] | None = Field(default=None, min_length=1)
    group_by: ClassSessionsGroupable | None = None
    filters: ClassSessionsExportFilters


def _check_session_range(filters: ClassSessionsExportFilters) -> None:
    _span_check(filters.from_, filters.to, start_field="from", end_field="to")


def _session_windows(
    first: date, last: date, zone: ZoneInfo
) -> list[tuple[datetime, datetime]]:
    """``[first 00:00, last + 1 day 00:00)`` in ``zone``, as UTC windows of at
    most :data:`CALENDAR_WINDOW`."""
    start = datetime.combine(first, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=zone).astimezone(
        UTC
    )
    windows = []
    while start < end:
        stop = min(start + CALENDAR_WINDOW, end)
        windows.append((start, stop))
        start = stop
    return windows


async def class_session_rows(
    session: AsyncSession,
    principal: Principal,
    filters: ClassSessionsExportFilters,
    *,
    limit: int,
    language: str,
) -> list[dict[str, Any]]:
    """``scheduling.service.list_calendar_sessions`` over 45-day windows."""
    zone = ZoneInfo(await administration.get_center_time_zone(session))
    found: dict[uuid.UUID, Any] = {}
    for start, stop in _session_windows(filters.from_, filters.to, zone):
        calendar = await scheduling.list_calendar_sessions(
            session,
            ClassSessionRangeQuery.model_validate(
                {
                    "from": start,
                    "to": stop,
                    "tutor_user_id": filters.tutor_user_id,
                    "student_id": filters.student_id,
                    "classroom_id": filters.classroom_id,
                    "kind": filters.kind,
                    "include_cancelled": filters.include_cancelled,
                }
            ),
        )
        if calendar.truncated:
            raise RowLimitExceeded()
        for item in calendar.items:
            found.setdefault(item.id, item)
        _too_many(len(found), limit)
    items = sorted(found.values(), key=lambda item: (item.starts_at, item.id))
    rows = []
    for item in items:
        starts = item.starts_at.astimezone(zone)
        ends = item.ends_at.astimezone(zone)
        rows.append(
            {
                "date": starts.date(),
                "start_time": starts.time().replace(second=0, microsecond=0),
                "end_time": ends.time().replace(second=0, microsecond=0),
                "duration_minutes": int(
                    (item.ends_at - item.starts_at).total_seconds() // 60
                ),
                "kind": item.kind,
                "state": item.state,
                "subject": item.subject.name,
                "education_level": item.subject.education_level_name,
                "tutor": item.tutor.display_name if item.tutor else None,
                "classroom": item.classroom.name if item.classroom else None,
                "students": [p.display_name for p in item.participants],
                "series": item.series_id is not None,
            }
        )
    return rows


CLASS_SESSIONS_DATASET: Final = ExportDataset(
    code=CLASS_SESSIONS,
    permission="schedule.view",
    personal=True,
    columns=(
        ExportColumn("date", "date", groupable=True),
        ExportColumn("start_time", "time"),
        ExportColumn("end_time", "time"),
        ExportColumn("duration_minutes", "integer"),
        ExportColumn("kind", "enum", groupable=True, values="session_kind"),
        ExportColumn("state", "enum", groupable=True, values="session_state"),
        ExportColumn("subject", "text", groupable=True),
        ExportColumn("education_level", "text", groupable=True),
        ExportColumn("tutor", "text", groupable=True),
        ExportColumn("classroom", "text", groupable=True),
        ExportColumn("students", "list"),
        ExportColumn("series", "boolean"),
    ),
    filters_model=ClassSessionsExportFilters,
    parameters_model=ClassSessionsExportParameters,
    rows=class_session_rows,
    date_filter="required",
    check_filters=_check_session_range,
)


# --- expenses -------------------------------------------------------------------------

type ExpensesColumn = Literal[
    "incurred_on",
    "due_on",
    "category",
    "vendor",
    "description",
    "amount",
    "adjusted_amount",
    "paid_amount",
    "open_amount",
    "status",
    "receipt_reference",
    "template",
]
type ExpensesGroupable = Literal[
    "incurred_on", "due_on", "category", "vendor", "status", "template"
]


class ExpensesExportFilters(BaseModel):
    """The ``/finance/expenses`` filters an export can use (no text search)."""

    model_config = ConfigDict(extra="forbid")

    status: Annotated[list[ExpenseStatus], Field(min_length=1)] | None = None
    category_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None
    incurred_from: date | None = None
    incurred_to: date | None = None


class ExpensesExportParameters(ExportParametersBase):
    """An ``expenses`` export."""

    COLUMN_CODES: ClassVar[tuple[str, ...]] = (
        "incurred_on",
        "due_on",
        "category",
        "vendor",
        "description",
        "amount",
        "adjusted_amount",
        "paid_amount",
        "open_amount",
        "status",
        "receipt_reference",
        "template",
    )

    dataset: Literal["expenses"]
    format: ExportFormat
    columns: list[ExpensesColumn] | None = Field(default=None, min_length=1)
    group_by: ExpensesGroupable | None = None
    filters: ExpensesExportFilters = Field(default_factory=ExpensesExportFilters)


def _check_expense_range(filters: ExpensesExportFilters) -> None:
    if filters.incurred_from is not None and filters.incurred_to is not None:
        _span_check(
            filters.incurred_from,
            filters.incurred_to,
            start_field="incurred_from",
            end_field="incurred_to",
        )


def _category_name(category: Any, language: str) -> str:
    """The page's rule (#114): a system category shows its key's label in the
    viewer's language, any other its name."""
    if category.system_key is not None:
        return message(language, f"finance.categories.system.{category.system_key}")
    return str(category.name)


async def expense_rows(
    session: AsyncSession,
    principal: Principal,
    filters: ExpensesExportFilters,
    *,
    limit: int,
    language: str,
) -> list[dict[str, Any]]:
    """``finance.service.list_expenses`` sorted ``-incurred_on``, page by
    page."""
    expense_filters = finance.ExpenseFilters(
        statuses=filters.status,
        category_id=filters.category_id,
        vendor_id=filters.vendor_id,
        incurred_from=filters.incurred_from,
        incurred_to=filters.incurred_to,
    )
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await finance.list_expenses(
            session,
            filters=expense_filters,
            sort="-incurred_on",
            page=OffsetParams(limit=PAGE_SIZE, offset=offset),
        )
        _too_many(page.total, limit)
        rows.extend(
            {
                "incurred_on": item.incurred_on,
                "due_on": item.due_on,
                "category": _category_name(item.category, language),
                "vendor": item.vendor.name if item.vendor else None,
                "description": item.description,
                "amount": item.amount,
                "adjusted_amount": item.adjusted_amount,
                "paid_amount": item.paid_amount,
                "open_amount": item.open_amount,
                "status": item.status,
                "receipt_reference": item.receipt_reference,
                "template": item.template.name if item.template else None,
            }
            for item in page.items
        )
        offset += PAGE_SIZE
        if not page.items or offset >= page.total:
            return rows


EXPENSES_DATASET: Final = ExportDataset(
    code=EXPENSES,
    permission="expense.view",
    personal=False,
    columns=(
        ExportColumn("incurred_on", "date", groupable=True),
        ExportColumn("due_on", "date", groupable=True),
        ExportColumn("category", "text", groupable=True),
        ExportColumn("vendor", "text", groupable=True),
        ExportColumn("description", "text"),
        ExportColumn("amount", "money"),
        ExportColumn("adjusted_amount", "money"),
        ExportColumn("paid_amount", "money"),
        ExportColumn("open_amount", "money"),
        ExportColumn("status", "enum", groupable=True, values="expense_status"),
        ExportColumn("receipt_reference", "text"),
        ExportColumn("template", "text", groupable=True),
    ),
    filters_model=ExpensesExportFilters,
    parameters_model=ExpensesExportParameters,
    rows=expense_rows,
    date_filter="optional",
    check_filters=_check_expense_range,
)

BUILT_IN_DATASETS: Final = (STUDENTS_DATASET, CLASS_SESSIONS_DATASET, EXPENSES_DATASET)
"""In registry (and catalogue) order."""

ExportParameters: TypeAlias = Annotated[  # noqa: UP040 - FastAPI reads it as a body
    StudentsExportParameters | ClassSessionsExportParameters | ExpensesExportParameters,
    Field(discriminator="dataset"),
]
"""The ``POST /api/v1/exports`` body (#124 B2): one model per dataset."""
