"""The import kinds registry (#122 A1, A2).

Each importable record type is an :class:`ImportKind` that its owning module
implements and the composition roots register with
:func:`register_import_kind` (``app/wiring.py``), so ``administration`` never
imports the module that owns the records (#40 E4). Re-registering the same
object changes nothing; a different object for a registered code raises
``ValueError``.

The kind decides everything that depends on its records:

- ``code`` (``^[a-z_]{3,32}$``), ``permission`` (the permission manual
  creation needs, for documentation and the list filter) and
  :meth:`ImportKind.may_import` (the full check, for example
  ``user.invite`` plus "may assign ``tutor``");
- ``columns``: the CSV columns it reads, with their required flag;
- :meth:`ImportKind.prepare`: validation and deduplication of a whole file
  (or, at commit, of one batch) **in bulk**, in a fixed number of
  statements. ``principal`` is the importer while staging and ``None`` for
  the worker's re-check at commit;
- ``batch_size`` (default :data:`DEFAULT_BATCH_SIZE`): ready rows per
  commit transaction;
- :meth:`ImportKind.commit_row`: create one record from one row's staged
  values, inside the pipeline's savepoint, with the row reference
  :class:`ImportRowRef` (#123 E7). A kind that needs a process setting (the
  scheduling horizon, #123 B4) reads the run's settings with
  :func:`run_settings`.

Codes in :class:`RowIssue` are stable strings and never carry a value.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PERMISSION_CODE_PATTERN, Principal
from app.core.config import Settings, get_settings
from app.modules.administration.imports.models import KIND_CODE_PATTERN

_KIND_CODE: Final = re.compile(KIND_CODE_PATTERN)

RUN_SETTINGS_KEY: Final = "administration.import_run_settings"
"""The ``session.info`` key under which the import task puts the run's
:class:`~app.core.config.Settings` for each batch's Unit of Work."""

DEFAULT_BATCH_SIZE: Final = 100
"""Ready rows committed per transaction unless the kind says otherwise."""

MAX_COLUMNS: Final = 30
"""The most columns a file (and a kind) may have (#122 B2)."""

IMPORT_ROW_CODES: Final[frozenset[str]] = frozenset(
    {
        # #122 B4 (and the D1 commit-time code), shared by every kind.
        "required",
        "invalid",
        "too_long",
        "invalid_date",
        "future_date",
        "invalid_email",
        "invalid_phone",
        "education_level_unknown",
        "education_level_unavailable",
        "username_invalid",
        "username_taken",
        "duplicate_existing",
        "duplicate_in_file",
        # #123: both new kinds name a student by the natural key.
        "student_unknown",
        "student_ambiguous",
        "student_inactive",
        # #123 B2, B4: schedules.
        "subject_unknown",
        "subject_inactive",
        "tutor_unknown",
        "tutor_inactive",
        "classroom_unknown",
        "classroom_inactive",
        "time_format",
        "duration_out_of_range",
        "weekdays_empty",
        "weekday_out_of_range",
        "weekdays_duplicate",
        "interval_out_of_range",
        "until_and_count",
        "count_out_of_range",
        "until_before_start",
        "no_occurrences",
        "starts_on_in_past",
        "crosses_local_date",
        "scheduling_conflict",
        "series_not_offerable",
        # The conflict codes a failed ``scheduling_conflict`` row lists
        # (#123 B4: the ADR-0005 hard and soft codes, ``scheduling.conflicts.
        # CODE_ORDER``, cross-checked in tests/unit/test_imports_registry.py).
        "tutor_overlap",
        "room_overlap",
        "student_overlap",
        "room_capacity_exceeded",
        "tutor_unavailable",
        "room_unavailable",
        "center_closed",
        "outside_operating_hours",
        "tutor_not_qualified",
        "group_capacity_exceeded",
        "notice_window",
        # #123 C2, C3: opening balances.
        "student_has_no_family",
        "student_not_in_family",
        "invalid_amount",
        "amount_not_positive",
        "date_out_of_range",
    }
)
"""Every code a :class:`RowIssue` or a failed or skipped
:class:`CommitOutcome` may carry (#130 B1): the web labels each one
(``packages/api-client/i18n-inventory.json``). Both refuse any other code
with ``ValueError``; a site whose code comes from elsewhere (another
module's error) passes it through :func:`declared_row_code`."""

FALLBACK_ROW_CODE: Final = "invalid"
"""The declared code for a refusal whose own code has no row label."""


def declared_row_code(code: str) -> str:
    """``code`` when it is declared, else :data:`FALLBACK_ROW_CODE`."""
    return code if code in IMPORT_ROW_CODES else FALLBACK_ROW_CODE


def _check_row_code(code: str) -> None:
    if code not in IMPORT_ROW_CODES:
        raise ValueError(f"undeclared import row code: {code!r}")


type RowStatus = Literal["ready", "duplicate", "error"]
type OutcomeStatus = Literal["created", "skipped", "failed"]


@dataclass(frozen=True)
class ImportColumn:
    """One CSV column a kind reads (matched trimmed and case-insensitive)."""

    name: str
    required: bool = False


@dataclass(frozen=True)
class RowIssue:
    """An error or warning on a row: the column (``None`` for the whole row)
    and a stable code, never a value."""

    column: str | None
    code: str

    def __post_init__(self) -> None:
        _check_row_code(self.code)


@dataclass(frozen=True)
class RowResult:
    """A row's staging verdict: ``ready`` (will be imported), ``duplicate``
    (will be skipped; carries the warning ``duplicate_existing`` or
    ``duplicate_in_file``) or ``error`` (never imported)."""

    status: RowStatus
    errors: tuple[RowIssue, ...] = ()
    warnings: tuple[RowIssue, ...] = ()


@dataclass(frozen=True)
class ImportRowRef:
    """The staged row a commit creates a record from (#123 E7): records that
    keep an import reference store it."""

    job_id: uuid.UUID
    row_number: int


@dataclass(frozen=True)
class CommitOutcome:
    """What :meth:`ImportKind.commit_row` did with one row.

    - ``created`` with the new record's ``record_id``;
    - ``skipped`` with a ``code`` (``duplicate_existing``);
    - ``failed`` with a ``code`` and optionally ``errors`` (codes only, no
      values), which the pipeline stores on the row after the code.

    A ``skipped`` or ``failed`` outcome rolls the row's savepoint back.
    """

    status: OutcomeStatus
    record_id: uuid.UUID | None = None
    code: str | None = None
    errors: tuple[RowIssue, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.code is not None:
            _check_row_code(self.code)

    @classmethod
    def created(cls, record_id: uuid.UUID) -> CommitOutcome:
        return cls(status="created", record_id=record_id)

    @classmethod
    def skipped(cls, code: str) -> CommitOutcome:
        return cls(status="skipped", code=code)

    @classmethod
    def failed(cls, code: str, errors: Sequence[RowIssue] = ()) -> CommitOutcome:
        return cls(status="failed", code=code, errors=tuple(errors))


class ImportKind(Protocol):
    """One importable record type (see the module docstring)."""

    @property
    def code(self) -> str: ...

    @property
    def permission(self) -> str: ...

    @property
    def columns(self) -> Sequence[ImportColumn]: ...

    @property
    def batch_size(self) -> int: ...

    def may_import(self, principal: Principal) -> bool: ...

    async def prepare(
        self,
        session: AsyncSession,
        rows: Sequence[Mapping[str, str]],
        principal: Principal | None,
    ) -> list[RowResult]:
        """One result per row, in order. ``rows`` hold the kind's columns
        present in the file (trimmed text, ``""`` when blank)."""
        ...

    async def commit_row(
        self,
        session: AsyncSession,
        row_values: Mapping[str, str],
        ref: ImportRowRef,
        actor_user_id: uuid.UUID,
    ) -> CommitOutcome: ...


_kinds: dict[str, ImportKind] = {}


def register_import_kind(kind: ImportKind) -> None:
    """Register ``kind`` under its ``code``.

    Raises ``ValueError`` for a malformed code or permission, a column list
    that is empty, repeated or over :data:`MAX_COLUMNS`, a ``batch_size``
    below 1, or another object already registered under the code. Registering
    the same object again changes nothing.
    """
    if _KIND_CODE.fullmatch(kind.code) is None:
        raise ValueError(f"not an import kind code: {kind.code!r}")
    if PERMISSION_CODE_PATTERN.fullmatch(kind.permission) is None:
        raise ValueError(f"not a permission code: {kind.permission!r}")
    names = [column.name for column in kind.columns]
    if not names or len(names) > MAX_COLUMNS or len(set(names)) != len(names):
        raise ValueError(f"invalid columns for import kind {kind.code!r}")
    if any(name != name.strip().lower() or not name for name in names):
        raise ValueError(f"column names must be trimmed lower case: {kind.code!r}")
    if kind.batch_size < 1:
        raise ValueError(f"batch_size must be at least 1: {kind.code!r}")
    registered = _kinds.get(kind.code)
    if registered is not None and registered is not kind:
        raise ValueError(f"import kind already registered: {kind.code!r}")
    _kinds[kind.code] = kind


def unregister_import_kind(code: str) -> None:
    """Remove a registered kind (tests only); an unknown code is ignored."""
    _kinds.pop(code, None)


def registered_import_kinds() -> frozenset[str]:
    """The registered kind codes."""
    return frozenset(_kinds)


def import_kind(code: str) -> ImportKind | None:
    """The kind registered under ``code``, or ``None``."""
    return _kinds.get(code)


def importable_kinds(principal: Principal) -> list[str]:
    """The registered kinds ``principal`` may import, sorted."""
    return sorted(code for code, kind in _kinds.items() if kind.may_import(principal))


def run_settings(session: AsyncSession) -> Settings:
    """The settings of the import run committing in ``session`` (#123 B4:
    the scheduling horizon), else the process settings."""
    settings = session.info.get(RUN_SETTINGS_KEY)
    if isinstance(settings, Settings):
        return settings
    return get_settings()
