"""The sensitive-action registry (#26 B; ADR-0007 D10)."""

from collections import Counter
from datetime import timedelta

import pytest

from app.core.reauth import (
    ACTION_CODE_PATTERN,
    REAUTH_WINDOW,
    SENSITIVE_ACTIONS,
    actions_binding,
)
from app.modules.identity.permissions import PERMISSION_CODES

# One per ADR-0007 D10 item (permission changes are covered by the first two).
EXPECTED_ACTIONS = {
    "user.roles.change": "#170",
    "user.delegations.change": "#170",
    "user.invitation.staff_role": "#29",
    "user.suspend_or_archive": "#31",
    "user.password_reset.issue": "#30",
    "payroll.finalize": "M7-07",
    "finance.refund_or_reversal": "M6-15",
    "finance.correction": "M6-15",
    "data.retention_or_deletion": "M8-12",
    "settings.payment_methods_or_time_zone": "#37 (time zone), M8-04 (payment methods)",
    "data.personal_export": "M8-07",
}

EXPECTED_BOUND = {
    "user.roles.change": {
        "user.role.assign",
        "user.admin_role.assign",
        "user.owner_role.assign",
    },
    "user.delegations.change": {"user.delegation.manage"},
    "user.password_reset.issue": {"user.password_reset.issue"},
    "payroll.finalize": {"payroll.finalize"},
    "finance.refund_or_reversal": {"finance.refund.approve"},
}


def test_the_registry_has_exactly_the_eleven_actions():
    assert {code: a.applied_by for code, a in SENSITIVE_ACTIONS.items()} == (
        EXPECTED_ACTIONS
    )


def test_every_code_matches_the_pattern_and_its_key():
    for code, action in SENSITIVE_ACTIONS.items():
        assert ACTION_CODE_PATTERN.fullmatch(code), code
        assert action.code == code


def test_every_entry_has_a_description_an_adr_item_and_an_owner():
    for action in SENSITIVE_ACTIONS.values():
        assert action.description.strip()
        assert action.adr_item.strip()
        assert action.applied_by.strip()


def test_the_bound_permissions():
    bound = {
        code: set(a.bound_permissions)
        for code, a in SENSITIVE_ACTIONS.items()
        if a.bound_permissions
    }

    assert bound == EXPECTED_BOUND


def test_no_permission_is_bound_to_two_actions():
    counts = Counter(p for a in SENSITIVE_ACTIONS.values() for p in a.bound_permissions)

    assert [p for p, n in counts.items() if n > 1] == []


def test_every_bound_permission_is_in_the_catalog():
    bound = {p for a in SENSITIVE_ACTIONS.values() for p in a.bound_permissions}

    assert bound <= PERMISSION_CODES


def test_permissions_that_also_cover_other_work_are_not_bound():
    for permission in ("user.manage", "user.invite", "user.view"):
        assert actions_binding(permission) == frozenset()
    assert actions_binding("user.admin_role.assign") == {"user.roles.change"}


def test_the_window_is_five_minutes():
    assert REAUTH_WINDOW == timedelta(minutes=5)


def test_the_registry_is_read_only():
    with pytest.raises(TypeError):
        SENSITIVE_ACTIONS["x.y"] = SENSITIVE_ACTIONS["finance.correction"]  # type: ignore[index]
