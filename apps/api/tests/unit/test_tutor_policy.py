"""The tutor profile policies (#43 B2, B3, D2): pure functions, no I/O."""

import uuid

import pytest
from pydantic import ValidationError

from app.core.authz import Principal
from app.modules.identity import permissions as identity
from app.modules.staff import permissions as policy
from app.modules.staff.permissions import (
    ALL_FIELDS,
    NO_FIELDS,
    SELF_FIELDS,
    can_view_tutor,
    editable_fields,
    sees_every_tutor,
)
from app.modules.staff.schemas import TutorUpdate

SELF = uuid.UUID("00000000-0000-4000-8000-00000000000a")
OTHER = uuid.UUID("00000000-0000-4000-8000-00000000000b")
ROLES = identity.ROLE_CODES


def principal(*roles: str, extra: frozenset[str] = frozenset()) -> Principal:
    permissions: set[str] = set(extra)
    for role in roles:
        permissions |= identity.ROLE_PERMISSIONS[role]
    return Principal(
        user_id=SELF, roles=frozenset(roles), permissions=frozenset(permissions)
    )


def test_the_codes_match_the_identity_catalog():
    assert policy.ACADEMIC_COORDINATOR == identity.ACADEMIC_COORDINATOR
    assert policy.TUTOR == identity.TUTOR
    assert policy.TUTOR_VIEW == identity.TUTOR_VIEW
    assert policy.TUTOR_MANAGE == identity.TUTOR_MANAGE


def test_view_and_manage_holders_follow_owner_decision_e1():
    holders = {
        code: {r for r in ROLES if code in identity.ROLE_PERMISSIONS[r]}
        for code in (identity.TUTOR_VIEW, identity.TUTOR_MANAGE)
    }

    assert holders[identity.TUTOR_VIEW] == {
        "owner",
        "administrator",
        "academic_coordinator",
        "tutor",
    }
    assert holders[identity.TUTOR_MANAGE] == {"owner", "administrator"}
    assert identity.TUTOR_VIEW not in identity.DELEGABLE_PERMISSIONS
    assert identity.TUTOR_MANAGE not in identity.DELEGABLE_PERMISSIONS


@pytest.mark.parametrize(
    ("roles", "sees_other"),
    [
        (("owner",), True),
        (("administrator",), True),
        (("academic_coordinator",), True),
        (("tutor",), False),
        (("finance_staff",), False),
        (("student",), False),
        (("guardian",), False),
        # Multi-role: the union.
        (("tutor", "administrator"), True),
        (("tutor", "academic_coordinator"), True),
        (("tutor", "finance_staff"), False),
        (("tutor", "guardian"), False),
    ],
)
def test_who_sees_another_tutor(roles, sees_other):
    caller = principal(*roles)

    assert can_view_tutor(caller, OTHER) is sees_other
    assert sees_every_tutor(caller) is sees_other


@pytest.mark.parametrize("role", ["tutor", "academic_coordinator", "administrator"])
def test_a_holder_of_tutor_view_sees_themselves(role):
    assert can_view_tutor(principal(role), SELF) is True


@pytest.mark.parametrize("role", ["finance_staff", "student", "guardian"])
def test_without_tutor_view_nobody_is_visible_not_even_themselves(role):
    assert can_view_tutor(principal(role), SELF) is False
    assert editable_fields(principal(role), SELF) == NO_FIELDS


def test_the_coordinator_role_needs_tutor_view():
    # The role alone (a hand-built principal without the permission) is not
    # enough.
    caller = Principal(
        user_id=SELF, roles=frozenset({"academic_coordinator"}), permissions=frozenset()
    )

    assert can_view_tutor(caller, OTHER) is False


def test_tutor_manage_alone_sees_and_edits_everyone():
    # D2: the policy reads tutor.manage, not a role.
    caller = Principal(
        user_id=SELF,
        roles=frozenset({"tutor"}),
        permissions=frozenset({"tutor.view", "tutor.manage"}),
    )

    assert can_view_tutor(caller, OTHER) is True
    assert editable_fields(caller, OTHER) == ALL_FIELDS


@pytest.mark.parametrize("role", ["owner", "administrator"])
def test_managers_edit_every_field_of_any_tutor(role):
    assert editable_fields(principal(role), OTHER) == ALL_FIELDS
    assert editable_fields(principal(role), SELF) == ALL_FIELDS


def test_a_manager_who_may_not_manage_the_account_edits_only_the_experience():
    # D1: an Administrator and an Owner who is also a tutor.
    fields = editable_fields(
        principal("administrator"), OTHER, may_manage_account=False
    )

    assert fields == {"professional_experience"}


def test_the_account_rule_never_limits_the_own_profile():
    assert (
        editable_fields(principal("administrator"), SELF, may_manage_account=False)
        == ALL_FIELDS
    )


def test_a_tutor_edits_their_own_contact_and_experience_only():
    assert editable_fields(principal("tutor"), SELF) == SELF_FIELDS
    assert "display_name" not in SELF_FIELDS
    assert editable_fields(principal("tutor"), OTHER) == NO_FIELDS


def test_a_coordinator_views_but_edits_nobody_else():
    assert editable_fields(principal("academic_coordinator"), OTHER) == NO_FIELDS


def test_a_coordinator_who_is_also_a_tutor_edits_their_own_profile():
    caller = principal("academic_coordinator", "tutor")

    assert editable_fields(caller, SELF) == SELF_FIELDS
    assert editable_fields(caller, OTHER) == NO_FIELDS


# --- The update body (C3) ---------------------------------------------------------


def test_the_update_body_trims_and_clears():
    body = TutorUpdate.model_validate(
        {
            "version": 0,
            "email": "  ",
            "phone": "",
            "professional_experience": "  Ten years of maths.\n",
        }
    )

    assert body.email is None
    assert body.phone is None
    assert body.professional_experience == "Ten years of maths."
    assert body.sent_fields() == {"email", "phone", "professional_experience"}


def test_blank_experience_is_none():
    body = TutorUpdate.model_validate({"version": 1, "professional_experience": " "})

    assert body.professional_experience is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"version": -1},
        {"version": 0, "display_name": None},
        {"version": 0, "display_name": "  "},
        {"version": 0, "email": "not-an-email"},
        {"version": 0, "phone": "12"},
        {"version": 0, "professional_experience": "x" * 4001},
        {"version": 0, "username": "other"},
    ],
)
def test_invalid_update_bodies(body):
    with pytest.raises(ValidationError):
        TutorUpdate.model_validate(body)


def test_4000_characters_are_accepted():
    body = TutorUpdate.model_validate(
        {"version": 0, "professional_experience": "x" * 4000}
    )

    assert body.professional_experience is not None
    assert len(body.professional_experience) == 4000
