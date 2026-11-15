"""The ``students`` import kind (#122 A3, B5, D5; E6, E8).

Registered by the composition roots (``app/wiring.py``) through
``families.service.STUDENTS_IMPORT_KIND``. Permission: ``student.manage``,
the one ``POST /api/v1/students`` needs (E5).

**Columns** (E8): ``given_names``\\*, ``family_names``\\*, ``birthdate``\\*,
``email``, ``phone``, ``school_name``, ``school_grade``, ``education_level``,
``contact_name``\\*, ``contact_relationship``\\*, ``contact_phone``\\* and
``contact2_name``, ``contact2_relationship``, ``contact2_phone`` (all three
or none). No guardians, families, logins or enrollments.

**Validation**: each row goes through :class:`StudentCreate` (no copied
rules); Pydantic errors become the #122 B4 codes per column
(:func:`_issue_code`). ``birthdate`` is ``YYYY-MM-DD`` or ``DD/MM/YYYY``,
not after the center's today (``future_date``). ``education_level`` names an
**active** level, matched by ``lower(name)`` (``education_level_unknown``
while staging; ``education_level_unavailable`` in the worker's re-check, the
code ``POST /api/v1/students`` gives a level deactivated meanwhile).

**Natural key** (E6): ``f_unaccent(lower(given_names))``,
``f_unaccent(lower(family_names))`` and ``birthdate``. A row matching an
existing student is ``duplicate`` (``duplicate_existing``); a row repeating an
earlier **ready** row of the same file is ``duplicate``
(``duplicate_in_file``). Existing students are found with one statement for
the whole file.

``prepare`` runs a fixed number of statements: the center's today, the
levels by name and the natural keys (B7). ``commit_row`` creates the student
like ``POST /api/v1/students`` (active, contacts in order) through
``families.service.create_imported_student``.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import Date, Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families.permissions import STUDENT_MANAGE
from app.modules.families.schemas import StudentCreate

KIND: Final = "students"

PROFILE_COLUMNS: Final = (
    "given_names",
    "family_names",
    "birthdate",
    "email",
    "phone",
    "school_name",
    "school_grade",
    "education_level",
)
CONTACT_FIELDS: Final = ("name", "relationship", "phone")
CONTACT_PREFIXES: Final = ("contact", "contact2")
REQUIRED: Final = frozenset(
    {"given_names", "family_names", "birthdate"}
    | {f"contact_{name}" for name in CONTACT_FIELDS}
)
COLUMNS: Final = tuple(
    administration.ImportColumn(name, name in REQUIRED)
    for name in (
        *PROFILE_COLUMNS,
        *(f"{prefix}_{name}" for prefix in CONTACT_PREFIXES for name in CONTACT_FIELDS),
    )
)

_ISO_DATE: Final = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY_DATE: Final = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")

_KEYS_SQL: Final = text(
    """
    SELECT k.i AS i,
           public.f_unaccent(lower(k.g)) AS g,
           public.f_unaccent(lower(k.f)) AS f,
           EXISTS (
               SELECT 1 FROM students s
               WHERE public.f_unaccent(lower(s.given_names))
                     = public.f_unaccent(lower(k.g))
                 AND public.f_unaccent(lower(s.family_names))
                     = public.f_unaccent(lower(k.f))
                 AND s.birthdate = k.b
           ) AS taken
    FROM unnest(:g, :f, :b) WITH ORDINALITY AS k(g, f, b, i)
    """
).bindparams(
    bindparam("g", type_=ARRAY(Text)),
    bindparam("f", type_=ARRAY(Text)),
    bindparam("b", type_=ARRAY(Date)),
)


_MATCH_SQL: Final = text(
    """
    SELECT k.i AS i, s.id AS student_id, s.is_active AS is_active,
           fs.family_id AS family_id
    FROM unnest(:g, :f, :b) WITH ORDINALITY AS k(g, f, b, i)
    LEFT JOIN students s
           ON public.f_unaccent(lower(s.given_names))
              = public.f_unaccent(lower(k.g))
          AND public.f_unaccent(lower(s.family_names))
              = public.f_unaccent(lower(k.f))
          AND s.birthdate = k.b
    LEFT JOIN family_students fs ON fs.student_id = s.id
    """
).bindparams(
    bindparam("g", type_=ARRAY(Text)),
    bindparam("f", type_=ARRAY(Text)),
    bindparam("b", type_=ARRAY(Date)),
)

STUDENT_KEY_COLUMNS: Final = (
    "student_given_names",
    "student_family_names",
    "student_birthdate",
)
"""The columns other import kinds name a student with (#123 E10)."""


@dataclass(frozen=True)
class StudentKeyMatch:
    """What one natural key names (#123 A3): ``status`` is ``found``,
    ``student_unknown`` (no student) or ``student_ambiguous`` (more than
    one); a found student carries its id, whether it is active and its
    current family (``None`` without one)."""

    status: str
    student_id: uuid.UUID | None = None
    is_active: bool = False
    family_id: uuid.UUID | None = None


async def students_by_natural_key(
    session: AsyncSession, keys: Sequence[tuple[str, str, date]]
) -> list[StudentKeyMatch]:
    """Per ``(given_names, family_names, birthdate)`` key, in order, the
    student it names by the #122 natural key (unaccented, case-insensitive
    names and the birthdate), in **one** statement (none for no keys). Used
    by the ``schedules`` and ``opening_balances`` import kinds (#123 A3,
    E10). No access check."""
    if not keys:
        return []
    rows = await session.execute(
        _MATCH_SQL,
        {
            "g": [key[0] for key in keys],
            "f": [key[1] for key in keys],
            "b": [key[2] for key in keys],
        },
    )
    found: dict[int, list[Any]] = {}
    for row in rows:
        found.setdefault(row.i, [])
        if row.student_id is not None:
            found[row.i].append(row)
    matches: list[StudentKeyMatch] = []
    for index in range(1, len(keys) + 1):
        candidates = found.get(index, [])
        if not candidates:
            matches.append(StudentKeyMatch("student_unknown"))
        elif len(candidates) > 1:
            matches.append(StudentKeyMatch("student_ambiguous"))
        else:
            [one] = candidates
            matches.append(
                StudentKeyMatch(
                    "found",
                    student_id=one.student_id,
                    is_active=bool(one.is_active),
                    family_id=one.family_id,
                )
            )
    return matches


def parse_birthdate(value: str) -> date | None:
    """``YYYY-MM-DD`` or ``DD/MM/YYYY`` as a date, or ``None``."""
    iso = _ISO_DATE.fullmatch(value)
    dmy = _DMY_DATE.fullmatch(value)
    try:
        if iso is not None:
            return date(int(iso[1]), int(iso[2]), int(iso[3]))
        if dmy is not None:
            return date(int(dmy[3]), int(dmy[2]), int(dmy[1]))
    except ValueError:
        return None
    return None


def _column_of(loc: tuple[int | str, ...]) -> str | None:
    """The CSV column a :class:`StudentCreate` error location points at."""
    if not loc:
        return None
    head = loc[0]
    if head == "emergency_contacts":
        if len(loc) >= 3 and isinstance(loc[1], int) and loc[1] < 2:
            return f"{CONTACT_PREFIXES[loc[1]]}_{loc[2]}"
        return "contact_name"
    if head == "education_level_id":
        return "education_level"
    return str(head)


def _issue_code(column: str, error_type: str) -> str:
    """The #122 B4 code of a Pydantic error on ``column``."""
    if error_type in {"missing", "string_too_short"}:
        return "required"
    if error_type == "string_too_long":
        return "too_long"
    if column == "birthdate":
        return "invalid_date"
    if error_type == "string_pattern_mismatch":
        if column.endswith("email"):
            return "invalid_email"
        if column.endswith("phone"):
            return "invalid_phone"
    return "invalid"


@dataclass
class _Checked:
    """A row after validation, before the duplicate checks."""

    errors: list[administration.RowIssue] = field(default_factory=list)
    body: StudentCreate | None = None


def _value(row: Mapping[str, str], column: str) -> str:
    return row.get(column, "").strip()


def _body_data(
    row: Mapping[str, str],
    levels: Mapping[str, uuid.UUID],
    errors: list[administration.RowIssue],
    *,
    level_code: str,
) -> dict[str, Any]:
    """The :class:`StudentCreate` input of a row; the checks Pydantic can't
    make (date forms, the second contact, the level) add to ``errors``."""
    data: dict[str, Any] = {
        name: _value(row, name)
        for name in ("given_names", "family_names", "email", "phone")
        + ("school_name", "school_grade")
    }
    raw_birthdate = _value(row, "birthdate")
    if not raw_birthdate:
        errors.append(administration.RowIssue("birthdate", "required"))
    else:
        birthdate = parse_birthdate(raw_birthdate)
        if birthdate is None:
            errors.append(administration.RowIssue("birthdate", "invalid_date"))
        else:
            data["birthdate"] = birthdate.isoformat()
    level_name = _value(row, "education_level")
    if level_name:
        level_id = levels.get(level_name.lower())
        if level_id is None:
            errors.append(administration.RowIssue("education_level", level_code))
        else:
            data["education_level_id"] = str(level_id)
    contacts = [
        {name: _value(row, f"contact_{name}") for name in CONTACT_FIELDS},
    ]
    second = {name: _value(row, f"contact2_{name}") for name in CONTACT_FIELDS}
    if any(second.values()):
        if all(second.values()):
            contacts.append(second)
        else:
            errors.extend(
                administration.RowIssue(f"contact2_{name}", "required")
                for name, value in second.items()
                if not value
            )
    data["emergency_contacts"] = contacts
    return data


def check_row(
    row: Mapping[str, str],
    levels: Mapping[str, uuid.UUID],
    today: date,
    *,
    level_code: str = "education_level_unknown",
) -> _Checked:
    """Validate one row with :class:`StudentCreate` (pure)."""
    checked = _Checked()
    data = _body_data(row, levels, checked.errors, level_code=level_code)
    flagged = {issue.column for issue in checked.errors}
    try:
        body = StudentCreate.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            column = _column_of(tuple(error["loc"]))
            if column is None or column in flagged:
                continue
            flagged.add(column)
            checked.errors.append(
                administration.RowIssue(column, _issue_code(column, error["type"]))
            )
        return checked
    if checked.errors:
        return checked
    if body.birthdate > today:
        checked.errors.append(administration.RowIssue("birthdate", "future_date"))
        return checked
    checked.body = body
    return checked


async def _natural_keys(
    session: AsyncSession, bodies: Sequence[StudentCreate]
) -> list[tuple[tuple[str, str, date], bool]]:
    """Per body: its normalized natural key and whether a student has it
    (one statement)."""
    if not bodies:
        return []
    rows = await session.execute(
        _KEYS_SQL,
        {
            "g": [body.given_names for body in bodies],
            "f": [body.family_names for body in bodies],
            "b": [body.birthdate for body in bodies],
        },
    )
    by_index = {row.i: ((row.g, row.f), bool(row.taken)) for row in rows}
    return [
        (
            (*by_index[index + 1][0], body.birthdate),
            by_index[index + 1][1],
        )
        for index, body in enumerate(bodies)
    ]


def _row_values(row: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in row.items() if key in _COLUMN_NAMES}


_COLUMN_NAMES: Final = frozenset(column.name for column in COLUMNS)


@dataclass(frozen=True)
class StudentsImportKind:
    """See the module docstring."""

    code: str = KIND
    permission: str = STUDENT_MANAGE
    columns: Sequence[administration.ImportColumn] = COLUMNS
    batch_size: int = administration.DEFAULT_BATCH_SIZE

    def may_import(self, principal: Principal) -> bool:
        return principal.has(STUDENT_MANAGE)

    async def prepare(
        self,
        session: AsyncSession,
        rows: Sequence[Mapping[str, str]],
        principal: Principal | None,
    ) -> list[administration.RowResult]:
        today = await administration.center_today(session)
        level_names = {
            _value(row, "education_level").lower()
            for row in rows
            if _value(row, "education_level")
        }
        levels = await catalog.active_level_ids_by_name(session, level_names)
        level_code = (
            "education_level_unknown"
            if principal is not None
            else "education_level_unavailable"
        )
        checked = [
            check_row(_row_values(row), levels, today, level_code=level_code)
            for row in rows
        ]
        bodies = [item.body for item in checked if item.body is not None]
        keys = iter(await _natural_keys(session, bodies))
        seen: set[tuple[str, str, date]] = set()
        results: list[administration.RowResult] = []
        for item in checked:
            if item.body is None:
                results.append(
                    administration.RowResult("error", errors=tuple(item.errors))
                )
                continue
            key, taken = next(keys)
            if taken:
                warning = "duplicate_existing"
            elif key in seen:
                warning = "duplicate_in_file"
            else:
                seen.add(key)
                results.append(administration.RowResult("ready"))
                continue
            results.append(
                administration.RowResult(
                    "duplicate", warnings=(administration.RowIssue(None, warning),)
                )
            )
        return results

    async def commit_row(
        self,
        session: AsyncSession,
        row_values: Mapping[str, str],
        ref: administration.ImportRowRef,
        actor_user_id: uuid.UUID,
    ) -> administration.CommitOutcome:
        # Imported here: families.service exports this kind (no import cycle).
        from app.modules.families import service as families

        today = await administration.center_today(session)
        name = _value(row_values, "education_level").lower()
        levels = await catalog.active_level_ids_by_name(session, [name] if name else [])
        checked = check_row(
            _row_values(row_values),
            levels,
            today,
            level_code="education_level_unavailable",
        )
        if checked.body is None:
            return administration.CommitOutcome.failed("invalid", checked.errors)
        record_id = await families.create_imported_student(
            session, checked.body, today=today
        )
        return administration.CommitOutcome.created(record_id)


STUDENTS_IMPORT_KIND: Final = StudentsImportKind()
