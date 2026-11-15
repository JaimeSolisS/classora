"""Guardian request bodies (#34 C1, C3, D1): trimming, "" → null, unknown
fields, null rules and the relationship enum."""

import uuid
from typing import get_args

import pytest
from pydantic import ValidationError

from app.modules.families import schemas
from app.modules.families.constants import GUARDIAN_RELATIONSHIPS
from app.modules.families.schemas import (
    GuardianCreate,
    GuardianUpdate,
    StudentGuardianCreate,
)

USER = uuid.UUID("00000000-0000-4000-8000-000000000001")


def test_the_relationship_enum_is_the_constant():
    assert get_args(schemas.GuardianRelationship.__value__) == GUARDIAN_RELATIONSHIPS


def test_strings_are_trimmed_and_blank_optionals_become_null():
    body = GuardianCreate.model_validate(
        {
            "given_names": "  Laura ",
            "family_names": " Ñúñez",
            "email": "  ",
            "phone": "",
        }
    )

    assert (body.given_names, body.family_names) == ("Laura", "Ñúñez")
    assert (body.email, body.phone, body.user_id) == (None, None, None)


def test_a_full_create_body():
    body = GuardianCreate.model_validate(
        {
            "given_names": "Laura",
            "family_names": "Ñúñez",
            "email": "laura@example.com",
            "phone": "+52 00 1234 5678",
            "user_id": str(USER),
        }
    )

    assert body.user_id == USER


@pytest.mark.parametrize(
    "overrides",
    [
        {"given_names": "   "},
        {"family_names": ""},
        {"given_names": "x" * 101},
        {"email": "not-an-email"},
        {"phone": "12"},
        {"user_id": "not-a-uuid"},
        {"extra": 1},
    ],
    ids=str,
)
def test_invalid_create_bodies(overrides):
    with pytest.raises(ValidationError):
        GuardianCreate.model_validate(
            {"given_names": "Laura", "family_names": "Ñúñez", **overrides}
        )


def test_an_update_needs_a_version_and_keeps_unsent_fields_unset():
    with pytest.raises(ValidationError):
        GuardianUpdate.model_validate({"given_names": "Laura"})
    body = GuardianUpdate.model_validate({"version": 1, "phone": "55 1234 5678"})

    assert body.model_fields_set == {"version", "phone"}


@pytest.mark.parametrize("field", ["given_names", "family_names", "is_active"])
def test_null_is_refused_for_required_fields(field):
    with pytest.raises(ValidationError):
        GuardianUpdate.model_validate({"version": 1, field: None})


@pytest.mark.parametrize("field", ["email", "phone", "user_id"])
def test_null_clears_optional_fields(field):
    body = GuardianUpdate.model_validate({"version": 1, field: None})

    assert field in body.model_fields_set
    assert getattr(body, field) is None


def test_update_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        GuardianUpdate.model_validate({"version": 1, "account": None})


@pytest.mark.parametrize("relationship", GUARDIAN_RELATIONSHIPS)
def test_every_relationship_code_is_accepted(relationship):
    body = StudentGuardianCreate.model_validate(
        {"guardian_id": str(USER), "relationship": relationship}
    )

    assert body.relationship == relationship


@pytest.mark.parametrize(
    "body",
    [
        {"guardian_id": str(USER), "relationship": "aunt"},
        {"guardian_id": str(USER), "relationship": "Mother"},
        {"guardian_id": str(USER)},
        {"relationship": "mother"},
        {"guardian_id": str(USER), "relationship": "mother", "extra": 1},
    ],
    ids=str,
)
def test_invalid_link_bodies(body):
    with pytest.raises(ValidationError):
        StudentGuardianCreate.model_validate(body)
