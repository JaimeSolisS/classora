"""User administration rules (#31 B1, B2, E2, F1, D3, I3): who may manage whom,
``allowed_actions``, the state-route table and the request/response schemas."""

import re
import uuid
from typing import get_args

import pytest
from pydantic import ValidationError

from app.modules.identity.models import EMAIL_PATTERN, PHONE_PATTERN
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ACTIVE,
    ADMINISTRATOR,
    ARCHIVED,
    DELEGABLE_PERMISSIONS,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PENDING,
    ROLE_CODES,
    ROLE_PERMISSIONS,
    STUDENT,
    SUSPENDED,
    TUTOR,
    USER_ADMIN_ROLE_ASSIGN,
    USER_STATES,
    UserActions,
    allowed_user_actions,
    may_manage_user,
    resolve_permissions,
)
from app.modules.identity.schemas import (
    EMAIL_REGEX,
    PHONE_REGEX,
    DelegablePermissionCode,
    UserDetail,
    UserListItem,
    UserState,
    UserUpdate,
)
from app.modules.identity.user_admin import STATE_ACTIONS, _escape_like

ACTOR = uuid.UUID("00000000-0000-4000-8000-00000000000a")
TARGET = uuid.UUID("00000000-0000-4000-8000-00000000000b")

# Who may manage a single-role target (owner decision #31 E2 (a)): an Owner
# everyone, an Administrator everyone but Owners and Administrators, the
# other roles nobody (they lack user.manage).
MANAGEABLE_BY = {
    OWNER: set(ROLE_CODES),
    ADMINISTRATOR: {ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN},
    ACADEMIC_COORDINATOR: set(),
    FINANCE_STAFF: set(),
    TUTOR: set(),
    STUDENT: set(),
    GUARDIAN: set(),
}


def permissions_of(*roles: str, delegated: tuple[str, ...] = ()) -> frozenset[str]:
    return resolve_permissions(ACTIVE, roles, delegated)


# --- B1: may_manage_user --------------------------------------------------------------


@pytest.mark.parametrize("actor", ROLE_CODES)
@pytest.mark.parametrize("target", ROLE_CODES)
def test_each_actor_role_against_each_single_role_target(actor, target):
    allowed = may_manage_user(
        permissions_of(actor), actor_id=ACTOR, target_id=TARGET, target_roles=[target]
    )

    assert allowed is (target in MANAGEABLE_BY[actor])


def test_the_table_covers_every_role():
    assert set(MANAGEABLE_BY) == set(ROLE_CODES)


@pytest.mark.parametrize(("actor", "allowed"), [(OWNER, True), (ADMINISTRATOR, False)])
def test_a_multi_role_target_needs_authority_over_every_role(actor, allowed):
    assert (
        may_manage_user(
            permissions_of(actor),
            actor_id=ACTOR,
            target_id=TARGET,
            target_roles=[TUTOR, ADMINISTRATOR],
        )
        is allowed
    )


def test_an_administrator_manages_a_tutor_who_is_also_a_guardian():
    assert may_manage_user(
        permissions_of(ADMINISTRATOR),
        actor_id=ACTOR,
        target_id=TARGET,
        target_roles=[TUTOR, GUARDIAN],
    )


@pytest.mark.parametrize("actor", ROLE_CODES)
def test_a_roleless_target_is_managed_by_any_user_manage_holder(actor):
    allowed = may_manage_user(
        permissions_of(actor), actor_id=ACTOR, target_id=TARGET, target_roles=[]
    )

    assert allowed is ("user.manage" in ROLE_PERMISSIONS[actor])


def test_a_delegated_administrator_manages_administrators_but_not_owners():
    delegated = permissions_of(ADMINISTRATOR, delegated=(USER_ADMIN_ROLE_ASSIGN,))

    def manages(*roles: str) -> bool:
        return may_manage_user(
            delegated, actor_id=ACTOR, target_id=TARGET, target_roles=roles
        )

    assert manages(ADMINISTRATOR)
    assert manages(ADMINISTRATOR, TUTOR)
    assert not manages(OWNER)
    assert not manages(OWNER, ADMINISTRATOR)


@pytest.mark.parametrize("actor", [OWNER, ADMINISTRATOR])
def test_self_is_always_manageable_by_a_user_manage_holder(actor):
    # Even an Administrator, who could not assign their own role.
    assert may_manage_user(
        permissions_of(actor), actor_id=ACTOR, target_id=ACTOR, target_roles=[actor]
    )


def test_self_without_user_manage_is_not_manageable():
    assert not may_manage_user(
        permissions_of(TUTOR), actor_id=ACTOR, target_id=ACTOR, target_roles=[TUTOR]
    )


# --- E2: allowed_actions --------------------------------------------------------------


def actions(
    *,
    actor_roles: tuple[str, ...] = (OWNER,),
    delegated: tuple[str, ...] = (),
    target_id: uuid.UUID = TARGET,
    state: str = ACTIVE,
    target_roles: tuple[str, ...] = (TUTOR,),
    password_set: bool = True,
) -> UserActions:
    return allowed_user_actions(
        actor_id=ACTOR,
        actor_roles=actor_roles,
        actor_permissions=permissions_of(*actor_roles, delegated=delegated),
        target_id=target_id,
        target_state=state,
        target_roles=target_roles,
        target_password_set=password_set,
    )


@pytest.mark.parametrize(
    ("state", "password_set", "suspend", "archive", "reactivate"),
    [
        (ACTIVE, True, True, True, False),
        (SUSPENDED, True, False, True, True),
        (ARCHIVED, True, False, False, True),
        (ARCHIVED, False, False, False, True),
        (PENDING, False, False, True, False),  # an open invitation
        (PENDING, True, False, False, False),  # a registration request
    ],
)
def test_state_actions_follow_the_state(
    state, password_set, suspend, archive, reactivate
):
    result = actions(state=state, password_set=password_set)

    assert (result.suspend, result.archive, result.reactivate) == (
        suspend,
        archive,
        reactivate,
    )
    assert result.edit is True


@pytest.mark.parametrize("state", USER_STATES)
def test_own_account_edit_only(state):
    result = actions(target_id=ACTOR, state=state, target_roles=(OWNER,))

    assert result == UserActions(
        edit=True,
        suspend=False,
        archive=False,
        reactivate=False,
        reset_password=False,
    )


@pytest.mark.parametrize("state", USER_STATES)
def test_nothing_when_the_caller_may_not_manage_the_target(state):
    result = actions(actor_roles=(ADMINISTRATOR,), state=state, target_roles=(OWNER,))

    assert (result.edit, result.suspend, result.archive, result.reactivate) == (
        False,
        False,
        False,
        False,
    )


@pytest.mark.parametrize(
    ("actor_roles", "delegated", "target_roles", "state", "expected"),
    [
        ((OWNER,), (), (TUTOR,), ACTIVE, True),
        ((OWNER,), (), (ADMINISTRATOR,), ACTIVE, True),
        ((OWNER,), (), (OWNER,), ACTIVE, True),
        ((ADMINISTRATOR,), (), (TUTOR,), ACTIVE, True),
        ((ADMINISTRATOR,), (), (ADMINISTRATOR,), ACTIVE, False),
        ((ADMINISTRATOR,), (), (OWNER,), ACTIVE, False),
        # A delegation does not count (#30 C2: the owner role only).
        ((ADMINISTRATOR,), (USER_ADMIN_ROLE_ASSIGN,), (ADMINISTRATOR,), ACTIVE, False),
        ((OWNER,), (), (TUTOR,), SUSPENDED, False),
        ((OWNER,), (), (TUTOR,), ARCHIVED, False),
        ((OWNER,), (), (TUTOR,), PENDING, False),
        ((TUTOR,), (), (STUDENT,), ACTIVE, False),
    ],
)
def test_reset_password_follows_the_30_rules(
    actor_roles, delegated, target_roles, state, expected
):
    result = actions(
        actor_roles=actor_roles,
        delegated=delegated,
        target_roles=target_roles,
        state=state,
    )

    assert result.reset_password is expected


def test_reset_password_is_false_for_self():
    assert actions(target_id=ACTOR).reset_password is False


def test_a_delegated_administrator_may_suspend_an_administrator():
    result = actions(
        actor_roles=(ADMINISTRATOR,),
        delegated=(USER_ADMIN_ROLE_ASSIGN,),
        target_roles=(ADMINISTRATOR,),
    )

    assert (result.edit, result.suspend, result.archive) == (True, True, True)


# --- G2: the state-route table --------------------------------------------------------


def test_the_state_routes_table():
    assert {
        action: (rule.new_state, rule.from_states, rule.pending_without_password)
        for action, rule in STATE_ACTIONS.items()
    } == {
        "suspend": (SUSPENDED, frozenset({ACTIVE}), False),
        "archive": (ARCHIVED, frozenset({ACTIVE, SUSPENDED}), True),
        "reactivate": (ACTIVE, frozenset({SUSPENDED, ARCHIVED}), False),
    }


# --- D2: LIKE escaping ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("ana", "ana"),
        ("50%", "50/%"),
        ("a_b", "a/_b"),
        ("a/b", "a//b"),
        ("a\\b", "a\\b"),
    ],
)
def test_like_escaping(text, escaped):
    assert _escape_like(text) == escaped


# --- F1: UserUpdate -------------------------------------------------------------------


def test_only_version_is_required():
    body = UserUpdate.model_validate({"version": 3})

    assert body.model_fields_set == {"version"}


@pytest.mark.parametrize("version", [0, -1, None, "x"])
def test_version_must_be_a_positive_integer(version):
    with pytest.raises(ValidationError):
        UserUpdate.model_validate({"version": version})


def test_display_name_is_trimmed():
    assert UserUpdate(version=1, display_name="  Ana  ").display_name == "Ana"


@pytest.mark.parametrize("value", ["", "   ", None, "x" * 201])
def test_display_name_invalid(value):
    with pytest.raises(ValidationError) as caught:
        UserUpdate.model_validate({"version": 1, "display_name": value})

    assert caught.value.errors()[0]["loc"][0] == "display_name"


@pytest.mark.parametrize("field", ["email", "phone"])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_blank_or_null_contact_clears(field, value):
    body = UserUpdate.model_validate({"version": 1, field: value})

    assert getattr(body, field) is None
    assert field in body.model_fields_set


@pytest.mark.parametrize(
    ("field", "value", "stored"),
    [
        ("email", " a@b.co ", "a@b.co"),
        ("phone", "+52 (55) 1234-5678", "+52 (55) 1234-5678"),
        ("phone", "55.1234.5678", "55.1234.5678"),
    ],
)
def test_valid_contact_values(field, value, stored):
    assert getattr(UserUpdate.model_validate({"version": 1, field: value}), field) == (
        stored
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "ab"),
        ("email", "a b@c.d"),
        ("email", "a@b"),
        ("email", "@b.co"),
        ("email", "a@" + "b" * 250 + ".co"),
        ("phone", "12"),
        ("phone", "phone-ext"),
        ("phone", "+52 55 1234 567x"),
        ("phone", "1" * 33),
    ],
)
def test_invalid_contact_values_name_the_field(field, value):
    with pytest.raises(ValidationError) as caught:
        UserUpdate.model_validate({"version": 1, field: value})

    assert caught.value.errors()[0]["loc"][0] == field


SAMPLES = [
    ("email", "a@b.co"),
    ("email", "ana.lopez+x@example.com.mx"),
    ("email", "ab"),
    ("email", "a b@c.d"),
    ("email", "a@b"),
    ("phone", "+52 (55) 1234-5678"),
    ("phone", "5512345678"),
    ("phone", "12"),
    ("phone", "phone-ext"),
    ("phone", "+5"),
]


@pytest.mark.parametrize(("field", "value"), SAMPLES)
def test_the_api_and_database_patterns_agree(field, value):
    database = {"email": EMAIL_PATTERN, "phone": PHONE_PATTERN}[field]
    api = {"email": EMAIL_REGEX, "phone": PHONE_REGEX}[field]
    # PostgreSQL's [:space:] class is \s in Python.
    as_python = database.replace("[:space:]", r"\s")

    assert as_python == api
    assert bool(re.fullmatch(api, value)) is bool(re.fullmatch(as_python, value))


# --- D3, E1, I3: response schemas -----------------------------------------------------


def test_the_list_item_has_no_contact_password_or_delegation_field():
    assert set(UserListItem.model_fields) == {
        "id",
        "username",
        "display_name",
        "state",
        "roles",
        "created_at",
    }


def test_the_detail_fields():
    assert set(UserDetail.model_fields) == set(UserListItem.model_fields) | {
        "email",
        "phone",
        "version",
        "updated_at",
        "delegations",
        "password_set",
        "allowed_actions",
    }


def test_the_state_and_delegation_enums_match_the_catalog():
    assert get_args(UserState.__value__) == USER_STATES
    delegable = get_args(DelegablePermissionCode.__value__)
    assert set(delegable) == DELEGABLE_PERMISSIONS
    assert list(delegable) == sorted(delegable)
