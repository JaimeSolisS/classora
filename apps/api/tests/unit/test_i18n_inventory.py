"""The generated i18n inventory (#130 B1-B3; E2, E8).

``app.core.i18n_inventory`` lists every code the web must label; ``make
api-client`` writes it to ``packages/api-client/i18n-inventory.json`` and the
web's ``src/i18n/inventory.test.ts`` requires an es and en label for each.
"""

import json
import textwrap
from pathlib import Path

import pytest

from app.core import i18n_inventory
from app.core.errors import AppError
from app.modules.administration.imports.registry import (
    FALLBACK_ROW_CODE,
    IMPORT_ROW_CODES,
    CommitOutcome,
    RowIssue,
    declared_row_code,
)
from app.modules.scheduling.conflicts import CODE_ORDER

COMMITTED = (
    Path(__file__).resolve().parents[4] / "packages/api-client/i18n-inventory.json"
)
AREAS = [
    "field_codes",
    "import_row_codes",
    "job_tasks",
    "notification_types",
    "problem_codes",
]


@pytest.fixture(scope="module")
def inventory() -> dict[str, list[str]]:
    return i18n_inventory.build_inventory()


class _TestOnlyError(AppError):
    """A test's own subclass: never part of the inventory."""

    status = 418
    code = "test_only_code"


def test_every_area_is_sorted_unique_and_non_empty(inventory):
    assert sorted(inventory) == AREAS
    for area, values in inventory.items():
        assert values, area
        assert values == sorted(set(values)), area


def test_samples_of_each_area_are_listed(inventory):
    assert {"version_conflict", "series_not_offerable", "role_unknown"} <= set(
        inventory["problem_codes"]
    )
    # The core's own codes (CORE_PROBLEM_CODES) and the local-only demo code.
    assert {"validation_error", "not_found", "method_not_allowed"} <= set(
        inventory["problem_codes"]
    )
    assert "demo_job_already_queued" in inventory["problem_codes"]
    assert {"birthdate_in_future", "date_range_invalid", "money_invalid"} <= set(
        inventory["field_codes"]
    )
    assert "administration.approval_assigned" in inventory["notification_types"]
    assert "billing:post_session_charges" in inventory["job_tasks"]
    assert "student_ambiguous" in inventory["import_row_codes"]


def test_abstract_bases_local_tasks_and_test_classes_are_left_out(inventory):
    assert "password_invalid" not in inventory["problem_codes"]
    assert "test_only_code" not in inventory["problem_codes"]
    assert not [
        task
        for task in inventory["job_tasks"]
        if task.startswith(("builtin:", "demo:", "procrastinate."))
    ]
    assert not [t for t in inventory["notification_types"] if t.startswith("dev.")]


def test_the_inventory_equals_the_committed_file(inventory):
    committed = COMMITTED.read_text(encoding="utf-8")
    assert i18n_inventory.render_inventory(inventory) == committed, (
        "packages/api-client/i18n-inventory.json is stale: run `make api-client`"
    )


def test_main_writes_the_rendered_file(tmp_path, inventory):
    out = tmp_path / "inventory.json"
    assert i18n_inventory.main([str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert json.loads(text) == inventory
    assert i18n_inventory.main([]) == 2


# --- Field-code scan --------------------------------------------------------------


def test_the_dynamic_field_code_sites_are_pinned():
    scan = i18n_inventory.field_code_scan()
    assert scan.dynamic_sites == set(i18n_inventory.DYNAMIC_FIELD_CODE_SITES), (
        "A field-error code the scan can't resolve: add the site to "
        "DYNAMIC_FIELD_CODE_SITES with the codes it can send.\n"
        + "\n".join(
            f"{site}: {sorted(expressions)}"
            for site, expressions in sorted(scan.dynamic_expressions.items())
        )
    )


def test_dynamic_site_codes_are_in_the_inventory(inventory):
    for source in i18n_inventory.DYNAMIC_FIELD_CODE_SITES.values():
        assert set(source()) <= set(inventory["field_codes"])


SAMPLE_SOURCE = """
from typing import Final
from pydantic_core import PydanticCustomError

LIMIT_CODE: Final = "limit_reached"
_MESSAGES = {"from_map": "A message.", "Not a code": "x"}


def literal():
    return {"path": ["body", "a"], "code": "literal_code", "message": "m"}


def conditional(flag):
    code = "first_code" if flag else "second_code"
    return {"path": ["body", "a"], "code": code, "message": "m"}


def _helper(field, code, message):
    return {"path": ["body", field], "code": code, "message": message}


def caller():
    first = _helper("a", "helper_code", "m")
    return [first, _helper("b", code=LIMIT_CODE, message="m")]


def mapped(code):
    return {"path": ["body"], "code": code, "message": _MESSAGES[code]}


def custom():
    raise PydanticCustomError("custom_code", "A message.")


def factory(*, required):
    def validate(value):
        raise PydanticCustomError(required, "This field is required.")
    return validate


DEFAULT = factory(required="factory_code")


def rule(value):
    if value is None:
        return None
    return "returned_code"


def from_rule(value):
    code = rule(value)
    return {"path": ["body"], "code": code, "message": "m"}


def issue():
    return (["body", "a"], "tuple_code", "Some message.")


def dynamic(exc):
    return {"path": ["body"], "code": exc.reason, "message": "m"}
"""


def test_the_scan_finds_each_construction(tmp_path):
    (tmp_path / "sample.py").write_text(textwrap.dedent(SAMPLE_SOURCE))
    scan = i18n_inventory.field_code_scan(tmp_path)
    assert scan.codes == {
        "literal_code",
        "first_code",
        "second_code",
        "helper_code",
        "limit_reached",
        "from_map",
        "custom_code",
        "factory_code",
        "returned_code",
        "tuple_code",
    }
    assert scan.dynamic_sites == {f"{tmp_path.name}/sample.py:dynamic"}


# --- Import row codes (#130 B1) -----------------------------------------------------


def test_the_row_codes_cover_the_scheduling_conflict_codes():
    assert set(CODE_ORDER) <= IMPORT_ROW_CODES
    assert "scheduling_conflict" in IMPORT_ROW_CODES
    assert FALLBACK_ROW_CODE in IMPORT_ROW_CODES


def test_an_undeclared_row_code_is_refused():
    with pytest.raises(ValueError, match="undeclared import row code"):
        RowIssue(None, "not_a_row_code")
    with pytest.raises(ValueError, match="undeclared import row code"):
        CommitOutcome.failed("not_a_row_code")
    with pytest.raises(ValueError, match="undeclared import row code"):
        CommitOutcome.skipped("not_a_row_code")
    assert RowIssue("a", "required").code == "required"
    assert CommitOutcome.failed("invalid").code == "invalid"


def test_declared_row_code_falls_back_to_invalid():
    assert declared_row_code("student_unknown") == "student_unknown"
    assert declared_row_code("version_conflict") == FALLBACK_ROW_CODE
