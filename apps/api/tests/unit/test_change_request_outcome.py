"""The outcome of a change request (#62 B2 step 10, C2 step 10, C5, F1, A2;
H3): pure, no database."""

import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest

from app.modules.administration.service import SchedulingPolicyValues
from app.modules.scheduling.change_request_outcome import (
    Outcome,
    effective_outcome,
    exception_applicable,
    shown_outcome,
)
from app.modules.scheduling.change_request_review import (
    ChangeRequestApprovedParams,
    ChangeRequestRejectedParams,
)
from app.modules.scheduling.models import (
    CHANGE_REQUEST_CHECKS,
    SessionChangeRequest,
)

START = datetime(2031, 1, 7, 16, tzinfo=UTC)
ASKED = START - timedelta(hours=10)
POLICY = SchedulingPolicyValues(
    id=uuid.uuid4(),
    effective_from=datetime(2000, 1, 1, tzinfo=UTC),
    booking_notice_hours=24,
    change_notice_hours=24,
    response_hours=48,
    makeup_after_on_time=True,
    makeup_after_late=False,
    makeup_after_no_show=False,
    makeup_window_days=30,
)


@pytest.mark.parametrize(
    "stored",
    [
        Outcome("on_time", True, START + timedelta(days=30)),
        Outcome("late", False, None),
        Outcome("excused", True, START + timedelta(days=30)),
    ],
)
@pytest.mark.parametrize("kind", ["cancellation", "reschedule"])
def test_without_an_exception_the_stored_outcome_stands(stored, kind):
    assert (
        effective_outcome(
            stored=stored,
            exception=False,
            kind=kind,
            policy=None,
            session_starts_at=START,
            requested_at=ASKED,
        )
        == stored
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("cancellation", Outcome("excused", True, START + timedelta(days=30))),
        ("reschedule", Outcome("excused", False, None)),
    ],
)
def test_an_exception_is_excused_under_the_stored_version(kind, expected):
    assert (
        effective_outcome(
            stored=Outcome("late", False, None),
            exception=True,
            kind=kind,
            policy=POLICY,
            session_starts_at=START,
            requested_at=ASKED,
        )
        == expected
    )


def test_an_exception_needs_a_request_that_is_not_excused():
    assert exception_applicable("late")
    assert exception_applicable("on_time")
    assert exception_applicable("no_show")
    assert not exception_applicable("excused")


def _row(status: str, **values: object) -> SessionChangeRequest:
    return SessionChangeRequest(
        status=status,
        classification="late",
        makeup_allowed=False,
        makeup_until=None,
        **values,
    )


def test_reads_show_the_final_outcome_only_once_approved():
    final = {
        "final_classification": "excused",
        "final_makeup_allowed": True,
        "final_makeup_until": START + timedelta(days=30),
    }
    assert shown_outcome(_row("approved", **final)) == Outcome(
        "excused", True, START + timedelta(days=30)
    )
    for status in ("pending", "rejected", "withdrawn"):
        assert shown_outcome(_row(status)) == Outcome("late", False, None)


def test_the_notification_params_carry_no_reason_or_people():
    forbidden = {
        "reason",
        "decision_reason",
        "tutor",
        "tutor_name",
        "classroom",
        "classification",
        "note",
    }
    assert not forbidden & set(ChangeRequestApprovedParams.model_fields)
    assert not forbidden & set(ChangeRequestRejectedParams.model_fields)
    assert set(ChangeRequestApprovedParams.model_fields) == {
        "change_request_id",
        "kind",
        "starts_at",
        "new_starts_at",
        "makeup_until",
        "exception",
    }
    assert set(ChangeRequestRejectedParams.model_fields) == {
        "change_request_id",
        "kind",
        "starts_at",
    }


def test_the_model_and_the_migration_declare_the_same_checks():
    migration = import_module(
        "migrations.versions.0044_scheduling_session_change_requests"
    )
    assert migration.CHECKS == CHANGE_REQUEST_CHECKS
    checks = {
        constraint.name
        for constraint in SessionChangeRequest.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert {
        str(name).removeprefix("ck_session_change_requests_") for name in checks
    } == set(CHANGE_REQUEST_CHECKS)
