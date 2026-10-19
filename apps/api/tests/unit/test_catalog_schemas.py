"""Catalog request bodies (#32 D1, D2, E1, E2): trim, length, null, extra."""

import pytest
from pydantic import ValidationError

from app.modules.catalog.schemas import (
    EducationLevelCreate,
    EducationLevelUpdate,
    SubjectCreate,
    SubjectUpdate,
)


@pytest.mark.parametrize("model", [EducationLevelCreate, SubjectCreate])
def test_names_are_trimmed_and_inner_spaces_kept(model):
    assert model(name="  Física   II \t").name == "Física   II"


@pytest.mark.parametrize("model", [EducationLevelCreate, SubjectCreate])
@pytest.mark.parametrize("name", ["", "   ", "\t\n", "x" * 101, None])
def test_blank_long_or_null_names_are_rejected(model, name):
    with pytest.raises(ValidationError):
        model(name=name)


def test_a_100_character_name_is_accepted():
    assert len(EducationLevelCreate(name="x" * 100).name) == 100


@pytest.mark.parametrize("sort_order", [-1, 10000, 1.5, "first"])
def test_sort_order_is_0_to_9999(sort_order):
    with pytest.raises(ValidationError):
        EducationLevelCreate(name="Primaria", sort_order=sort_order)


def test_sort_order_is_optional():
    assert EducationLevelCreate(name="Primaria").sort_order is None
    assert EducationLevelCreate(name="Primaria", sort_order=0).sort_order == 0
    assert EducationLevelCreate(name="Primaria", sort_order=9999).sort_order == 9999


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (EducationLevelCreate, {"name": "A", "is_active": True}),
        (EducationLevelCreate, {"name": "A", "seed_key": "elementary"}),
        (SubjectCreate, {"name": "A", "education_level_id": None}),
        (EducationLevelUpdate, {"version": 1, "id": "x"}),
        (SubjectUpdate, {"version": 1, "education_level_id": "x"}),
    ],
)
def test_unknown_fields_are_rejected(model, body):
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        model(**body)


@pytest.mark.parametrize("model", [EducationLevelUpdate, SubjectUpdate])
def test_an_update_needs_a_positive_version(model):
    for body in ({}, {"version": 0}, {"version": None}):
        with pytest.raises(ValidationError):
            model(**body)


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (EducationLevelUpdate, "name"),
        (EducationLevelUpdate, "sort_order"),
        (EducationLevelUpdate, "is_active"),
        (SubjectUpdate, "name"),
        (SubjectUpdate, "is_active"),
    ],
)
def test_null_never_clears_a_field(model, field):
    with pytest.raises(ValidationError, match="cannot be null"):
        model(version=1, **{field: None})


def test_fields_not_sent_are_not_in_the_fields_set():
    body = EducationLevelUpdate(version=2, is_active=False)

    assert body.model_fields_set == {"version", "is_active"}
    assert SubjectUpdate(version=1, name=" Arte ").name == "Arte"
