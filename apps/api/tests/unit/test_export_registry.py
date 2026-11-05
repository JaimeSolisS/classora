"""The export dataset registry, the parameters and who may export (#124 A2,
A4, B2, E7, G1).

The pipeline (request, worker, download) runs against PostgreSQL in
``tests/integration/test_exports_*.py``.
"""

import uuid
from typing import Any, ClassVar, Literal, get_args

import pytest
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.core.authz import Principal
from app.main import build_app
from app.modules.administration import service as administration
from app.modules.identity.permissions import ROLE_PERMISSIONS
from app.modules.reporting.exports import datasets
from app.modules.reporting.exports.datasets import (
    BUILT_IN_DATASETS,
    CLASS_SESSIONS_DATASET,
    EXPENSES_DATASET,
    STUDENTS_DATASET,
    ClassSessionsColumn,
    ClassSessionsGroupable,
    ExpensesColumn,
    ExpensesGroupable,
    ExportParameters,
    StudentsColumn,
    StudentsGroupable,
)
from app.modules.reporting.exports.registry import (
    ExportColumn,
    ExportDataset,
    ExportParametersBase,
    export_dataset,
    exportable_datasets,
    may_export,
    register_export_dataset,
    registered_export_datasets,
    unregister_export_dataset,
)
from app.modules.reporting.exports.service import (
    EXPORT_FAILED,
    EXPORT_READY,
    register_export_wiring,
)

PARAMETERS = TypeAdapter(ExportParameters)


class FakeFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FakeParameters(ExportParametersBase):
    COLUMN_CODES: ClassVar[tuple[str, ...]] = ("name", "count")

    dataset: Literal["fake_rows"]
    format: Literal["csv", "xlsx"]
    columns: list[Literal["name", "count"]] | None = Field(default=None, min_length=1)
    group_by: Literal["name"] | None = None
    filters: FakeFilters = Field(default_factory=FakeFilters)


async def _rows(*_: Any, **__: Any) -> list[dict[str, Any]]:
    return []


def fake(code: str = "fake_rows", **change: Any) -> ExportDataset:
    made: dict[str, Any] = {
        "code": code,
        "permission": "expense.view",
        "personal": False,
        "columns": (
            ExportColumn("name", "text", True),
            ExportColumn("count", "integer"),
        ),
        "filters_model": FakeFilters,
        "parameters_model": FakeParameters,
        "rows": _rows,
    }
    made.update(change)
    return ExportDataset(**made)


@pytest.fixture
def registered_fake():
    dataset = fake()
    register_export_dataset(dataset)
    yield dataset
    unregister_export_dataset(dataset.code)


def principal(*roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=uuid.uuid4(), roles=frozenset(roles), permissions=frozenset(permissions)
    )


# --- A2: the registry -----------------------------------------------------------------


def test_registering_the_same_dataset_again_changes_nothing(registered_fake):
    register_export_dataset(registered_fake)

    assert export_dataset("fake_rows") is registered_fake
    assert registered_export_datasets()[-1] is registered_fake


def test_another_dataset_for_a_registered_code_raises(registered_fake):
    with pytest.raises(ValueError, match="already registered"):
        register_export_dataset(fake())


@pytest.mark.parametrize("code", ["ab", "Fake", "fake-rows", "x" * 33])
def test_a_malformed_code_raises(code):
    with pytest.raises(ValueError, match="invalid export dataset code"):
        register_export_dataset(fake(code))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"columns": ()}, "non-empty and unique"),
        (
            {"columns": (ExportColumn("name", "text"), ExportColumn("name", "text"))},
            "non-empty and unique",
        ),
        (
            {
                "columns": (
                    ExportColumn("name", "enum"),
                    ExportColumn("count", "integer"),
                )
            },
            "enum",
        ),
        ({"columns": (ExportColumn("name", "text"),)}, "columns differ"),
        ({"filters_model": BaseModel}, "filters differ"),
    ],
)
def test_a_malformed_dataset_raises(change, message):
    with pytest.raises(ValueError, match=message):
        register_export_dataset(fake(**change))


def test_the_parameters_model_must_name_the_dataset():
    with pytest.raises(ValueError, match="dataset differs"):
        register_export_dataset(fake("other_rows"))


def test_unregistering_an_unknown_code_changes_nothing():
    unregister_export_dataset("no_such_dataset")

    assert export_dataset("no_such_dataset") is None


def test_the_app_registers_the_three_datasets_in_order():
    build_app()

    codes = [d.code for d in registered_export_datasets()]
    assert codes[:3] == ["students", "class_sessions", "expenses"]
    assert [export_dataset(c) for c in codes[:3]] == list(BUILT_IN_DATASETS)


def test_registering_the_wiring_again_changes_nothing():
    register_export_wiring()
    register_export_wiring()

    assert export_dataset("students") is STUDENTS_DATASET


# --- A3: the three datasets' declarations ---------------------------------------------


@pytest.mark.parametrize(
    ("dataset", "columns", "groupable", "permission", "personal", "date_filter"),
    [
        (
            STUDENTS_DATASET,
            StudentsColumn,
            StudentsGroupable,
            "student.view",
            True,
            "none",
        ),
        (
            CLASS_SESSIONS_DATASET,
            ClassSessionsColumn,
            ClassSessionsGroupable,
            "schedule.view",
            True,
            "required",
        ),
        (
            EXPENSES_DATASET,
            ExpensesColumn,
            ExpensesGroupable,
            "expense.view",
            False,
            "optional",
        ),
    ],
)
def test_each_dataset_declares_its_page_permission_columns_and_groupings(
    dataset, columns, groupable, permission, personal, date_filter
):
    codes = tuple(c.code for c in dataset.columns)

    assert get_args(columns.__value__) == codes
    assert dataset.parameters_model.COLUMN_CODES == codes
    assert get_args(groupable.__value__) == tuple(
        c.code for c in dataset.columns if c.groupable
    )
    assert dataset.permission == permission
    assert dataset.personal is personal
    assert dataset.date_filter == date_filter


def test_the_students_columns_hold_no_birthdate_or_contact():
    codes = {c.code for c in STUDENTS_DATASET.columns}

    assert codes == {
        "family_names",
        "given_names",
        "is_active",
        "is_minor",
        "school_grade",
        "education_level",
    }


# --- A4: who may export ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("owner", {"students", "class_sessions", "expenses"}),
        ("administrator", {"students", "class_sessions"}),
        ("academic_coordinator", {"students", "class_sessions"}),
        ("finance_staff", {"students", "class_sessions", "expenses"}),
        ("tutor", set()),
        ("student", set()),
        ("guardian", set()),
    ],
)
def test_may_export_needs_a_staff_role_and_the_page_permission(role, allowed):
    who = principal(role)

    assert {d.code for d in BUILT_IN_DATASETS if may_export(who, d)} == allowed


def test_a_permission_without_a_staff_role_is_not_enough():
    who = Principal(
        user_id=uuid.uuid4(),
        roles=frozenset({"tutor"}),
        permissions=frozenset({"student.view", "schedule.view", "expense.view"}),
    )

    assert not any(may_export(who, d) for d in BUILT_IN_DATASETS)


def test_exportable_datasets_keep_registry_order():
    build_app()

    assert [d.code for d in exportable_datasets(principal("owner"))][:3] == [
        "students",
        "class_sessions",
        "expenses",
    ]


# --- B2, E7: the parameters -----------------------------------------------------------


def test_the_default_columns_are_all_in_catalogue_order():
    parsed = PARAMETERS.validate_python({"dataset": "expenses", "format": "csv"})

    assert parsed.selected_columns() == list(
        EXPENSES_DATASET.parameters_model.COLUMN_CODES
    )
    assert parsed.model_dump(mode="json", by_alias=True)["columns"] == list(
        EXPENSES_DATASET.parameters_model.COLUMN_CODES
    )
    assert parsed.grouping() is None


def test_columns_keep_the_requested_order():
    parsed = PARAMETERS.validate_python(
        {
            "dataset": "students",
            "format": "xlsx",
            "columns": ["given_names", "family_names"],
            "group_by": None,
        }
    )

    assert parsed.selected_columns() == ["given_names", "family_names"]
    assert parsed.export_format() == "xlsx"


@pytest.mark.parametrize(
    "body",
    [
        {"dataset": "payments", "format": "csv"},
        {"dataset": "expenses", "format": "pdf"},
        {"dataset": "expenses", "format": "csv", "columns": []},
        {"dataset": "expenses", "format": "csv", "columns": ["amount", "amount"]},
        {"dataset": "expenses", "format": "csv", "columns": ["salary"]},
        {"dataset": "expenses", "format": "csv", "group_by": "amount"},
        {
            "dataset": "expenses",
            "format": "csv",
            "columns": ["amount"],
            "group_by": "status",
        },
        {"dataset": "expenses", "format": "csv", "extra": 1},
        {"dataset": "expenses", "format": "csv", "filters": {"q": "x"}},
        {"dataset": "class_sessions", "format": "csv"},
        {
            "dataset": "class_sessions",
            "format": "csv",
            "filters": {"from": "2026-01-01"},
        },
        {"dataset": "students", "format": "csv", "filters": {"status": "gone"}},
    ],
)
def test_a_bad_body_is_a_validation_error(body):
    with pytest.raises(ValidationError):
        PARAMETERS.validate_python(body)


def test_group_by_must_be_groupable_and_selected():
    parsed = PARAMETERS.validate_python(
        {
            "dataset": "class_sessions",
            "format": "csv",
            "columns": ["date", "tutor"],
            "group_by": "tutor",
            "filters": {"from": "2026-01-01", "to": "2026-01-31"},
        }
    )

    assert parsed.grouping() == "tutor"
    assert parsed.model_dump(mode="json", by_alias=True)["filters"]["from"] == (
        "2026-01-01"
    )


@pytest.mark.parametrize(
    ("dataset", "filters", "field"),
    [
        ("class_sessions", {"from": "2026-02-01", "to": "2026-01-31"}, "to"),
        ("class_sessions", {"from": "2026-01-01", "to": "2027-01-02"}, "from"),
        (
            "expenses",
            {"incurred_from": "2026-02-01", "incurred_to": "2026-01-31"},
            "incurred_to",
        ),
        (
            "expenses",
            {"incurred_from": "2025-01-01", "incurred_to": "2026-01-02"},
            "incurred_from",
        ),
    ],
)
def test_a_bad_range_is_export_range_invalid(dataset, filters, field):
    parsed = PARAMETERS.validate_python(
        {"dataset": dataset, "format": "csv", "filters": filters}
    )
    check = export_dataset_or_built_in(dataset).check_filters
    assert check is not None

    with pytest.raises(Exception) as caught:
        check(parsed.filter_values())

    error = caught.value
    assert getattr(error, "code", None) == "export_range_invalid"
    assert error.errors == [  # type: ignore[attr-defined]
        {
            "path": ["body", "filters", field],
            "code": "export_range_invalid",
            "message": error.errors[0]["message"],  # type: ignore[attr-defined]
        }
    ]


@pytest.mark.parametrize(
    ("dataset", "filters"),
    [
        ("class_sessions", {"from": "2026-01-01", "to": "2027-01-01"}),
        ("class_sessions", {"from": "2026-01-01", "to": "2026-01-01"}),
        ("expenses", {"incurred_from": "2026-01-01"}),
        ("expenses", {"incurred_from": "2025-01-01", "incurred_to": "2026-01-01"}),
    ],
)
def test_a_range_of_up_to_366_days_is_valid(dataset, filters):
    parsed = PARAMETERS.validate_python(
        {"dataset": dataset, "format": "csv", "filters": filters}
    )
    check = export_dataset_or_built_in(dataset).check_filters
    assert check is not None

    check(parsed.filter_values())


def export_dataset_or_built_in(code: str) -> ExportDataset:
    return {d.code: d for d in BUILT_IN_DATASETS}[code]


def test_the_session_windows_cover_the_local_days_in_45_day_steps():
    from datetime import date, timedelta
    from zoneinfo import ZoneInfo

    windows = datasets._session_windows(
        date(2026, 1, 1), date(2026, 12, 31), ZoneInfo("America/Mexico_City")
    )

    assert windows[0][0].isoformat() == "2026-01-01T06:00:00+00:00"
    assert windows[-1][1].isoformat() == "2027-01-01T06:00:00+00:00"
    assert all(stop - start <= timedelta(days=45) for start, stop in windows)
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:], strict=False))
    assert len(windows) == 9


# --- G1: the notification types -------------------------------------------------------


def test_the_two_notification_types_are_optional_for_staff():
    build_app()

    for code in (EXPORT_READY, EXPORT_FAILED):
        info = administration.notification_type_info(code)
        assert info is not None
        assert info.mandatory is False
        assert info.recipient_roles == {
            "owner",
            "administrator",
            "academic_coordinator",
            "finance_staff",
        }


def test_the_notification_params_hold_ids_and_codes_only():
    build_app()

    assert administration.validated_params(
        EXPORT_READY,
        {"export_id": "x", "dataset": "students", "format": "csv", "row_count": 3},
    ) == {"export_id": "x", "dataset": "students", "format": "csv", "row_count": 3}
    with pytest.raises(ValueError):
        administration.validated_params(
            EXPORT_FAILED,
            {
                "export_id": "x",
                "dataset": "students",
                "error_code": "error",
                "name": "A",
            },
        )
