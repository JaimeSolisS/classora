"""The pure attendance rules (#76 A3, B1, H5): the block codes for every
state x started or not x tutor or staff, and the record policy for every
role x relation."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.authz import Principal
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from app.modules.scheduling.attendance_rules import (
    ATTENDANCE_STATUSES,
    RECORDABLE_STATES,
    AttendanceAccess,
    attendance_access,
    attendance_block,
    can_correct,
)
from app.modules.scheduling.models import ATTENDANCE_STATUS_VALUES
from app.modules.scheduling.states import SessionState

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
PAST = NOW - timedelta(minutes=30)
LONG_AGO = NOW - timedelta(days=7)
FUTURE = NOW + timedelta(minutes=1)


def test_the_statuses_and_states():
    assert ATTENDANCE_STATUSES == ("present", "late", "absent")
    assert ATTENDANCE_STATUS_VALUES == ATTENDANCE_STATUSES
    assert RECORDABLE_STATES == {SessionState.CONFIRMED, SessionState.COMPLETED}


NOT_RECORDABLE = [
    SessionState.UNASSIGNED,
    SessionState.OFFERED,
    SessionState.CONFLICT,
    SessionState.CANCELLED,
    SessionState.SKIPPED_CLOSURE,
]

BLOCK_CASES = [
    # (state, starts_at, by_tutor, expected)
    *(
        (state, starts_at, by_tutor, "attendance_not_recordable")
        for state in NOT_RECORDABLE
        for starts_at in (PAST, FUTURE)
        for by_tutor in (True, False)
    ),
    (SessionState.CONFIRMED, FUTURE, True, "attendance_not_started"),
    (SessionState.CONFIRMED, FUTURE, False, "attendance_not_started"),
    (SessionState.COMPLETED, FUTURE, True, "attendance_not_started"),
    (SessionState.COMPLETED, FUTURE, False, "attendance_not_started"),
    (SessionState.CONFIRMED, NOW, True, None),
    (SessionState.CONFIRMED, PAST, True, None),
    (SessionState.CONFIRMED, LONG_AGO, True, None),
    (SessionState.CONFIRMED, PAST, False, None),
    (SessionState.COMPLETED, PAST, True, "attendance_locked"),
    (SessionState.COMPLETED, PAST, False, None),
]


@pytest.mark.parametrize(("state", "starts_at", "by_tutor", "expected"), BLOCK_CASES)
def test_attendance_block(state, starts_at, by_tutor, expected):
    assert attendance_block(state, starts_at, NOW, by_tutor=by_tutor) == expected


def principal(user_id: uuid.UUID, *roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


ME = uuid.uuid4()
OTHER = uuid.uuid4()

POLICY_CASES = [
    # (roles, the session's tutor_user_id, expected)
    ((TUTOR,), ME, AttendanceAccess.TUTOR),
    ((TUTOR,), OTHER, AttendanceAccess.HIDDEN),
    ((TUTOR,), None, AttendanceAccess.HIDDEN),
    # tutor_user_id but no longer a tutor: hidden.
    ((STUDENT,), ME, AttendanceAccess.HIDDEN),
    ((GUARDIAN,), ME, AttendanceAccess.HIDDEN),
    ((STUDENT,), OTHER, AttendanceAccess.HIDDEN),
    ((GUARDIAN,), OTHER, AttendanceAccess.HIDDEN),
    ((GUARDIAN, STUDENT), OTHER, AttendanceAccess.HIDDEN),
    *(
        ((role,), tutor, AttendanceAccess.STAFF)
        for role in (OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF)
        for tutor in (ME, OTHER, None)
    ),
    # A staff member who is also the current tutor marks as the tutor.
    ((ADMINISTRATOR, TUTOR), ME, AttendanceAccess.TUTOR),
    ((ADMINISTRATOR, TUTOR), OTHER, AttendanceAccess.STAFF),
    ((TUTOR, GUARDIAN), OTHER, AttendanceAccess.HIDDEN),
]


@pytest.mark.parametrize(("roles", "tutor_user_id", "expected"), POLICY_CASES)
def test_attendance_access(roles, tutor_user_id, expected):
    assert attendance_access(principal(ME, *roles), tutor_user_id) is expected


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (OWNER, True),
        (ADMINISTRATOR, True),
        (ACADEMIC_COORDINATOR, True),
        (FINANCE_STAFF, False),
        (TUTOR, False),
        (STUDENT, False),
        (GUARDIAN, False),
    ],
)
def test_can_correct(role, expected):
    assert can_correct(principal(ME, role)) is expected
