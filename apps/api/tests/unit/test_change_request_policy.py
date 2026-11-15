"""The change-request record policy (#62 S1, S2; H3): pure, no database."""

import uuid

import pytest

from app.core.authz import Principal
from app.modules.scheduling.change_requests_policy import (
    CreateOutcome,
    create_access,
    may_read,
    may_withdraw,
)
from app.modules.scheduling.requests_policy import FamilyAccess

ME = uuid.uuid4()
OTHER = uuid.uuid4()
CHILD = uuid.uuid4()
SIBLING = uuid.uuid4()


def principal(*roles: str, user_id: uuid.UUID = ME) -> Principal:
    return Principal(user_id=user_id, roles=frozenset(roles), permissions=frozenset())


@pytest.mark.parametrize(
    ("roles", "state", "tutor", "family", "expected"),
    [
        # The session's tutor, in any state (the state is refused later).
        (("tutor",), "confirmed", ME, {}, ("allowed", "tutor", None)),
        (("tutor",), "offered", ME, {}, ("allowed", "tutor", None)),
        # Another tutor: hidden.
        (("tutor",), "confirmed", OTHER, {}, ("hidden", None, None)),
        # The tutor wins for a multi-role caller who teaches the session.
        (
            ("tutor", "guardian"),
            "confirmed",
            ME,
            {CHILD: FamilyAccess.ALLOWED},
            ("allowed", "tutor", None),
        ),
        # A family member of a participant of a family-visible session.
        (
            ("guardian",),
            "confirmed",
            OTHER,
            {CHILD: FamilyAccess.ALLOWED},
            ("allowed", "family", CHILD),
        ),
        (
            ("student",),
            "completed",
            OTHER,
            {CHILD: FamilyAccess.ALLOWED},
            ("allowed", "family", CHILD),
        ),
        # Not family-visible: hidden, whatever the links.
        (
            ("guardian",),
            "offered",
            OTHER,
            {CHILD: FamilyAccess.ALLOWED},
            ("hidden", None, None),
        ),
        (
            ("guardian",),
            "unassigned",
            None,
            {CHILD: FamilyAccess.ALLOWED},
            ("hidden", None, None),
        ),
        (
            ("guardian",),
            "cancelled",
            OTHER,
            {CHILD: FamilyAccess.ALLOWED},
            ("hidden", None, None),
        ),
        (
            ("guardian",),
            "conflict",
            OTHER,
            {CHILD: FamilyAccess.ALLOWED},
            ("hidden", None, None),
        ),
        # An adult without the consent.
        (
            ("guardian",),
            "confirmed",
            OTHER,
            {CHILD: FamilyAccess.CONSENT_REQUIRED},
            ("consent_required", None, None),
        ),
        # One allowed participant is enough; the first by id.
        (
            ("guardian",),
            "confirmed",
            OTHER,
            {
                max(CHILD, SIBLING): FamilyAccess.ALLOWED,
                min(CHILD, SIBLING): FamilyAccess.ALLOWED,
            },
            ("allowed", "family", min(CHILD, SIBLING)),
        ),
        # Unlinked, inactive profile, staff: hidden.
        (
            ("guardian",),
            "confirmed",
            OTHER,
            {CHILD: FamilyAccess.HIDDEN},
            ("hidden", None, None),
        ),
        (("guardian",), "confirmed", OTHER, {}, ("hidden", None, None)),
        (("owner",), "confirmed", OTHER, {}, ("hidden", None, None)),
        (("administrator",), "confirmed", ME, {}, ("hidden", None, None)),
    ],
)
def test_create_access(roles, state, tutor, family, expected):
    access = create_access(
        principal(*roles), state=state, tutor_user_id=tutor, family=family
    )

    assert (access.outcome.value, access.initiated_by, access.student_id) == expected


def test_unknown_states_are_hidden():
    access = create_access(
        principal("guardian"),
        state="draft",
        tutor_user_id=OTHER,
        family={CHILD: FamilyAccess.ALLOWED},
    )
    assert access.outcome is CreateOutcome.HIDDEN


@pytest.mark.parametrize(
    ("roles", "initiated_by", "requested_by", "student", "family", "expected"),
    [
        (("guardian",), "family", OTHER, CHILD, {CHILD}, True),
        (("guardian",), "family", ME, CHILD, set(), False),
        (("guardian",), "tutor", OTHER, None, {CHILD}, False),
        (("tutor",), "tutor", ME, None, set(), True),
        (("tutor",), "tutor", OTHER, None, set(), False),
        (("tutor",), "family", OTHER, CHILD, set(), False),
        (("guardian",), "tutor", ME, None, set(), False),
    ],
)
def test_may_read(roles, initiated_by, requested_by, student, family, expected):
    assert (
        may_read(
            principal(*roles),
            initiated_by=initiated_by,
            requested_by_user_id=requested_by,
            student_id=student,
            family_students=family,
        )
        is expected
    )


def test_only_the_requester_withdraws():
    assert may_withdraw(principal("guardian"), requested_by_user_id=ME)
    assert not may_withdraw(principal("guardian"), requested_by_user_id=OTHER)
