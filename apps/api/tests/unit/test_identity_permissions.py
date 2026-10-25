"""Permission catalog, role-assignment authority and resolution (M2-02 C, G1)."""

import re
from typing import get_args

import pytest

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ACTIVE,
    ADMINISTRATOR,
    DELEGABLE_PERMISSIONS,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    PERMISSION_CODES,
    PERMISSIONS,
    ROLE_CODES,
    ROLE_PERMISSIONS,
    STAFF_ROLES,
    STUDENT,
    TUTOR,
    USER_ADMIN_ROLE_ASSIGN,
    USER_STATES,
    may_assign_role,
    resolve_permissions,
)
from app.modules.identity.schemas import PermissionCode, RoleCode

CODE_PATTERN = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")

# The approved C1 table (owner decision E2 (a), 2026-10-18).
EXPECTED = {
    "user.view": {OWNER, ADMINISTRATOR},
    "user.manage": {OWNER, ADMINISTRATOR},
    "user.invite": {OWNER, ADMINISTRATOR},
    "user.role.assign": {OWNER, ADMINISTRATOR},
    "user.admin_role.assign": {OWNER},
    "user.owner_role.assign": {OWNER},
    "user.delegation.manage": {OWNER},
    "user.password_reset.issue": {OWNER, ADMINISTRATOR},
    "registration.review": {OWNER, ADMINISTRATOR},
    "audit.view": {OWNER, ADMINISTRATOR},
    "catalog.manage": {OWNER, ADMINISTRATOR},
    "settings.manage": {OWNER, ADMINISTRATOR},
    "student.view": set(ROLE_CODES),
    "student.manage": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR},
    "guardian.view": {
        OWNER,
        ADMINISTRATOR,
        ACADEMIC_COORDINATOR,
        FINANCE_STAFF,
        GUARDIAN,
    },
    "family.manage": {OWNER, ADMINISTRATOR},
    "consent.record": {OWNER, ADMINISTRATOR, STUDENT, GUARDIAN},
    "payroll.finalize": {OWNER},
    "schedule.override": {OWNER},
    "finance.refund.approve": {OWNER},
    # Owner decision #43 E1 (a): no Finance Staff, Student or Guardian.
    "tutor.view": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, TUTOR},
    "tutor.manage": {OWNER, ADMINISTRATOR},
    "qualification.review": {OWNER, ADMINISTRATOR},
    # #48 A1 (ADR-0005 D9): no Tutor, Student or Guardian.
    "schedule.view": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF},
    "schedule.manage": {OWNER, ADMINISTRATOR},
    # #51 E1 (a) (ADR-0005 D9): no Finance, Tutor, Student or Guardian.
    "schedule.assign": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR},
    # Owner decision #64 E3 (a): no Tutor, Student or Guardian.
    "course.view": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF},
    "course.manage": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR},
    # Owner decision #76 E1 (a): no Finance, Tutor, Student or Guardian.
    "attendance.correct": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR},
    # Owner decision #77 E3 (a): no Finance, Tutor, Student or Guardian.
    "teaching_hours.approve": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR},
    # Owner decision #79 E4 (a): no Finance, Tutor, Student or Guardian.
    "academic_note.review": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR},
    # Owner-delegated decision #80 E5 (a): no Finance, Tutor, Student or
    # Guardian.
    "evaluation.manage": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR},
    # Owner-delegated decision #81 E4 (a): no Finance, Tutor, Student or
    # Guardian (tutors write through the plan record policy).
    "learning_plan.manage": {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR},
}


# --- C1, C2: catalog invariants ----------------------------------------------------


def test_catalog_matches_the_approved_table():
    by_permission = {
        code: {role for role, codes in ROLE_PERMISSIONS.items() if code in codes}
        for code in PERMISSION_CODES
    }

    assert by_permission == EXPECTED
    assert len(PERMISSIONS) == 33
    assert sum(len(codes) for codes in ROLE_PERMISSIONS.values()) == 84


def test_every_code_has_the_dotted_form():
    for permission in PERMISSIONS:
        assert CODE_PATTERN.fullmatch(permission.code), permission.code


def test_catalog_codes_are_unique():
    assert len(PERMISSION_CODES) == len(PERMISSIONS)


def test_every_mapped_code_is_in_the_catalog():
    for codes in ROLE_PERMISSIONS.values():
        assert codes <= PERMISSION_CODES


def test_owner_holds_every_permission():
    assert ROLE_PERMISSIONS[OWNER] == PERMISSION_CODES


def test_delegable_set_is_exactly_adr_0007():
    assert DELEGABLE_PERMISSIONS == {
        "payroll.finalize",
        "schedule.override",
        "finance.refund.approve",
        "user.admin_role.assign",
    }


def test_delegation_and_owner_assignment_are_not_delegable():
    assert "user.delegation.manage" not in DELEGABLE_PERMISSIONS
    assert "user.owner_role.assign" not in DELEGABLE_PERMISSIONS


def test_all_seven_roles_are_present():
    assert set(ROLE_CODES) == {
        "owner",
        "administrator",
        "academic_coordinator",
        "finance_staff",
        "tutor",
        "student",
        "guardian",
    }
    assert set(ROLE_PERMISSIONS) == set(ROLE_CODES)


def test_staff_roles():
    assert STAFF_ROLES == {
        "owner",
        "administrator",
        "academic_coordinator",
        "finance_staff",
    }


def test_user_states():
    assert USER_STATES == ("pending", "active", "suspended", "archived")


# --- C3: role-assignment authority -------------------------------------------------

# Which roles each actor role may assign (ADR-0007, D4).
ASSIGNABLE_BY = {
    OWNER: set(ROLE_CODES),
    ADMINISTRATOR: {ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR, STUDENT, GUARDIAN},
    ACADEMIC_COORDINATOR: set(),
    FINANCE_STAFF: set(),
    TUTOR: set(),
    STUDENT: set(),
    GUARDIAN: set(),
}


@pytest.mark.parametrize("actor", ROLE_CODES)
@pytest.mark.parametrize("role", ROLE_CODES)
def test_may_assign_role_for_each_actor_role(actor, role):
    expected = role in ASSIGNABLE_BY[actor]

    assert may_assign_role(ROLE_PERMISSIONS[actor], role) is expected


@pytest.mark.parametrize("role", ROLE_CODES)
def test_administrator_with_the_admin_role_delegation(role):
    permissions = ROLE_PERMISSIONS[ADMINISTRATOR] | {USER_ADMIN_ROLE_ASSIGN}

    assert may_assign_role(permissions, role) is (role != OWNER)


def test_unknown_role_is_never_assignable():
    assert may_assign_role(PERMISSION_CODES, "superuser") is False


def test_no_permissions_assign_nothing():
    assert not any(may_assign_role(frozenset(), role) for role in ROLE_CODES)


# --- G1: resolution ---------------------------------------------------------------


@pytest.mark.parametrize("role", ROLE_CODES)
def test_single_role(role):
    assert resolve_permissions(ACTIVE, [role], []) == ROLE_PERMISSIONS[role]


@pytest.mark.parametrize(
    "roles",
    [(TUTOR, GUARDIAN), (FINANCE_STAFF, ACADEMIC_COORDINATOR), (STUDENT, TUTOR)],
)
def test_multiple_roles_are_the_union(roles):
    expected = frozenset().union(*(ROLE_PERMISSIONS[r] for r in roles))

    assert resolve_permissions(ACTIVE, roles, []) == expected


def test_tutor_and_guardian_union_is_explicit():
    assert resolve_permissions(ACTIVE, [TUTOR, GUARDIAN], []) == {
        "student.view",
        "guardian.view",
        "consent.record",
        "tutor.view",
    }


def test_delegation_to_an_administrator_counts():
    result = resolve_permissions(
        ACTIVE, [ADMINISTRATOR], ["payroll.finalize", USER_ADMIN_ROLE_ASSIGN]
    )

    assert result == ROLE_PERMISSIONS[ADMINISTRATOR] | {
        "payroll.finalize",
        USER_ADMIN_ROLE_ASSIGN,
    }


@pytest.mark.parametrize("roles", [[], [TUTOR], [FINANCE_STAFF, GUARDIAN]])
def test_delegation_without_the_administrator_role_does_not_count(roles):
    result = resolve_permissions(ACTIVE, roles, ["payroll.finalize"])

    assert "payroll.finalize" not in result
    assert result == frozenset().union(*(ROLE_PERMISSIONS[r] for r in roles))


def test_non_delegable_delegation_does_not_count():
    result = resolve_permissions(
        ACTIVE, [ADMINISTRATOR], ["user.delegation.manage", "user.owner_role.assign"]
    )

    assert result == ROLE_PERMISSIONS[ADMINISTRATOR]


def test_unknown_codes_are_dropped():
    mapping = {TUTOR: {"student.view", "retired.permission"}}

    result = resolve_permissions(
        ACTIVE, [TUTOR, "retired_role"], ["retired.delegable"], role_permissions=mapping
    )

    assert result == {"student.view"}


@pytest.mark.parametrize("state", ["pending", "suspended", "archived", "unknown"])
def test_non_active_states_have_no_permissions(state):
    assert resolve_permissions(state, [OWNER], []) == frozenset()
    assert (
        resolve_permissions(state, [ADMINISTRATOR], ["payroll.finalize"]) == frozenset()
    )


# --- #28 F3: the SessionUser enums list exactly the catalog --------------------


def test_session_user_enums_match_the_catalog():
    roles = get_args(RoleCode.__value__)
    permissions = get_args(PermissionCode.__value__)
    assert set(roles) == set(ROLE_CODES)
    assert set(permissions) == PERMISSION_CODES
    # Sorted, so the OpenAPI document and the generated unions are stable.
    assert list(roles) == sorted(roles)
    assert list(permissions) == sorted(permissions)
