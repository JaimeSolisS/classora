"""The ``schedules`` import kind (#123 B; E1, E3-E5, E10).

Registered by the composition roots (``app/wiring.py``) through
``scheduling.service.SCHEDULES_IMPORT_KIND``. Permission:
``schedule.manage``, the one ``POST /api/v1/class-series`` needs; ``batch_size``
20 (one row may create up to a horizon of occurrences, each checked).

**One row = one recurring individual series** (E1; a one-off class is
``count = 1``). **Columns** (B1): ``student_given_names``\\*,
``student_family_names``\\*, ``student_birthdate``\\* (the #122 natural key,
E10: exactly one student), ``subject``\\* and ``education_level``\\* (the
subject by ``(lower(level), lower(name))``), ``starts_on``\\*,
``start_time``\\* (``HH:MM``), ``duration_minutes``\\*, ``weekdays``\\* (ISO
1-7 separated by spaces), ``interval`` (default 1), ``until`` or ``count``,
``tutor_username`` and ``classroom`` (by ``lower(name)``). Dates are
``YYYY-MM-DD`` or ``DD/MM/YYYY``. No override and no ``respond_by`` (E4).

**Staging** (B2, B3) validates the rule with :class:`ClassSeriesCreate` and
:func:`~app.modules.scheduling.series.check_series_rule` (no copied rule),
resolves the references in bulk and finds duplicates: an existing
individual series with the same student, subject, ``starts_on``,
``start_time`` and weekdays is ``duplicate_existing``; an earlier ready row
with the same key ``duplicate_in_file``. A fixed number of statements: the
zone, the students, the subjects, the tutors, the classrooms and the
duplicates. **Conflicts are not checked here** (E3).

**Commit** (B4): :meth:`SchedulesImportKind.commit_row` checks the row again
and calls :func:`~app.modules.scheduling.series.create_class_series`
(``override = None``, ``horizon_weeks`` from the run's settings, the committer as
actor), so every conflict check of a manual booking runs, across the rows
of the file too. A ``409 scheduling_conflict`` is ``failed`` with the
distinct conflict codes; a ``422`` is ``failed`` with its codes on their
columns; ``series_not_offerable`` is ``failed``. With a tutor the series
gets its pending offer and the tutor a notification, as by hand (E5).
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import Date, Text, Time, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.core.errors import AppError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.errors import (
    SchedulingConflictError,
    SeriesNotOfferableError,
    SessionValidationError,
)
from app.modules.scheduling.schemas import ClassSeriesCreate
from app.modules.scheduling.series import check_series_rule, create_class_series

KIND: Final = "schedules"
SCHEDULE_MANAGE: Final = "schedule.manage"
BATCH_SIZE: Final = 20
TUTOR_ROLE: Final = "tutor"
ACTIVE: Final = "active"

REQUIRED: Final = (
    *families.STUDENT_KEY_COLUMNS,
    "subject",
    "education_level",
    "starts_on",
    "start_time",
    "duration_minutes",
    "weekdays",
)
OPTIONAL: Final = ("interval", "until", "count", "tutor_username", "classroom")
COLUMNS: Final = (
    *(administration.ImportColumn(name, True) for name in REQUIRED),
    *(administration.ImportColumn(name) for name in OPTIONAL),
)
_COLUMN_NAMES: Final = frozenset((*REQUIRED, *OPTIONAL))

_INTEGER: Final = re.compile(r"\d{1,6}")
_SHORT_HOUR: Final = re.compile(r"\d:[0-5]\d")
_PLACEHOLDER: Final = uuid.UUID(int=0)

_RULE_COLUMNS: Final = {
    "starts_on": "starts_on",
    "start_time": "start_time",
    "duration_minutes": "duration_minutes",
    "weekdays": "weekdays",
    "interval": "interval",
    "until": "until",
    "count": "count",
}
"""The :class:`ClassSeriesCreate` field (or ``recurrence`` member) of each
CSV column; an error on ``recurrence`` itself is the row's."""

_REFERENCE_COLUMNS: Final = {
    "student_id": None,
    "subject_id": "subject",
    "tutor_user_id": "tutor_username",
    "classroom_id": "classroom",
}
"""The CSV column of a reference error of ``create_class_series`` (the
student is named by three columns: the error is the row's)."""

_DUPLICATES_SQL: Final = text(
    """
    SELECT k.i AS i,
           EXISTS (
               SELECT 1
               FROM class_series cs
               JOIN class_series_participants p ON p.class_series_id = cs.id
               WHERE cs.kind = 'individual'
                 AND p.student_id = k.s
                 AND cs.subject_id = k.sub
                 AND cs.starts_on = k.d
                 AND cs.start_time = k.t
                 AND array_to_string(
                       ARRAY(SELECT w FROM unnest(cs.weekdays) AS w ORDER BY w), ' '
                     ) = k.w
           ) AS taken
    FROM unnest(:s, :sub, :d, :t, :w) WITH ORDINALITY AS k(s, sub, d, t, w, i)
    """
).bindparams(
    bindparam("s", type_=ARRAY(UUID(as_uuid=True))),
    bindparam("sub", type_=ARRAY(UUID(as_uuid=True))),
    bindparam("d", type_=ARRAY(Date)),
    bindparam("t", type_=ARRAY(Time)),
    bindparam("w", type_=ARRAY(Text)),
)

type SeriesKey = tuple[uuid.UUID, uuid.UUID, date, time, str]


def _value(row: Mapping[str, str], column: str) -> str:
    return row.get(column, "").strip()


def parse_weekdays(value: str) -> list[int] | None:
    """``"1 3"`` as ``[1, 3]`` (order and repeats kept for the rule's own
    checks), or ``None`` when a token is not a whole number."""
    tokens = value.split()
    if not all(_INTEGER.fullmatch(token) for token in tokens):
        return None
    return [int(token) for token in tokens]


def _start_time(value: str) -> str:
    # A spreadsheet often drops the leading zero of an hour ("9:00").
    return f"0{value}" if _SHORT_HOUR.fullmatch(value) else value


@dataclass
class _Parsed:
    """A row's own values after the format checks (no lookups)."""

    errors: list[administration.RowIssue] = field(default_factory=list)
    student_key: tuple[str, str, date] | None = None
    subject_key: tuple[str, str] | None = None
    tutor: str | None = None
    classroom: str | None = None
    data: dict[str, Any] | None = None


def _parse(row: Mapping[str, str]) -> _Parsed:
    """The format checks of one row (pure)."""
    parsed = _Parsed()
    issue = parsed.errors.append
    for name in REQUIRED:
        if not _value(row, name):
            issue(administration.RowIssue(name, "required"))
    dates: dict[str, date | None] = {}
    for name in ("student_birthdate", "starts_on", "until"):
        raw = _value(row, name)
        dates[name] = None
        if raw:
            dates[name] = families.parse_birthdate(raw)
            if dates[name] is None:
                issue(administration.RowIssue(name, "invalid_date"))
    numbers: dict[str, int | None] = {}
    for name in ("duration_minutes", "interval", "count"):
        raw = _value(row, name)
        numbers[name] = None
        if raw:
            if _INTEGER.fullmatch(raw) is None:
                issue(administration.RowIssue(name, "invalid"))
            else:
                numbers[name] = int(raw)
    weekdays: list[int] | None = None
    if _value(row, "weekdays"):
        weekdays = parse_weekdays(_value(row, "weekdays"))
        if weekdays is None:
            issue(administration.RowIssue("weekdays", "invalid"))
    given, family = (_value(row, name) for name in families.STUDENT_KEY_COLUMNS[:2])
    birthdate = dates["student_birthdate"]
    if given and family and birthdate is not None:
        parsed.student_key = (given, family, birthdate)
    level, subject = _value(row, "education_level"), _value(row, "subject")
    if level and subject:
        parsed.subject_key = (level.lower(), subject.lower())
    tutor = _value(row, "tutor_username")
    if tutor:
        try:
            parsed.tutor = identity.normalize_username(tutor)
        except identity.UsernameInvalidError:
            issue(administration.RowIssue("tutor_username", "tutor_unknown"))
    room = _value(row, "classroom")
    if room:
        parsed.classroom = room.lower()
    if parsed.errors:
        return parsed
    recurrence: dict[str, Any] = {"weekdays": weekdays}
    if numbers["interval"] is not None:
        recurrence["interval"] = numbers["interval"]
    if dates["until"] is not None:
        recurrence["until"] = dates["until"]
    if numbers["count"] is not None:
        recurrence["count"] = numbers["count"]
    parsed.data = {
        "kind": "individual",
        "starts_on": dates["starts_on"],
        "start_time": _start_time(_value(row, "start_time")),
        "duration_minutes": numbers["duration_minutes"],
        "recurrence": recurrence,
    }
    return parsed


def _pydantic_issue(
    loc: tuple[int | str, ...], error_type: str
) -> administration.RowIssue:
    column = str(loc[-1]) if loc else None
    if column == "start_time":
        return administration.RowIssue(column, "time_format")
    if column == "duration_minutes" and error_type in {
        "greater_than_equal",
        "less_than_equal",
    }:
        return administration.RowIssue(column, "duration_out_of_range")
    return administration.RowIssue(_RULE_COLUMNS.get(column or ""), "invalid")


def _field_issues(error: AppError) -> list[administration.RowIssue]:
    """The row issues of a ``422`` from the rule checks or the create."""
    issues: list[administration.RowIssue] = []
    for item in error.errors or []:
        path = [str(part) for part in item["path"] if part != "body"]
        name = path[-1] if path else ""
        column = _REFERENCE_COLUMNS.get(name, _RULE_COLUMNS.get(name))
        issues.append(administration.RowIssue(column, item["code"]))
    return issues


def _rule_body(
    data: Mapping[str, Any], zone: str
) -> tuple[ClassSeriesCreate | None, list[administration.RowIssue]]:
    """The rule checks of :class:`ClassSeriesCreate` and
    :func:`check_series_rule`, with placeholder references (pure apart from
    the clock)."""
    try:
        body = ClassSeriesCreate.model_validate(
            {**data, "student_id": _PLACEHOLDER, "subject_id": _PLACEHOLDER}
        )
    except ValidationError as exc:
        seen: list[administration.RowIssue] = []
        for error in exc.errors(include_input=False, include_url=False):
            found = _pydantic_issue(tuple(error["loc"]), error["type"])
            if found not in seen:
                seen.append(found)
        return None, seen
    try:
        check_series_rule(body, zone)
    except SessionValidationError as exc:
        return None, _field_issues(exc)
    return body, []


@dataclass
class _Checked:
    """A row after every staging check."""

    result: administration.RowResult
    body: ClassSeriesCreate | None = None


def _series_key(body: ClassSeriesCreate) -> SeriesKey:
    days = " ".join(str(day) for day in sorted(body.recurrence.weekdays))
    return (body.student_id, body.subject_id, body.starts_on, body.start_time, days)


async def _taken(session: AsyncSession, keys: Sequence[SeriesKey]) -> list[bool]:
    """Per key, whether an individual series has it (one statement)."""
    if not keys:
        return []
    rows = await session.execute(
        _DUPLICATES_SQL,
        {
            "s": [key[0] for key in keys],
            "sub": [key[1] for key in keys],
            "d": [key[2] for key in keys],
            "t": [key[3] for key in keys],
            "w": [key[4] for key in keys],
        },
    )
    taken = {row.i: bool(row.taken) for row in rows}
    return [taken[index + 1] for index in range(len(keys))]


async def check_rows(
    session: AsyncSession, rows: Sequence[Mapping[str, str]]
) -> list[_Checked]:
    """Every staging check of ``rows`` in a fixed number of statements (B2,
    B3); a ready row carries its :class:`ClassSeriesCreate`."""
    zone = await administration.get_center_time_zone(session)
    parsed = [
        _parse({k: v for k, v in row.items() if k in _COLUMN_NAMES}) for row in rows
    ]
    student_keys = [item.student_key for item in parsed if item.student_key]
    students = iter(await families.students_by_natural_key(session, student_keys))
    subjects = await catalog.subjects_by_level_and_name(
        session, {item.subject_key for item in parsed if item.subject_key}
    )
    tutors = await identity.role_members_by_username(
        session, TUTOR_ROLE, {item.tutor for item in parsed if item.tutor}
    )
    rooms = await catalog.classrooms_by_name(
        session, {item.classroom for item in parsed if item.classroom}
    )
    staged: list[tuple[list[administration.RowIssue], ClassSeriesCreate | None]] = []
    for item in parsed:
        errors = list(item.errors)
        ids: dict[str, uuid.UUID | None] = {}
        if item.student_key is not None:
            match = next(students)
            if match.status != "found":
                errors.append(administration.RowIssue(None, match.status))
            elif not match.is_active:
                errors.append(administration.RowIssue(None, "student_inactive"))
            else:
                ids["student_id"] = match.student_id
        if item.subject_key is not None:
            subject = subjects.get(item.subject_key)
            if subject is None:
                errors.append(administration.RowIssue("subject", "subject_unknown"))
            elif not subject.usable:
                errors.append(administration.RowIssue("subject", "subject_inactive"))
            else:
                ids["subject_id"] = subject.id
        if item.tutor is not None:
            tutor = tutors.get(item.tutor)
            if tutor is None:
                errors.append(
                    administration.RowIssue("tutor_username", "tutor_unknown")
                )
            elif tutor[1] != ACTIVE:
                errors.append(
                    administration.RowIssue("tutor_username", "tutor_inactive")
                )
            else:
                ids["tutor_user_id"] = tutor[0]
        if item.classroom is not None:
            room = rooms.get(item.classroom)
            if room is None:
                errors.append(administration.RowIssue("classroom", "classroom_unknown"))
            elif not room.usable:
                errors.append(
                    administration.RowIssue("classroom", "classroom_inactive")
                )
            else:
                ids["classroom_id"] = room.id
        body: ClassSeriesCreate | None = None
        if item.data is not None:
            rule, rule_errors = _rule_body(item.data, zone)
            errors.extend(rule_errors)
            if rule is not None and not errors:
                body = rule.model_copy(update=ids)
        staged.append((errors, body))
    bodies = [body for _, body in staged if body is not None]
    taken = iter(await _taken(session, [_series_key(body) for body in bodies]))
    seen: set[SeriesKey] = set()
    checked: list[_Checked] = []
    for errors, body in staged:
        if body is None:
            checked.append(
                _Checked(administration.RowResult("error", errors=tuple(errors)))
            )
            continue
        key = _series_key(body)
        if next(taken):
            warning = "duplicate_existing"
        elif key in seen:
            warning = "duplicate_in_file"
        else:
            seen.add(key)
            checked.append(_Checked(administration.RowResult("ready"), body))
            continue
        checked.append(
            _Checked(
                administration.RowResult(
                    "duplicate", warnings=(administration.RowIssue(None, warning),)
                )
            )
        )
    return checked


def _conflict_codes(error: SchedulingConflictError) -> list[administration.RowIssue]:
    """The distinct conflict codes, in first-seen order (B4)."""
    codes: list[str] = []
    for conflict in error.conflicts or []:
        if conflict["code"] not in codes:
            codes.append(conflict["code"])
    return [administration.RowIssue(None, code) for code in codes]


@dataclass(frozen=True)
class SchedulesImportKind:
    """See the module docstring."""

    code: str = KIND
    permission: str = SCHEDULE_MANAGE
    columns: Sequence[administration.ImportColumn] = COLUMNS
    batch_size: int = BATCH_SIZE

    def may_import(self, principal: Principal) -> bool:
        return principal.has(SCHEDULE_MANAGE)

    async def prepare(
        self,
        session: AsyncSession,
        rows: Sequence[Mapping[str, str]],
        principal: Principal | None,
    ) -> list[administration.RowResult]:
        return [item.result for item in await check_rows(session, rows)]

    async def commit_row(
        self,
        session: AsyncSession,
        row_values: Mapping[str, str],
        ref: administration.ImportRowRef,
        actor_user_id: uuid.UUID,
    ) -> administration.CommitOutcome:
        [checked] = await check_rows(session, [row_values])
        if checked.result.status == "duplicate":
            return administration.CommitOutcome.skipped("duplicate_existing")
        if checked.body is None:
            return administration.CommitOutcome.failed("invalid", checked.result.errors)
        try:
            created = await create_class_series(
                session,
                checked.body,
                horizon_weeks=administration.run_settings(
                    session
                ).scheduling_horizon_weeks,
                actor_id=actor_user_id,
            )
        except SchedulingConflictError as exc:
            return administration.CommitOutcome.failed(exc.code, _conflict_codes(exc))
        except SessionValidationError as exc:
            return administration.CommitOutcome.failed("invalid", _field_issues(exc))
        except SeriesNotOfferableError as exc:
            return administration.CommitOutcome.failed(exc.code)
        return administration.CommitOutcome.created(created.id)


SCHEDULES_IMPORT_KIND: Final = SchedulesImportKind()
"""The ``schedules`` kind, registered in ``app/wiring.py``."""
