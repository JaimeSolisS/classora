"""The ``opening_balances`` import kind (#123 C; E2, E6, E8, E10).

Registered by the composition roots (``app/wiring.py``) through
``billing.service.OPENING_BALANCES_IMPORT_KIND``. Permission:
``billing.manage``, the one a manual charge needs (no reauthentication,
E8); ``batch_size`` 100.

**One row = one positive manual charge** with an import reference (E2):
nothing else that moves money is importable (no payments, receipts,
credits, allocations, adjustments, refunds or packages).

**Columns** (C1): ``student_given_names``\\*, ``student_family_names``\\*,
``student_birthdate``\\* (the #122 natural key, E10: exactly one student),
``description``\\* (trimmed, 1-200), ``amount``\\* (digits with an optional
``.`` and up to 2 decimals: no sign, symbol or thousands separator; above
0), ``service_date``\\* (2000-01-01 to the center's today) and
``due_date``\\* (2000-01-01 to today + 366 days). Dates are ``YYYY-MM-DD``
or ``DD/MM/YYYY``.

**Staging** (C2) reuses :class:`ManualChargeCreate` for the money and text
rules. Duplicates: an **imported** charge with the same ``(student_id,
service_date, lower(description))`` is ``duplicate_existing``; an earlier
ready row with that key ``duplicate_in_file``. A charge posted by hand is
never matched. A fixed number of statements: the zone, the students (with
their current family) and the imported charges.

**Commit** (C3): :meth:`OpeningBalancesImportKind.commit_row` checks the row
again (the family is re-read now) and calls
:func:`~app.modules.billing.corrections.post_manual_charge` with the import
reference, so the account is created if missing and locked last, the
charge is ``kind = 'manual'`` with ``list_amount = amount`` and no
discount, and the ``create`` audit event's actor is the committer. A line
already posted (the natural key under the account lock, or the unique
indexes in the savepoint) is ``skipped`` ``duplicate_existing``.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import Date, Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.billing.corrections import post_manual_charge
from app.modules.billing.errors import StudentHasNoFamilyError, StudentNotInFamilyError
from app.modules.billing.schemas import ManualChargeCreate, ManualChargeDraft
from app.modules.families import service as families

KIND: Final = "opening_balances"
BILLING_MANAGE: Final = "billing.manage"
BATCH_SIZE: Final = 100
DUE_HORIZON_DAYS: Final = 366
"""The latest due date: the center's today plus this (the manual charge
route's limit)."""

REQUIRED: Final = (
    *families.STUDENT_KEY_COLUMNS,
    "description",
    "amount",
    "service_date",
    "due_date",
)
COLUMNS: Final = tuple(administration.ImportColumn(name, True) for name in REQUIRED)
_COLUMN_NAMES: Final = frozenset(REQUIRED)

_AMOUNT: Final = re.compile(r"\d{1,12}(?:\.\d{1,2})?")
"""Digits with up to 2 decimals: ``NUMERIC(14,2)`` holds 12 integer
digits."""
_PLACEHOLDER: Final = uuid.UUID(int=0)

_IMPORTED_SQL: Final = text(
    """
    SELECT k.i AS i,
           EXISTS (
               SELECT 1 FROM charges c
               WHERE c.import_job_id IS NOT NULL
                 AND c.student_id = k.s
                 AND c.service_date = k.d
                 AND lower(c.description) = lower(k.t)
           ) AS taken,
           lower(k.t) AS lowered
    FROM unnest(:s, :d, :t) WITH ORDINALITY AS k(s, d, t, i)
    """
).bindparams(
    bindparam("s", type_=ARRAY(UUID(as_uuid=True))),
    bindparam("d", type_=ARRAY(Date)),
    bindparam("t", type_=ARRAY(Text)),
)

type LineKey = tuple[uuid.UUID, date, str]


def _value(row: Mapping[str, str], column: str) -> str:
    return row.get(column, "").strip()


def _issue_code(column: str, error_type: str) -> str:
    """The C2 code of a :class:`ManualChargeCreate` error on ``column``."""
    if column == "amount":
        return (
            "amount_not_positive"
            if error_type == "amount_not_positive"
            else "invalid_amount"
        )
    if error_type in {"missing", "string_too_short"}:
        return "required"
    if error_type == "string_too_long":
        return "too_long"
    if error_type == "date_out_of_range":
        return "date_out_of_range"
    return "invalid"


@dataclass
class _Parsed:
    """A row after the checks that need no lookup."""

    errors: list[administration.RowIssue] = field(default_factory=list)
    student_key: tuple[str, str, date] | None = None
    body: ManualChargeCreate | None = None


def _parse(row: Mapping[str, str], today: date) -> _Parsed:
    """The format and money checks of one row (pure)."""
    parsed = _Parsed()
    issue = parsed.errors.append
    for name in REQUIRED:
        if not _value(row, name):
            issue(administration.RowIssue(name, "required"))
    flagged = {item.column for item in parsed.errors}
    dates: dict[str, date | None] = {}
    for name in ("student_birthdate", "service_date", "due_date"):
        raw = _value(row, name)
        dates[name] = families.parse_birthdate(raw) if raw else None
        if raw and dates[name] is None:
            issue(administration.RowIssue(name, "invalid_date"))
            flagged.add(name)
    amount = _value(row, "amount")
    if amount and _AMOUNT.fullmatch(amount) is None:
        issue(administration.RowIssue("amount", "invalid_amount"))
        flagged.add("amount")
    given, family = (_value(row, name) for name in families.STUDENT_KEY_COLUMNS[:2])
    birthdate = dates["student_birthdate"]
    if given and family and birthdate is not None:
        parsed.student_key = (given, family, birthdate)
    data: dict[str, Any] = {
        "student_id": _PLACEHOLDER,
        "description": _value(row, "description"),
        "amount": amount,
        "service_date": dates["service_date"],
        "due_date": dates["due_date"],
    }
    try:
        body = ManualChargeCreate.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            column = str(error["loc"][0]) if error["loc"] else None
            if column is None or column in flagged:
                continue
            flagged.add(column)
            issue(administration.RowIssue(column, _issue_code(column, error["type"])))
        return parsed
    if parsed.errors:
        return parsed
    if body.service_date > today:
        issue(administration.RowIssue("service_date", "future_date"))
    if (body.due_date - today).days > DUE_HORIZON_DAYS:
        issue(administration.RowIssue("due_date", "date_out_of_range"))
    if not parsed.errors:
        parsed.body = body
    return parsed


@dataclass
class _Checked:
    """A row after every staging check; a ready row carries its draft."""

    result: administration.RowResult
    draft: ManualChargeDraft | None = None


def _error(issues: Sequence[administration.RowIssue]) -> _Checked:
    return _Checked(administration.RowResult("error", errors=tuple(issues)))


async def _imported(
    session: AsyncSession, keys: Sequence[tuple[uuid.UUID, date, str]]
) -> list[tuple[bool, str]]:
    """Per key: whether an imported charge has it, and the description in
    the database's lower case (one statement)."""
    if not keys:
        return []
    rows = await session.execute(
        _IMPORTED_SQL,
        {
            "s": [key[0] for key in keys],
            "d": [key[1] for key in keys],
            "t": [key[2] for key in keys],
        },
    )
    found = {row.i: (bool(row.taken), str(row.lowered)) for row in rows}
    return [found[index + 1] for index in range(len(keys))]


async def check_rows(
    session: AsyncSession,
    rows: Sequence[Mapping[str, str]],
    ref: administration.ImportRowRef | None = None,
) -> list[_Checked]:
    """Every staging check of ``rows`` in a fixed number of statements (C2).
    A ready row carries its :class:`ManualChargeDraft` (with the import
    reference ``ref`` when given)."""
    today = await administration.center_today(session)
    parsed = [
        _parse({k: v for k, v in row.items() if k in _COLUMN_NAMES}, today)
        for row in rows
    ]
    keys = [item.student_key for item in parsed if item.student_key is not None]
    students = iter(await families.students_by_natural_key(session, keys))
    drafts: list[_Checked] = []
    for item in parsed:
        match = next(students) if item.student_key is not None else None
        errors = list(item.errors)
        if match is not None and match.status != "found":
            errors.insert(0, administration.RowIssue(None, match.status))
        elif match is not None and match.family_id is None:
            errors.insert(0, administration.RowIssue(None, "student_has_no_family"))
        if errors or item.body is None or match is None:
            drafts.append(_error(errors))
            continue
        assert match.student_id is not None and match.family_id is not None  # noqa: S101
        drafts.append(
            _Checked(
                administration.RowResult("ready"),
                ManualChargeDraft(
                    family_id=match.family_id,
                    student_id=match.student_id,
                    description=item.body.description,
                    amount=item.body.amount,
                    service_date=item.body.service_date,
                    due_date=item.body.due_date,
                    import_job_id=None if ref is None else ref.job_id,
                    import_row_number=None if ref is None else ref.row_number,
                ),
            )
        )
    ready = [item.draft for item in drafts if item.draft is not None]
    taken = iter(
        await _imported(
            session,
            [(d.student_id, d.service_date, d.description) for d in ready],
        )
    )
    seen: set[LineKey] = set()
    checked: list[_Checked] = []
    for staged in drafts:
        if staged.draft is None:
            checked.append(staged)
            continue
        exists, lowered = next(taken)
        key = (staged.draft.student_id, staged.draft.service_date, lowered)
        if exists:
            warning = "duplicate_existing"
        elif key in seen:
            warning = "duplicate_in_file"
        else:
            seen.add(key)
            checked.append(staged)
            continue
        checked.append(
            _Checked(
                administration.RowResult(
                    "duplicate", warnings=(administration.RowIssue(None, warning),)
                )
            )
        )
    return checked


@dataclass(frozen=True)
class OpeningBalancesImportKind:
    """See the module docstring."""

    code: str = KIND
    permission: str = BILLING_MANAGE
    columns: Sequence[administration.ImportColumn] = COLUMNS
    batch_size: int = BATCH_SIZE

    def may_import(self, principal: Principal) -> bool:
        return principal.has(BILLING_MANAGE)

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
        [checked] = await check_rows(session, [row_values], ref)
        if checked.result.status == "duplicate":
            return administration.CommitOutcome.skipped("duplicate_existing")
        if checked.draft is None:
            codes = {issue.code for issue in checked.result.errors}
            if "student_has_no_family" in codes:
                return administration.CommitOutcome.failed("student_has_no_family")
            return administration.CommitOutcome.failed("invalid", checked.result.errors)
        try:
            posted = await post_manual_charge(session, checked.draft)
        except StudentNotInFamilyError as exc:
            return administration.CommitOutcome.failed(exc.code)
        except StudentHasNoFamilyError:
            return administration.CommitOutcome.failed("student_has_no_family")
        if not posted.created:
            return administration.CommitOutcome.skipped("duplicate_existing")
        return administration.CommitOutcome.created(posted.charge_id)


OPENING_BALANCES_IMPORT_KIND: Final = OpeningBalancesImportKind()
"""The ``opening_balances`` kind, registered in ``app/wiring.py``."""
