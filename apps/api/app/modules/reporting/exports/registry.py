"""The export dataset registry (#124 A2, A4).

A dataset is one staff list page made exportable: its ``rows`` call the
page's own service function with the requester's principal, so the record
filter and the field set are the page's (#124 E4). Datasets register with
:func:`register_export_dataset` (``app.wiring`` does, for the API, the CLI and
the worker), like the import kinds: the same object again is a no-op, another
object for a registered code raises.

The request body of a dataset is its ``parameters_model``, a subclass of
:class:`ExportParametersBase` with a ``dataset`` literal, the ``format``, an
ordered ``columns`` subset, an optional ``group_by`` and the dataset's
``filters`` (#124 B2, E7). One model, so a saved setup (M8-08) replays it as
is.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, model_validator

from app.core.authz import Principal
from app.modules.identity.service import STAFF_ROLES
from app.modules.reporting.exports.models import DATASET_CODE_PATTERN

type ColumnKind = Literal[
    "text", "enum", "boolean", "integer", "money", "date", "time", "list"
]
COLUMN_KINDS: Final = get_args(ColumnKind.__value__)

type DateFilter = Literal["required", "optional", "none"]
type ExportFormat = Literal["csv", "xlsx"]

_CODE: Final = re.compile(DATASET_CODE_PATTERN)


class RowLimitExceeded(Exception):  # noqa: N818 - the groomed name (#124 A2)
    """A dataset's rows are more than the run's limit (#124 E6): the job
    fails with ``row_limit_exceeded``, never a truncated file."""


@dataclass(frozen=True)
class ExportColumn:
    """One exported column: its code (the header's catalog key), its kind
    (how a value is written) and whether rows may be grouped by it.
    ``values`` names the ``exports.values.<group>`` labels of an ``enum``."""

    code: str
    kind: ColumnKind
    groupable: bool = False
    values: str | None = None


class ExportParametersBase(BaseModel):
    """What every dataset's parameters model shares (#124 B2, E7).

    A subclass declares ``dataset: Literal[code]``, ``format``,
    ``columns: list[<column literal>] | None`` (at least one), ``group_by:
    <groupable literal> | None`` and ``filters``, and sets
    :attr:`COLUMN_CODES`. Validation fills the default columns (all, in
    catalogue order) and refuses a repeated column or a ``group_by`` that is
    not selected.
    """

    model_config = ConfigDict(extra="forbid")

    COLUMN_CODES: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _normalized(self) -> Self:
        columns = getattr(self, "columns", None)
        if columns is None:
            self.__dict__["columns"] = list(self.COLUMN_CODES)
        elif len(set(columns)) != len(columns):
            raise ValueError("columns must not repeat a column")
        group_by = getattr(self, "group_by", None)
        if group_by is not None and group_by not in self.selected_columns():
            raise ValueError("group_by must be one of the selected columns")
        return self

    def selected_columns(self) -> list[str]:
        """The chosen column codes, in the requested order."""
        return list(getattr(self, "columns", None) or self.COLUMN_CODES)

    def grouping(self) -> str | None:
        value: str | None = getattr(self, "group_by", None)
        return value

    def export_format(self) -> ExportFormat:
        value: ExportFormat = getattr(self, "format")  # noqa: B009
        return value

    def filter_values(self) -> BaseModel:
        value: BaseModel = getattr(self, "filters")  # noqa: B009
        return value


type Rows = Callable[..., Awaitable[list[dict[str, Any]]]]
"""``async rows(session, principal, filters, *, limit, language) ->
list[dict]``: the rows as ``{column code: value}``, in the page's order;
raises :class:`RowLimitExceeded` past ``limit``. ``language`` is the job's,
for the values the page itself shows translated (a system expense
category)."""


@dataclass(frozen=True, eq=False)
class ExportDataset:
    """A registered dataset (#124 A2)."""

    code: str
    permission: str
    personal: bool
    columns: tuple[ExportColumn, ...]
    filters_model: type[BaseModel]
    parameters_model: type[ExportParametersBase]
    rows: Rows = field(repr=False)
    date_filter: DateFilter = "none"
    check_filters: Callable[[Any], None] | None = field(default=None, repr=False)
    """Raises ``422 export_range_invalid`` for a bad date range (after the
    body validated)."""

    def column(self, code: str) -> ExportColumn:
        for column in self.columns:
            if column.code == code:
                return column
        raise KeyError(code)


_datasets: dict[str, ExportDataset] = {}


def _check(dataset: ExportDataset) -> None:
    if not _CODE.fullmatch(dataset.code):
        raise ValueError(f"invalid export dataset code: {dataset.code!r}")
    codes = [column.code for column in dataset.columns]
    if not codes or len(set(codes)) != len(codes):
        raise ValueError(f"{dataset.code}: columns must be non-empty and unique")
    for column in dataset.columns:
        if column.kind not in COLUMN_KINDS:
            raise ValueError(f"{dataset.code}.{column.code}: unknown kind")
        if (column.kind == "enum") != (column.values is not None):
            raise ValueError(f"{dataset.code}.{column.code}: enum values group")
    if tuple(dataset.parameters_model.COLUMN_CODES) != tuple(codes):
        raise ValueError(f"{dataset.code}: the parameters model's columns differ")
    tag = dataset.parameters_model.model_fields["dataset"].annotation
    if get_args(tag) != (dataset.code,):
        raise ValueError(f"{dataset.code}: the parameters model's dataset differs")
    filters = dataset.parameters_model.model_fields["filters"].annotation
    if filters is not dataset.filters_model:
        raise ValueError(f"{dataset.code}: the parameters model's filters differ")


def register_export_dataset(dataset: ExportDataset) -> None:
    """Register ``dataset``. The same object again changes nothing; another
    object for a registered code, or a malformed dataset, raises
    ``ValueError``."""
    _check(dataset)
    registered = _datasets.get(dataset.code)
    if registered is not None and registered is not dataset:
        raise ValueError(f"export dataset already registered: {dataset.code}")
    _datasets[dataset.code] = dataset


def unregister_export_dataset(code: str) -> None:
    """Remove a dataset (tests only); an unknown code changes nothing."""
    _datasets.pop(code, None)


def export_dataset(code: str) -> ExportDataset | None:
    return _datasets.get(code)


def registered_export_datasets() -> tuple[ExportDataset, ...]:
    """Every registered dataset, in registration order."""
    return tuple(_datasets.values())


def may_export(principal: Principal, dataset: ExportDataset) -> bool:
    """A staff role **and** the dataset's permission (#124 A4, E4)."""
    return bool(STAFF_ROLES & principal.roles) and principal.has(dataset.permission)


def exportable_datasets(principal: Principal) -> list[ExportDataset]:
    """The datasets ``principal`` may export, in registration order."""
    return [d for d in registered_export_datasets() if may_export(principal, d)]
