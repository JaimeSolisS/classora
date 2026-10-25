"""Session closeout rules (#77 A5, C1, E4, E5, H4): table-driven, no
database."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.authz import Principal
from app.modules.academics.closeout_rules import (
    ACTUAL_MINUTES_RANGE,
    COMPLETED,
    CONFIRMED,
    NOT_CLOSABLE_STATES,
    SUMMARY_MAX,
    CloseoutAccess,
    all_marked_absent,
    clean_text,
    closeout_access,
    closeout_block,
    sees_summary,
    summary_errors,
)
from app.modules.administration.service import ApprovalHandler
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
from app.modules.scheduling.states import SessionState
from app.workflows.session_closeout import (
    SESSION_CLOSEOUT_HANDLER,
    SessionCloseoutApprovalHandler,
)

NOW = datetime(2031, 3, 5, 18, 0, tzinfo=UTC)
PAST = NOW - timedelta(minutes=30)
FUTURE = NOW + timedelta(minutes=1)


def test_the_bounds():
    assert ACTUAL_MINUTES_RANGE == (0, 720)
    assert SUMMARY_MAX == 2000


def test_the_state_strings_match_the_scheduling_states():
    # A5 drift test: academics compares states as text.
    assert {s.value for s in SessionState} == {
        CONFIRMED,
        COMPLETED,
        *NOT_CLOSABLE_STATES,
    }
    assert CONFIRMED == SessionState.CONFIRMED.value
    assert COMPLETED == SessionState.COMPLETED.value


@pytest.mark.parametrize("state", [s.value for s in SessionState])
@pytest.mark.parametrize("starts_at", [PAST, NOW, FUTURE])
@pytest.mark.parametrize("has_closeout", [False, True])
def test_closeout_block(state, starts_at, has_closeout):
    if state == "completed" or has_closeout:
        expected = "closeout_already_submitted"
    elif state != "confirmed":
        expected = "session_not_closable"
    elif starts_at > NOW:
        expected = "session_not_started"
    else:
        expected = None

    assert closeout_block(state, starts_at, NOW, has_closeout=has_closeout) == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("unassigned", "session_not_closable"),
        ("offered", "session_not_closable"),
        ("conflict", "session_not_closable"),
        ("cancelled", "session_not_closable"),
        ("skipped_closure", "session_not_closable"),
        ("completed", "closeout_already_submitted"),
        ("confirmed", None),
    ],
)
def test_closeout_block_by_state_for_a_started_session(state, expected):
    assert closeout_block(state, PAST, NOW, has_closeout=False) == expected


FULL = {
    "topics": "Fracciones",
    "performance": "Bien",
    "homework": None,
    "next_steps": "Decimales",
}


@pytest.mark.parametrize(
    ("summary", "all_absent", "expected"),
    [
        (FULL, False, []),
        (FULL | {"homework": "Ejercicios 1-5"}, False, []),
        (
            {},
            False,
            [
                ("topics", "summary_required"),
                ("performance", "summary_required"),
                ("next_steps", "summary_required"),
            ],
        ),
        ({}, True, []),
        (FULL | {"topics": "   "}, False, [("topics", "summary_required")]),
        (FULL | {"performance": None}, False, [("performance", "summary_required")]),
        (FULL | {"next_steps": ""}, False, [("next_steps", "summary_required")]),
        (FULL | {"topics": "x" * 2000}, False, []),
        (FULL | {"topics": "x" * 2001}, False, [("topics", "too_long")]),
        (FULL | {"homework": "x" * 2001}, False, [("homework", "too_long")]),
        (FULL | {"homework": " " + "x" * 2000 + " "}, False, []),
        ({"homework": "x" * 2001}, True, [("homework", "too_long")]),
    ],
)
def test_summary_errors(summary, all_absent, expected):
    assert summary_errors(summary, all_absent=all_absent) == expected


def test_clean_text():
    assert clean_text(None) is None
    assert clean_text("  ") is None
    assert clean_text("") is None
    assert clean_text("  a b  ") == "a b"


A, B = uuid.uuid4(), uuid.uuid4()


@pytest.mark.parametrize(
    ("statuses", "participants", "expected"),
    [
        ({A: "absent", B: "absent"}, 2, True),
        ({A: "absent"}, 1, True),
        ({A: "absent", B: "late"}, 2, False),
        ({A: "absent", B: "present"}, 2, False),
        ({A: "absent"}, 2, False),  # one unmarked
        ({}, 0, False),
    ],
)
def test_all_marked_absent(statuses, participants, expected):
    assert all_marked_absent(statuses, participants) is expected


def principal(user_id: uuid.UUID, *roles: str) -> Principal:
    permissions: set[str] = set()
    for role in roles:
        permissions |= ROLE_PERMISSIONS[role]
    return Principal(
        user_id=user_id, roles=frozenset(roles), permissions=frozenset(permissions)
    )


ME = uuid.uuid4()
OTHER = uuid.uuid4()


@pytest.mark.parametrize(
    ("roles", "tutor_user_id", "access", "summary"),
    [
        # The current tutor.
        ((TUTOR,), ME, CloseoutAccess.TUTOR, True),
        # A staff member who is also the current tutor.
        ((TUTOR, ACADEMIC_COORDINATOR), ME, CloseoutAccess.TUTOR, True),
        # Another tutor, a tutor without a session tutor, a lost tutor role.
        ((TUTOR,), OTHER, CloseoutAccess.HIDDEN, False),
        ((TUTOR,), None, CloseoutAccess.HIDDEN, False),
        ((GUARDIAN,), ME, CloseoutAccess.HIDDEN, False),
        ((STUDENT,), OTHER, CloseoutAccess.HIDDEN, False),
        ((GUARDIAN,), OTHER, CloseoutAccess.HIDDEN, False),
        # Staff with schedule.view; the summary for teaching_hours.approve.
        ((OWNER,), OTHER, CloseoutAccess.STAFF, True),
        ((ADMINISTRATOR,), OTHER, CloseoutAccess.STAFF, True),
        ((ACADEMIC_COORDINATOR,), OTHER, CloseoutAccess.STAFF, True),
        ((FINANCE_STAFF,), OTHER, CloseoutAccess.STAFF, False),
        ((FINANCE_STAFF,), None, CloseoutAccess.STAFF, False),
    ],
)
def test_the_record_policy_and_the_summary_rule(roles, tutor_user_id, access, summary):
    caller = principal(ME, *roles)

    assert closeout_access(caller, tutor_user_id) is access
    assert sees_summary(caller, tutor_user_id) is summary


def test_the_handler_conforms_to_the_protocol():
    handler: ApprovalHandler = SESSION_CLOSEOUT_HANDLER
    assert isinstance(SESSION_CLOSEOUT_HANDLER, SessionCloseoutApprovalHandler)
    assert handler.request_type == "academics.session_closeout"
    assert handler.review_permission == "teaching_hours.approve"
    assert handler.supports_request_changes is True
    assert handler.reason_required_on_reject is True
    assert SESSION_CLOSEOUT_HANDLER.inbox_decisions is False
