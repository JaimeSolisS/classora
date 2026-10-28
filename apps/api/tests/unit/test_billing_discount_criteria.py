"""Discount criteria and bodies (#95 B1 ``matches``, H2, H3; D3, D6, E3, E5
field codes), pure."""

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.modules.billing.discounts import ChargeFacts, DiscountEligibility, matches
from app.modules.billing.schemas import (
    CustomPriceCreate,
    CustomPriceUpdate,
    DiscountAssignmentCreate,
    DiscountCreate,
    DiscountUpdate,
    discount_rule_errors,
)

SUBJECT = uuid.uuid4()
LEVEL = uuid.uuid4()


def facts(**values) -> ChargeFacts:
    base = {
        "charge_kind": "session",
        "class_type": "individual",
        "subject_id": SUBJECT,
        "education_level_id": LEVEL,
    }
    return ChargeFacts(**{**base, **values})


def criteria(**values) -> DiscountEligibility:
    return DiscountEligibility.model_validate(values)


def test_absent_criteria_match_anything():
    assert matches(criteria(), facts())
    assert matches(criteria(), facts(education_level_id=None))


@pytest.mark.parametrize(
    ("given", "fact", "expected"),
    [
        ({"charge_kinds": ["session"]}, {}, True),
        ({"charge_kinds": ["group_enrollment"]}, {}, False),
        ({"class_types": ["individual", "group"]}, {}, True),
        ({"class_types": ["course"]}, {}, False),
        ({"subject_ids": [str(SUBJECT)]}, {}, True),
        ({"subject_ids": [str(uuid.uuid4())]}, {}, False),
        ({"education_level_ids": [str(LEVEL)]}, {}, True),
        ({"education_level_ids": [str(LEVEL)]}, {"education_level_id": None}, False),
        ({"education_level_ids": [str(uuid.uuid4())]}, {}, False),
    ],
)
def test_each_criterion_alone(given, fact, expected):
    assert matches(criteria(**given), facts(**fact)) is expected


def test_every_criterion_must_hold():
    both = criteria(charge_kinds=["session"], subject_ids=[str(uuid.uuid4())])
    assert not matches(both, facts())
    assert matches(
        criteria(charge_kinds=["session"], subject_ids=[str(SUBJECT)]), facts()
    )


def test_the_sibling_threshold_counts_active_students():
    rule = criteria(min_family_students=3)
    assert not matches(rule, facts(family_active_students=2))
    assert matches(rule, facts(family_active_students=3))
    assert not matches(rule, facts())  # no family / not read


def _codes(model, data) -> set[tuple[str, str]]:
    with pytest.raises(ValidationError) as raised:
        model.model_validate(data)
    return {(str(e["loc"][-1]), e["type"]) for e in raised.value.errors()}


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({"charge_kinds": []}, "criteria_empty_list"),
        ({"subject_ids": []}, "criteria_empty_list"),
        ({"subject_ids": [str(uuid.uuid4()) for _ in range(51)]}, "criteria_too_many"),
        ({"min_family_students": 1}, "min_family_students_out_of_range"),
        ({"min_family_students": 21}, "min_family_students_out_of_range"),
        ({"charge_kinds": ["manual"]}, "literal_error"),
        ({"unknown": 1}, "extra_forbidden"),
    ],
)
def test_the_criteria_schema(data, code):
    assert code in {c for _, c in _codes(DiscountEligibility, data)}


def test_criteria_are_stored_canonical():
    ids = sorted([uuid.uuid4(), uuid.uuid4()])
    stored = criteria(
        charge_kinds=["tuition_period", "session", "session"],
        subject_ids=[str(ids[1]), str(ids[0]), str(ids[1])],
    ).stored()
    assert stored == {
        "charge_kinds": ["session", "tuition_period"],
        "subject_ids": [str(ids[0]), str(ids[1])],
    }


def _discount(**values):
    base = {"kind": "discount", "audience": "all", "name": "Pronto pago"}
    return {**base, **values}


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_discount(), {("amount", "value_required")}),
        (_discount(percent="10", amount="5.00"), {("amount", "value_conflict")}),
        (_discount(percent="0"), {("percent", "percent_out_of_range")}),
        (_discount(percent="100.01"), {("percent", "percent_out_of_range")}),
        (_discount(percent="10.12345"), {("percent", "percent_precision")}),
        (_discount(percent="diez"), {("percent", "percent_invalid")}),
        (_discount(amount="0.00"), {("amount", "amount_not_positive")}),
        (
            _discount(kind="scholarship", percent="10"),
            {("audience", "audience_not_allowed")},
        ),
        (
            _discount(kind="sibling", audience="assigned", percent="10"),
            {
                ("audience", "audience_not_allowed"),
                ("eligibility", "min_family_students_required"),
            },
        ),
        (
            _discount(percent="10", eligibility={"min_family_students": 2}),
            {("eligibility", "min_family_students_not_allowed")},
        ),
        (
            _discount(kind="promotion", percent="10", starts_on="2031-01-01"),
            {("expires_on", "dates_required")},
        ),
        (
            _discount(percent="10", starts_on="2031-02-01", expires_on="2031-01-01"),
            {("expires_on", "dates_order")},
        ),
        (
            _discount(percent="10", starts_on="1999-12-31"),
            {("starts_on", "date_out_of_range")},
        ),
    ],
)
def test_the_create_body_codes(data, expected):
    assert _codes(DiscountCreate, data) == expected


def test_a_valid_create_body_normalises_the_percent():
    body = DiscountCreate.model_validate(
        _discount(kind="sibling", percent="7.5", eligibility={"min_family_students": 2})
    )
    assert body.percent == "7.5000"
    assert body.name == "Pronto pago"


def test_the_update_body_refuses_kind_and_audience():
    assert _codes(DiscountUpdate, {"version": 1, "kind": "discount"}) == {
        ("kind", "field_immutable")
    }
    assert ("audience", "field_immutable") in _codes(
        DiscountUpdate, {"version": 1, "audience": "all", "name": None}
    )


def test_the_row_rules_of_a_patch_result():
    errors = discount_rule_errors(
        kind="promotion",
        audience="all",
        percent=None,
        amount=None,
        starts_on=None,
        expires_on=date(2031, 1, 1),
        eligibility=criteria(),
    )
    assert {(e["path"][-1], e["code"]) for e in errors} == {
        ("amount", "value_required"),
        ("expires_on", "dates_required"),
    }


def test_the_target_rules():
    assert _codes(DiscountAssignmentCreate, {}) == {("family_id", "target_required")}
    both = {"student_id": str(uuid.uuid4()), "family_id": str(uuid.uuid4())}
    assert _codes(DiscountAssignmentCreate, both) == {("family_id", "target_conflict")}
    custom = {
        "service_price_id": str(uuid.uuid4()),
        "effective_from": "2031-03-10",
        "expires_on": "2031-03-09",
        "amount": "-1.00",
    }
    assert _codes(CustomPriceCreate, custom) == {
        ("family_id", "target_required"),
        ("expires_on", "dates_order"),
        ("amount", "amount_negative"),
    }


def test_a_custom_price_patch_changes_only_the_end():
    assert _codes(CustomPriceUpdate, {"version": 1}) == {("expires_on", "missing")}
    assert _codes(
        CustomPriceUpdate, {"version": 1, "expires_on": None, "amount": "1.00"}
    ) == {("amount", "field_immutable")}
    assert CustomPriceUpdate.model_validate({"version": 1, "expires_on": None})
