"""Pure assignment offer rules (#52 B1-B4, E1, D3 schema; I4)."""

import uuid
from datetime import UTC, datetime, timedelta
from itertools import product

import pytest
from pydantic import ValidationError

from app.core import jobs
from app.modules.administration.service import SchedulingPolicyValues
from app.modules.scheduling.models import OFFER_STATUSES
from app.modules.scheduling.offers import (
    ANSWERED_STATUSES,
    RespondByInvalidError,
    can_transition_offer,
    is_overdue,
    reassignment_priority,
    resolve_respond_by,
)
from app.modules.scheduling.schemas import (
    AssignmentOfferCreate,
    AssignmentOfferDecline,
    ClassSessionCreate,
)
from app.modules.scheduling.states import (
    FAMILY_VISIBLE_STATES,
    SessionState,
    is_family_visible,
)
from tests.unit.test_job_rules import settings

pytestmark = pytest.mark.unit

NOW = datetime(2031, 1, 6, 15, 0, 30, tzinfo=UTC)
START = datetime(2031, 1, 10, 16, 0, tzinfo=UTC)


def policy(response_hours: int = 48) -> SchedulingPolicyValues:
    return SchedulingPolicyValues(
        id=uuid.uuid7(),
        effective_from=datetime(2000, 1, 1, tzinfo=UTC),
        booking_notice_hours=24,
        change_notice_hours=24,
        response_hours=response_hours,
        makeup_after_on_time=True,
        makeup_after_late=False,
        makeup_after_no_show=False,
        makeup_window_days=30,
    )


# --- B1 ---------------------------------------------------------------------------


@pytest.mark.parametrize(("source", "target"), list(product(OFFER_STATUSES, repeat=2)))
def test_only_a_pending_offer_moves_and_only_to_a_final_status(source, target):
    expected = source == "pending" and target != "pending"
    assert can_transition_offer(source, target) is expected


def test_unknown_statuses_never_move():
    assert not can_transition_offer("pending", "draft")
    assert not can_transition_offer("draft", "accepted")


def test_answered_is_the_four_final_statuses():
    assert ANSWERED_STATUSES == {"accepted", "declined", "expired", "withdrawn"}


# --- B2 ---------------------------------------------------------------------------


def test_the_default_is_the_response_time_truncated_to_whole_minutes():
    assert resolve_respond_by(
        policy(), offered_at=NOW, first_starts_at=START
    ) == datetime(2031, 1, 8, 15, 0, tzinfo=UTC)


def test_the_default_never_passes_the_start():
    soon = NOW + timedelta(hours=10, seconds=30)
    assert resolve_respond_by(
        policy(), offered_at=NOW, first_starts_at=soon
    ) == datetime(2031, 1, 7, 1, 1, tzinfo=UTC)


def test_the_policy_response_time_is_used():
    assert resolve_respond_by(
        policy(12), offered_at=NOW, first_starts_at=START
    ) == datetime(2031, 1, 7, 3, 0, tzinfo=UTC)


def test_a_session_that_has_begun_has_no_deadline():
    with pytest.raises(RespondByInvalidError):
        resolve_respond_by(policy(), offered_at=NOW, first_starts_at=NOW)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        # Exactly the default is allowed.
        (
            datetime(2031, 1, 8, 15, 0, tzinfo=UTC),
            datetime(2031, 1, 8, 15, 0, tzinfo=UTC),
        ),
        # Seconds are dropped.
        (
            datetime(2031, 1, 7, 9, 30, 59, tzinfo=UTC),
            datetime(2031, 1, 7, 9, 30, tzinfo=UTC),
        ),
        # The first whole minute after offered_at.
        (
            datetime(2031, 1, 6, 15, 1, tzinfo=UTC),
            datetime(2031, 1, 6, 15, 1, tzinfo=UTC),
        ),
    ],
)
def test_an_earlier_deadline_is_kept(requested, expected):
    assert (
        resolve_respond_by(
            policy(), offered_at=NOW, first_starts_at=START, requested=requested
        )
        == expected
    )


@pytest.mark.parametrize(
    "requested",
    [
        datetime(2031, 1, 8, 15, 1, tzinfo=UTC),  # after the default
        datetime(2031, 1, 6, 15, 0, 45, tzinfo=UTC),  # truncates to <= offered_at
        NOW,
        NOW - timedelta(hours=1),
    ],
)
def test_other_deadlines_are_invalid(requested):
    with pytest.raises(RespondByInvalidError):
        resolve_respond_by(
            policy(), offered_at=NOW, first_starts_at=START, requested=requested
        )


# --- B3 ---------------------------------------------------------------------------


def test_an_offer_is_overdue_from_its_deadline_on():
    deadline = datetime(2031, 1, 8, 15, 0, tzinfo=UTC)
    assert not is_overdue(deadline, deadline - timedelta(microseconds=1))
    assert is_overdue(deadline, deadline)
    assert is_overdue(deadline, deadline + timedelta(minutes=1))


# --- B4 ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", list(SessionState))
def test_only_confirmed_and_completed_sessions_are_family_visible(state):
    assert is_family_visible(state) is (state in {"confirmed", "completed"})


def test_family_visible_states():
    assert FAMILY_VISIBLE_STATES == {"confirmed", "completed"}
    assert not is_family_visible("draft")


# --- E1 ---------------------------------------------------------------------------


def test_the_reassignment_priority_is_high_within_24_hours():
    assert reassignment_priority(NOW + timedelta(hours=23, minutes=59), NOW) == "high"
    assert reassignment_priority(NOW - timedelta(hours=1), NOW) == "high"
    assert reassignment_priority(NOW + timedelta(hours=24), NOW) == "normal"


# --- Schemas (C1, C2, D3) ---------------------------------------------------------


def test_a_decline_note_is_trimmed_and_an_empty_one_is_none():
    assert AssignmentOfferDecline(note="  Viajo  ").note == "Viajo"
    assert AssignmentOfferDecline(note="   ").note is None
    assert AssignmentOfferDecline().note is None
    assert AssignmentOfferDecline(note="x" * 500).note == "x" * 500


def test_a_decline_note_over_500_characters_is_refused():
    with pytest.raises(ValidationError) as exc:
        AssignmentOfferDecline(note="x" * 501)
    assert exc.value.errors()[0]["type"] == "decline_note_too_long"


def test_a_naive_respond_by_is_refused_by_the_schemas():
    naive = "2031-01-07T10:00:00"
    with pytest.raises(ValidationError):
        AssignmentOfferCreate.model_validate(
            {"tutor_user_id": "00000000-0000-7000-8000-000000000001", "version": 1}
            | {"respond_by": naive}
        )
    with pytest.raises(ValidationError):
        ClassSessionCreate.model_validate(
            {
                "kind": "individual",
                "student_id": "00000000-0000-7000-8000-000000000001",
                "subject_id": "00000000-0000-7000-8000-000000000002",
                "starts_at": "2031-01-10T10:00:00-06:00",
                "ends_at": "2031-01-10T11:00:00-06:00",
                "respond_by": naive,
            }
        )


def test_the_offer_body_forbids_unknown_members():
    with pytest.raises(ValidationError):
        AssignmentOfferCreate.model_validate(
            {
                "tutor_user_id": "00000000-0000-7000-8000-000000000001",
                "version": 1,
                "state": "confirmed",
            }
        )


# --- E3: the periodic task ------------------------------------------------------------


def test_the_expiry_task_is_periodic_with_a_queueing_lock():
    app = jobs.create_worker_job_app(settings("prod"))

    [periodic] = [
        p
        for p in app.periodic_registry.periodic_tasks.values()
        if p.task.name == "scheduling:expire_offers"
    ]
    assert periodic.cron == "*/5 * * * *"
    assert periodic.periodic_id == "scheduling-expire-offers"
    assert periodic.configure_kwargs["queueing_lock"] == "scheduling-expire-offers"
    assert app.tasks["scheduling:expire_offers"].queueing_lock == (
        "scheduling-expire-offers"
    )
