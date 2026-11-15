"""The compensation record policies (#105 E5, E6, J1) for every role and for
multi-role users."""

import uuid

import pytest

from app.core.authz import Principal
from app.modules.identity import permissions as identity
from app.modules.identity.permissions import ACTIVE, resolve_permissions
from app.modules.payroll import permissions as policy
from app.modules.payroll.permissions import (
    can_view_compensation,
    can_write_compensation,
)

ME = uuid.uuid4()
OTHER = uuid.uuid4()


def principal(*roles: str, delegations: tuple[str, ...] = ()) -> Principal:
    return Principal(
        user_id=ME,
        roles=frozenset(roles),
        permissions=resolve_permissions(ACTIVE, roles, delegations),
    )


def test_the_codes_match_the_identity_catalog():
    assert policy.OWNER == identity.OWNER
    assert policy.TUTOR == identity.TUTOR
    assert policy.PAYROLL_VIEW == identity.PAYROLL_VIEW


@pytest.mark.parametrize(
    ("roles", "own", "another"),
    [
        (("owner",), True, True),
        (("finance_staff",), True, True),
        (("tutor",), True, False),
        (("administrator",), False, False),
        (("academic_coordinator",), False, False),
        (("guardian",), False, False),
        (("student",), False, False),
        (("finance_staff", "tutor"), True, True),
        (("owner", "tutor"), True, True),
        (("administrator", "tutor"), True, False),
        (("guardian", "tutor"), True, False),
    ],
)
def test_who_reads_compensation(roles, own, another):
    caller = principal(*roles)
    assert can_view_compensation(caller, ME) is own
    assert can_view_compensation(caller, OTHER) is another


def test_payroll_finalize_gives_no_read():
    delegated = principal("administrator", delegations=("payroll.finalize",))
    assert not can_view_compensation(delegated, OTHER)
    assert not can_view_compensation(delegated, ME)


@pytest.mark.parametrize(
    ("roles", "own"),
    [
        (("owner",), True),
        (("owner", "tutor"), True),
        (("finance_staff",), False),
        (("finance_staff", "tutor"), False),
    ],
)
def test_only_an_owner_writes_their_own_pay(roles, own):
    caller = principal(*roles)
    assert can_write_compensation(caller, ME) is own
    assert can_write_compensation(caller, OTHER) is True
