"""Saved export setup bodies and the stored parameters (#125 A1, B3, B4, E5)."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.modules.identity.service import STAFF_ROLES
from app.modules.reporting.exports.datasets import ExportParameters
from app.modules.reporting.setups.models import SHARED_ROLES, ReportSetup
from app.modules.reporting.setups.schemas import ReportSetupCreate, ReportSetupUpdate
from app.modules.reporting.setups.service import stored_parameters

PARAMETERS = TypeAdapter(ExportParameters)
EXPENSES: dict[str, Any] = {"dataset": "expenses", "format": "csv", "filters": {}}


def create(**change: Any) -> ReportSetupCreate:
    return ReportSetupCreate.model_validate(
        {"name": "Gastos", "parameters": EXPENSES, **change}
    )


def test_the_shared_roles_are_the_staff_roles():
    assert set(SHARED_ROLES) == set(STAFF_ROLES)
    assert len(SHARED_ROLES) == 4


def test_the_name_is_trimmed_and_bounded():
    assert create(name="  Gastos del mes ").name == "Gastos del mes"
    assert create(name="x" * 80).name == "x" * 80
    assert create(name=f"  {'x' * 80}  ").name == "x" * 80
    for name in ("", "   ", "x" * 81):
        with pytest.raises(ValidationError):
            create(name=name)


@pytest.mark.parametrize("role", SHARED_ROLES)
def test_a_staff_role_may_be_shared_with(role):
    assert create(shared_role=role).shared_role == role


@pytest.mark.parametrize("role", ["tutor", "student", "guardian", "Owner", ""])
def test_other_roles_may_not(role):
    with pytest.raises(ValidationError):
        create(shared_role=role)


def test_private_by_default_and_extra_fields_refused():
    assert create().shared_role is None
    with pytest.raises(ValidationError):
        create(extra=1)


@pytest.mark.parametrize(
    "parameters",
    [
        {"dataset": "expenses"},
        {"dataset": "payroll", "format": "csv"},
        {**EXPENSES, "columns": []},
        {**EXPENSES, "columns": ["amount", "amount"]},
        {**EXPENSES, "columns": ["amount"], "group_by": "vendor"},
        {**EXPENSES, "group_by": "amount"},
        {"dataset": "class_sessions", "format": "csv", "filters": {}},
    ],
)
def test_the_parameters_are_the_export_rules(parameters):
    with pytest.raises(ValidationError):
        create(parameters=parameters)


def test_stored_parameters_keep_omitted_fields_omitted():
    body = {
        "dataset": "class_sessions",
        "format": "xlsx",
        "filters": {"from": "2026-01-01", "to": "2026-01-31"},
    }
    parameters = create(parameters=body).parameters

    stored = stored_parameters(parameters)

    assert stored == body
    # The default columns are filled in the model, not in what is stored.
    assert parameters.selected_columns()[0] == "date"
    assert PARAMETERS.validate_python(stored) == parameters


def test_stored_parameters_keep_the_order_grouping_and_filters():
    body = {
        "dataset": "expenses",
        "format": "csv",
        "columns": ["vendor", "amount"],
        "group_by": "vendor",
        "filters": {"status": ["paid"], "incurred_from": "2026-01-01"},
    }

    stored = stored_parameters(create(parameters=body).parameters)

    assert stored == body
    assert stored["dataset"] == "expenses"


def test_an_update_may_not_null_the_name_or_the_parameters():
    assert ReportSetupUpdate.model_validate({"version": 1}).model_fields_set == {
        "version"
    }
    unshare = ReportSetupUpdate.model_validate({"version": 1, "shared_role": None})
    assert "shared_role" in unshare.model_fields_set
    for field in ("name", "parameters"):
        with pytest.raises(ValidationError):
            ReportSetupUpdate.model_validate({"version": 1, field: None})
    with pytest.raises(ValidationError):
        ReportSetupUpdate.model_validate({"name": "x"})
    with pytest.raises(ValidationError):
        ReportSetupUpdate.model_validate({"version": 0})


def test_the_model_is_versioned_and_audited():
    from app.core.audit import Audited
    from app.core.models import VersionedMixin

    assert issubclass(ReportSetup, Audited)
    assert issubclass(ReportSetup, VersionedMixin)
    assert ReportSetup.__table__.c.created_by_user_id.foreign_keys
