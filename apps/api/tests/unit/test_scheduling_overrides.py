"""Overrides of soft conflicts: the decision, the body rules and the drift
guard of the migration's CHECK (#49 B2, C2, D1, G7)."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.scheduling.conflicts import CODE_ORDER, HARD, SOFT, WARNING, Conflict
from app.modules.scheduling.models import SOFT_CODES_IN_ORDER, ClassSessionOverride
from app.modules.scheduling.overrides import (
    OverrideDecision,
    evaluate_override,
    overridden_codes,
)
from app.modules.scheduling.schemas import ClassSessionCreate, SessionOverride

pytestmark = pytest.mark.unit

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

CLOSED = Conflict("center_closed", {"date": "2031-01-12"})
HOURS = Conflict(
    "outside_operating_hours",
    {"date": "2031-01-12", "opens_at": None, "closes_at": None},
)
UNQUALIFIED = Conflict("tutor_not_qualified", {"subject_id": "s"})
STUDENT = Conflict("student_overlap", {"student_id": "x", "class_session_ids": []})


def override(*codes: str, reason: str = "Acordado con la familia") -> SessionOverride:
    return SessionOverride(codes=list(codes), reason=reason)


# --- C2: the decision -----------------------------------------------------------


def test_no_conflict_is_allowed_with_nothing_overridden():
    assert evaluate_override([], None) == OverrideDecision(allowed=True)
    assert evaluate_override([], override("center_closed")) == OverrideDecision(
        allowed=True
    )


def test_without_an_override_any_conflict_refuses():
    assert evaluate_override([HOURS], None) == OverrideDecision(allowed=False)


def test_a_hard_conflict_always_refuses():
    decision = evaluate_override([STUDENT, HOURS], override("outside_operating_hours"))

    assert decision == OverrideDecision(allowed=False)


def test_an_unlisted_soft_conflict_refuses():
    decision = evaluate_override([HOURS, UNQUALIFIED], override("tutor_not_qualified"))

    assert decision == OverrideDecision(allowed=False)


def test_every_soft_conflict_listed_is_allowed_with_the_computed_conflicts():
    decision = evaluate_override(
        [HOURS, UNQUALIFIED], override("tutor_not_qualified", "outside_operating_hours")
    )

    assert decision == OverrideDecision(allowed=True, overridden=(HOURS, UNQUALIFIED))


def test_listed_codes_that_do_not_occur_are_ignored():
    decision = evaluate_override(
        [HOURS], override("outside_operating_hours", "center_closed", "notice_window")
    )

    assert decision.allowed
    assert decision.overridden == (HOURS,)
    assert overridden_codes(decision.overridden) == ["outside_operating_hours"]


@pytest.mark.parametrize("code", sorted(SOFT))
def test_each_soft_code_can_be_overridden(code):
    # group_capacity_exceeded is unreachable for an individual session (#48
    # D4): it is covered here.
    conflict = Conflict(code, {})

    assert evaluate_override([conflict], override(code)).overridden == (conflict,)


def test_overridden_codes_are_unique_in_order():
    other_day = Conflict("center_closed", {"date": "2031-01-13"})

    assert overridden_codes([CLOSED, other_day, HOURS]) == [
        "center_closed",
        "outside_operating_hours",
    ]


# --- B2: the body ---------------------------------------------------------------


def errors_of(data: dict[str, Any]) -> list[tuple[tuple[Any, ...], str]]:
    with pytest.raises(ValidationError) as caught:
        SessionOverride.model_validate(data)
    return [(error["loc"], error["type"]) for error in caught.value.errors()]


@pytest.mark.parametrize(
    "data",
    [
        {"codes": ["center_closed"]},
        {"codes": ["center_closed"], "reason": None},
        {"codes": ["center_closed"], "reason": ""},
        {"codes": ["center_closed"], "reason": "   \n\t"},
    ],
)
def test_a_missing_or_blank_reason_is_reason_required(data):
    assert errors_of(data) == [(("reason",), "reason_required")]


def test_the_reason_is_trimmed_and_at_most_1000_characters():
    assert override("center_closed", reason="  Acordado  ").reason == "Acordado"
    exactly = "a" * 1000
    assert override("center_closed", reason=f" {exactly} ").reason == exactly
    assert errors_of({"codes": ["center_closed"], "reason": "a" * 1001}) == [
        (("reason",), "reason_too_long")
    ]


@pytest.mark.parametrize("data", [{"reason": "x"}, {"codes": [], "reason": "x"}])
def test_missing_or_empty_codes_are_codes_required(data):
    assert errors_of(data) == [(("codes",), "codes_required")]


def test_a_duplicate_code_is_refused():
    assert errors_of({"codes": ["center_closed", "center_closed"], "reason": "x"}) == [
        (("codes",), "duplicate_code")
    ]


def test_hard_warning_and_unknown_codes_are_refused_on_their_item():
    data = {
        "codes": ["center_closed", "student_overlap", "break_too_short", "nope"],
        "reason": "x",
    }

    assert errors_of(data) == [
        (("codes", 1), "code_not_overridable"),
        (("codes", 2), "code_unknown"),
        (("codes", 3), "code_unknown"),
    ]


@pytest.mark.parametrize("code", sorted(HARD))
def test_every_hard_code_is_not_overridable(code):
    assert errors_of({"codes": [code], "reason": "x"}) == [
        (("codes", 0), "code_not_overridable")
    ]


@pytest.mark.parametrize("code", sorted(WARNING))
def test_every_warning_code_is_unknown(code):
    assert errors_of({"codes": [code], "reason": "x"}) == [
        (("codes", 0), "code_unknown")
    ]


def test_all_seven_soft_codes_fit_and_extra_members_are_refused():
    assert len(override(*SOFT).codes) == 7
    assert errors_of({"codes": ["center_closed"], "reason": "x", "extra": 1}) == [
        (("extra",), "extra_forbidden")
    ]


def test_the_create_body_takes_an_optional_override():
    base = {
        "kind": "individual",
        "student_id": "0190f0f0-0000-7000-8000-000000000001",
        "subject_id": "0190f0f0-0000-7000-8000-000000000002",
        "starts_at": "2031-01-06T10:00:00-06:00",
        "ends_at": "2031-01-06T11:00:00-06:00",
    }

    assert ClassSessionCreate.model_validate(base).override is None
    assert ClassSessionCreate.model_validate(base | {"override": None}).override is None
    body = ClassSessionCreate.model_validate(
        base | {"override": {"codes": ["center_closed"], "reason": " Sí "}}
    )
    assert body.override == SessionOverride(codes=["center_closed"], reason="Sí")


# --- D1: the CHECK drift guard --------------------------------------------------


def _revision() -> ModuleType:
    [path] = VERSIONS.glob("*_scheduling_session_overrides.py")
    spec = importlib.util.spec_from_file_location(f"revision_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_check_lists_exactly_the_soft_codes_in_rule_order():
    revision = _revision()

    assert set(revision.SOFT_CODES) == SOFT
    assert revision.SOFT_CODES == tuple(c for c in CODE_ORDER if c in SOFT)
    assert revision.SOFT_CODES == SOFT_CODES_IN_ORDER


def test_the_model_check_matches_the_migration():
    revision = _revision()
    prefix = "ck_class_session_overrides_"
    model_checks = {
        str(constraint.name).removeprefix(prefix): str(constraint.sqltext)
        for constraint in ClassSessionOverride.__table__.constraints
        if str(constraint.name).startswith(prefix)
    }

    assert model_checks == revision.CHECKS
