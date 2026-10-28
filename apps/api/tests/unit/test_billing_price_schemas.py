"""Price catalog bodies, errors and the lookup's argument checks (#89 B3,
B5, B9, C2), without a database."""

import json
import uuid
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.errors import (
    PriceCatalogValidationError,
    PriceEffectiveFromInvalidError,
    PriceVersionInEffectError,
    PriceVersionInUseError,
    ServicePriceExistsError,
)
from app.modules.billing.models import ALLOWED_MODELS
from app.modules.billing.schemas import PriceVersionCreate, ServicePriceCreate

pytestmark = pytest.mark.unit

ID = "00000000-0000-4000-8000-000000000089"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _codes(model: Any, body: dict[str, Any]) -> list[tuple[str, str]]:
    with pytest.raises(ValidationError) as raised:
        model.model_validate_json(json.dumps(body))
    return [(str(e["loc"][-1]), e["type"]) for e in raised.value.errors()]


def _create(**overrides: Any) -> dict[str, Any]:
    body = {
        "class_type": "individual",
        "pricing_model": "per_session",
        "subject_id": ID,
        "effective_from": "2031-03-10",
        "amount": "300.00",
        **overrides,
    }
    return {k: v for k, v in body.items() if v is not ...}


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"education_level_id": ID, "duration_minutes": 15},
        {"duration_minutes": 480, "amount": "0"},
        {"class_type": "group", "group_id": ID},
        {"class_type": "group", "pricing_model": "full_course"},
        {"class_type": "course", "pricing_model": "full_course", "cohort_id": ID},
        {"effective_from": "2000-01-01"},
        {"effective_from": "2099-12-31", "amount": "999999999999.99"},
    ],
)
def test_valid_create_bodies(overrides):
    body = ServicePriceCreate.model_validate_json(json.dumps(_create(**overrides)))

    assert isinstance(body.amount, Money)


def test_every_allowed_combination_is_accepted_or_routed_elsewhere():
    accepted = set()
    for class_type, models in ALLOWED_MODELS.items():
        for model in models:
            try:
                ServicePriceCreate.model_validate_json(
                    json.dumps(_create(class_type=class_type, pricing_model=model))
                )
            except ValidationError:
                continue
            accepted.add((class_type, model))

    assert accepted == {
        ("individual", "per_session"),
        ("group", "per_session"),
        ("group", "full_course"),
        ("course", "full_course"),
    }


def test_all_body_errors_come_in_one_response():
    codes = _codes(
        ServicePriceCreate,
        _create(
            class_type="course",
            pricing_model="per_session",
            cohort_id=ID,
            group_id=ID,
            education_level_id=ID,
            duration_minutes=5,
            effective_from="1999-12-31",
            amount="-1.00",
        ),
    )

    assert codes == [
        ("pricing_model", "pricing_model_not_allowed"),
        ("group_id", "scope_conflict"),
        ("education_level_id", "level_not_allowed"),
        ("duration_minutes", "duration_not_allowed"),
        ("effective_from", "date_out_of_range"),
        ("amount", "amount_negative"),
    ]


def test_an_invalid_class_type_skips_the_rules_that_depend_on_it():
    assert _codes(
        ServicePriceCreate, _create(class_type="tutoring", group_id=ID, cohort_id=ID)
    ) == [("class_type", "literal_error"), ("group_id", "scope_conflict")]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"effective_from": "2031-03-10"}, [("amount", "missing")]),
        ({"amount": None}, [("effective_from", "missing")]),
        (
            {"effective_from": "2100-01-01", "amount": "-0.01"},
            [("effective_from", "date_out_of_range"), ("amount", "amount_negative")],
        ),
        (
            {"effective_from": "2031-03-10", "amount": 10},
            [("amount", "money_invalid")],
        ),
        (
            {"effective_from": "2031-03-10", "amount": None, "note": "x"},
            [("note", "extra_forbidden")],
        ),
    ],
)
def test_version_body_errors(body, expected):
    assert _codes(PriceVersionCreate, body) == expected


def test_a_null_version_amount_is_not_offered():
    body = PriceVersionCreate.model_validate_json(
        '{"effective_from": "2031-03-10", "amount": null}'
    )

    assert body.amount is None
    assert body.effective_from == date(2031, 3, 10)


def test_error_classes():
    exists = ServicePriceExistsError(uuid.UUID(ID))
    assert (exists.status, exists.code, exists.details) == (
        409,
        "service_price_exists",
        {"service_price_id": ID},
    )
    assert ServicePriceExistsError(None).details is None
    invalid = PriceEffectiveFromInvalidError()
    assert (invalid.status, invalid.code) == (422, "price_effective_from_invalid")
    assert invalid.errors == [
        {
            "path": ["body", "effective_from"],
            "code": "price_effective_from_invalid",
            "message": invalid.detail,
        }
    ]
    assert (PriceVersionInEffectError.status, PriceVersionInEffectError.code) == (
        409,
        "price_version_in_effect",
    )
    assert (PriceVersionInUseError.status, PriceVersionInUseError.code) == (
        409,
        "price_version_in_use",
    )
    references = PriceCatalogValidationError(
        [{"path": ["body", "subject_id"], "code": "subject_unknown", "message": "m"}]
    )
    assert (references.status, references.code) == (422, "validation_error")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query",
    [
        {"pricing_model": "package"},
        {"cohort_id": uuid.uuid4()},
        {"group_id": uuid.uuid4()},
        {"class_type": "group", "cohort_id": uuid.uuid4()},
        {"class_type": "course", "group_id": uuid.uuid4()},
        {"class_type": "group", "group_id": uuid.uuid4(), "cohort_id": uuid.uuid4()},
    ],
)
async def test_the_lookup_refuses_programming_errors_before_any_query(query):
    values: dict[str, Any] = {
        "class_type": "individual",
        "pricing_model": "per_session",
        "subject_id": uuid.uuid4(),
        "on": date(2031, 3, 10),
        **query,
    }

    with pytest.raises(ValueError):
        await billing.lookup_price(None, **values)  # type: ignore[arg-type]
