"""Pure substitution and cancellation rules (#55 B1, C1 step 6, D1, F3; I5)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.scheduling.models import AssignmentOffer
from app.modules.scheduling.offer_records import (
    OfferClosedParams,
    OfferReceivedParams,
    substitution_params,
)
from app.modules.scheduling.schemas import ClassSessionCancel, SubstitutionCreate
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.substitutions import (
    CANCELLABLE_STATES,
    cancel_audience,
    cancel_refusal,
    substitution_refusal,
    substitution_still_valid,
)

NOW = datetime(2031, 1, 1, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=5)


# --- B1 ----------------------------------------------------------------------------


@pytest.mark.parametrize("state", list(SessionState))
def test_only_a_confirmed_session_can_get_a_substitute(state: SessionState) -> None:
    refusal = substitution_refusal(state.value, LATER, NOW, has_pending=False)

    assert refusal == (None if state == SessionState.CONFIRMED else "state")


def test_an_unknown_state_is_refused() -> None:
    assert substitution_refusal("draft", LATER, NOW, has_pending=False) == "state"


@pytest.mark.parametrize(
    ("starts_at", "refusal"),
    [
        (NOW - timedelta(minutes=1), "started"),
        (NOW, "started"),
        (NOW + timedelta(minutes=1), None),
    ],
)
def test_the_start_boundary(starts_at: datetime, refusal: str | None) -> None:
    assert (
        substitution_refusal("confirmed", starts_at, NOW, has_pending=False) == refusal
    )


def test_a_pending_substitution_refuses_a_second_one() -> None:
    assert (
        substitution_refusal("confirmed", LATER, NOW, has_pending=True)
        == "substitution_pending"
    )
    # The state and the start come first.
    assert substitution_refusal("offered", LATER, NOW, has_pending=True) == "state"
    assert substitution_refusal("confirmed", NOW, NOW, has_pending=True) == "started"


# --- C1 step 6 ---------------------------------------------------------------------


@pytest.mark.parametrize("state", list(SessionState))
def test_the_cancellable_states(state: SessionState) -> None:
    refusal = cancel_refusal(state.value, LATER, NOW)

    assert refusal == (None if state in CANCELLABLE_STATES else "state")
    assert (state in CANCELLABLE_STATES) == (
        state
        in {
            SessionState.UNASSIGNED,
            SessionState.OFFERED,
            SessionState.CONFIRMED,
            SessionState.CONFLICT,
        }
    )


@pytest.mark.parametrize(
    ("starts_at", "refusal"),
    [(NOW, "started"), (NOW - timedelta(hours=1), "started"), (LATER, None)],
)
def test_a_started_session_is_not_cancelled(
    starts_at: datetime, refusal: str | None
) -> None:
    assert cancel_refusal("confirmed", starts_at, NOW) == refusal
    # A final state is reported before the start.
    assert cancel_refusal("completed", starts_at, NOW) == "state"


def test_an_unknown_state_is_not_cancellable() -> None:
    assert cancel_refusal("draft", LATER, NOW) == "state"


# --- F3 ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "audience"),
    [
        ("confirmed", {"tutor", "family"}),
        ("offered", {"tutor"}),
        ("unassigned", set()),
        ("conflict", set()),
        ("completed", set()),
        ("skipped_closure", set()),
    ],
)
def test_who_hears_about_a_cancellation(state: str, audience: set[str]) -> None:
    assert cancel_audience(state) == audience


# --- D1 ----------------------------------------------------------------------------

ORIGINAL = uuid.uuid4()
SLOT = (LATER, LATER + timedelta(hours=1))


def _valid(**changes: Any) -> bool:
    values: dict[str, Any] = {
        "state": "confirmed",
        "tutor_user_id": ORIGINAL,
        "starts_at": SLOT[0],
        "ends_at": SLOT[1],
    } | changes
    return substitution_still_valid(
        **values,
        original_tutor_user_id=ORIGINAL,
        slot_starts_at=SLOT[0],
        slot_ends_at=SLOT[1],
    )


def test_the_validity_recheck() -> None:
    assert _valid()
    assert not _valid(state="cancelled")
    assert not _valid(tutor_user_id=uuid.uuid4())
    assert not _valid(starts_at=SLOT[0] + timedelta(minutes=15))
    assert not _valid(ends_at=SLOT[1] + timedelta(minutes=15))


# --- Bodies ------------------------------------------------------------------------


def _codes(error: ValidationError) -> list[tuple[Any, str]]:
    return [(e["loc"], e["type"]) for e in error.errors()]


def test_the_substitution_body_trims_the_reason() -> None:
    body = SubstitutionCreate.model_validate(
        {"substitute_tutor_user_id": str(uuid.uuid4()), "version": 1, "reason": " Ill "}
    )

    assert body.reason == "Ill"
    assert body.respond_by is None
    assert body.override is None


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        (None, "reason_required"),
        ("   ", "reason_required"),
        ("x" * 1001, "reason_too_long"),
    ],
)
@pytest.mark.parametrize("model", [SubstitutionCreate, ClassSessionCancel])
def test_reason_errors(model: Any, reason: str | None, code: str) -> None:
    data: dict[str, Any] = {"substitute_tutor_user_id": str(uuid.uuid4()), "version": 1}
    if model is ClassSessionCancel:
        data = {"version": 1}
    if reason is not None:
        data["reason"] = reason

    with pytest.raises(ValidationError) as raised:
        model.model_validate(data)

    assert _codes(raised.value) == [(("reason",), code)]


@pytest.mark.parametrize("model", [SubstitutionCreate, ClassSessionCancel])
def test_unknown_members_are_refused(model: Any) -> None:
    with pytest.raises(ValidationError) as raised:
        model.model_validate(
            {
                "substitute_tutor_user_id": str(uuid.uuid4()),
                "version": 1,
                "reason": "x",
                "tutor_user_id": str(uuid.uuid4()),
            }
        )

    assert ("tutor_user_id",) in [loc for loc, _ in _codes(raised.value)]


def test_the_cancel_body_keeps_one_thousand_characters() -> None:
    body = ClassSessionCancel.model_validate({"version": 3, "reason": "x" * 1000})

    assert (body.version, len(body.reason)) == (3, 1000)


# --- F1 / F4 params ----------------------------------------------------------------


def test_substitution_keys_only_for_a_substitution() -> None:
    assignment = AssignmentOffer(kind="assignment", scope="session")
    substitution = AssignmentOffer(kind="substitution", scope="session")

    assert substitution_params(assignment, "Ana") == {}
    assert substitution_params(substitution, "Ana") == {
        "kind": "substitution",
        "original_tutor_name": "Ana",
    }
    base = {
        "offer_id": "o",
        "subject_name": "Álgebra",
        "starts_at": "2031-01-06T16:00:00Z",
        "respond_by": "2031-01-03T12:00:00Z",
    }
    # Unset keys are left out of the stored params; never a reason key.
    assert OfferReceivedParams.model_validate(base).model_dump() == base
    dumped = OfferReceivedParams.model_validate(
        base | substitution_params(substitution, "Ana")
    ).model_dump()
    assert dumped == base | {"kind": "substitution", "original_tutor_name": "Ana"}
    assert "reason" not in OfferClosedParams.model_fields
