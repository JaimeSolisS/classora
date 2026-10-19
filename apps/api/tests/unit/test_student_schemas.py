"""Student request bodies (#33 C1, D1): trimming, "" → null, 1-5 contacts,
unknown fields, null rules, and the contact patterns shared with #31."""

import re

import pytest
from pydantic import ValidationError

from app.modules.families import models as family_models
from app.modules.families import schemas
from app.modules.families.schemas import StudentCreate, StudentUpdate
from app.modules.identity import models as identity_models
from app.modules.identity import schemas as identity_schemas


def contact(**overrides):
    return {
        "name": "Ana",
        "relationship": "Madre",
        "phone": "55 1234 5678",
        **overrides,
    }


def create(**overrides) -> StudentCreate:
    body = {
        "given_names": "José",
        "family_names": "García",
        "birthdate": "2012-05-04",
        "emergency_contacts": [contact()],
        **overrides,
    }
    return StudentCreate.model_validate(body)


def test_the_patterns_are_the_31_ones():
    assert family_models.EMAIL_PATTERN == identity_models.EMAIL_PATTERN
    assert family_models.PHONE_PATTERN == identity_models.PHONE_PATTERN
    assert schemas.EMAIL_REGEX == identity_schemas.EMAIL_REGEX
    assert schemas.PHONE_REGEX == identity_schemas.PHONE_REGEX


@pytest.mark.parametrize(
    ("value", "valid"),
    [("a@b.co", True), ("ab", False), ("a b@c.d", False), ("a@b", False)],
)
def test_email_regex(value, valid):
    assert (re.fullmatch(schemas.EMAIL_REGEX, value) is not None) is valid


def test_strings_are_trimmed_and_blank_optionals_become_null():
    body = create(
        given_names="  José María ",
        email="  ",
        phone="",
        school_name=" Escuela ",
        school_grade="",
        emergency_contacts=[
            contact(name=" Ana ", relationship=" Tía ", phone=" 55 1234 5678 ")
        ],
    )

    assert body.given_names == "José María"
    assert body.email is None
    assert body.phone is None
    assert body.school_name == "Escuela"
    assert body.school_grade is None
    [item] = body.emergency_contacts
    assert (item.name, item.relationship, item.phone) == ("Ana", "Tía", "55 1234 5678")


@pytest.mark.parametrize(
    "overrides",
    [
        {"emergency_contacts": []},
        {"emergency_contacts": [contact()] * 6},
        {"given_names": " "},
        {"family_names": "x" * 101},
        {"birthdate": "1899-12-31"},
        {"birthdate": "04/05/2012"},
        {"birthdate": 20120504},
        {"birthdate": "2012-02-30"},
        {"email": "nope"},
        {"phone": "12"},
        {"school_name": "x" * 201},
        {"school_grade": "x" * 51},
        {"education_level_id": "not-a-uuid"},
        {"emergency_contacts": [contact(relationship="x" * 51)]},
        {"emergency_contacts": [contact(name="x" * 201)]},
        {"emergency_contacts": [contact(phone="")]},
        {"emergency_contacts": [contact(extra="x")]},
        {"emergency_contacts": [contact(id="00000000-0000-4000-8000-000000000001")]},
        {"version": 1},
        {"is_active": True},
    ],
    ids=str,
)
def test_invalid_create_bodies(overrides):
    with pytest.raises(ValidationError):
        create(**overrides)


def test_five_contacts_are_allowed_in_order():
    body = create(emergency_contacts=[contact(name=f"C{n}") for n in range(5)])

    assert [c.name for c in body.emergency_contacts] == [f"C{n}" for n in range(5)]


def test_an_update_needs_a_version_and_keeps_unsent_fields_unset():
    with pytest.raises(ValidationError):
        StudentUpdate.model_validate({})
    body = StudentUpdate.model_validate({"version": 3, "email": ""})

    assert body.model_fields_set == {"version", "email"}
    assert body.email is None


@pytest.mark.parametrize(
    "field",
    ["given_names", "family_names", "birthdate", "is_active", "emergency_contacts"],
)
def test_null_is_refused_for_required_fields(field):
    with pytest.raises(ValidationError):
        StudentUpdate.model_validate({"version": 1, field: None})


@pytest.mark.parametrize(
    "field", ["email", "phone", "school_name", "school_grade", "education_level_id"]
)
def test_null_clears_optional_fields(field):
    body = StudentUpdate.model_validate({"version": 1, field: None})

    assert field in body.model_fields_set
    assert getattr(body, field) is None


def test_update_contacts_may_carry_ids():
    body = StudentUpdate.model_validate(
        {
            "version": 1,
            "emergency_contacts": [
                contact(id="00000000-0000-4000-8000-000000000001"),
                contact(),
            ],
        }
    )

    assert [str(c.id) if c.id else None for c in body.emergency_contacts] == [
        "00000000-0000-4000-8000-000000000001",
        None,
    ]


@pytest.mark.parametrize(
    ("q", "valid"),
    [
        ("a", True),
        ("  jose  ", True),
        ("a b c d e", True),
        ("a b c d e f", False),
        ("", False),
        ("x" * 101, False),
    ],
)
def test_search_text(q, valid):
    from pydantic import TypeAdapter

    adapter = TypeAdapter(schemas.SearchText)
    if valid:
        assert adapter.validate_python(q) == q.strip()
    else:
        with pytest.raises(ValidationError):
            adapter.validate_python(q)
