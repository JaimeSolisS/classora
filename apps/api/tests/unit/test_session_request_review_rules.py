"""Pure parts of the staff review of session requests (#60 I3): the inbox
handler's guards (E2), the notification params (F1), the bodies (C1, D1),
the errors and the ``notice_reference_at`` default of the create path
(E3)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.scheduling import booking, request_approval, request_review
from app.modules.scheduling.errors import (
    SessionRequestNotPendingError,
    SessionRequestReviewRouteRequiredError,
)
from app.modules.scheduling.request_schemas import (
    SessionRequestConfirm,
    SessionRequestReject,
)
from app.modules.scheduling.schemas import ClassSessionSlot
from tests.conftest import frozen_clock

pytestmark = pytest.mark.anyio

REQUEST_ID = uuid.uuid4()
VIEW = administration.ApprovalRequestView(
    id=uuid.uuid4(),
    request_type="scheduling.session_request",
    subject_id=REQUEST_ID,
    requester_user_id=uuid.uuid4(),
    status="pending",
)
STAFF = Principal(
    user_id=uuid.uuid4(), roles=frozenset({"owner"}), permissions=frozenset()
)


@dataclass
class _Row:
    status: str
    class_session_id: uuid.UUID | None = None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _with_row(monkeypatch: pytest.MonkeyPatch, row: _Row | None) -> None:
    async def _subject(session: Any, request_id: uuid.UUID) -> _Row | None:
        assert request_id == REQUEST_ID
        return row

    monkeypatch.setattr(request_approval, "_subject", _subject)


HANDLER = request_approval.SESSION_REQUEST_APPROVAL_HANDLER


@pytest.mark.parametrize(
    "row",
    [
        None,
        _Row("pending"),
        _Row("rejected"),
        _Row("withdrawn"),
        _Row("confirmed", None),
    ],
    ids=["missing", "pending", "rejected", "withdrawn", "confirmed-without-session"],
)
async def test_approve_needs_a_confirmed_request_with_a_session(monkeypatch, row):
    _with_row(monkeypatch, row)

    with pytest.raises(SessionRequestReviewRouteRequiredError) as raised:
        await HANDLER.approve(None, VIEW, STAFF)  # type: ignore[arg-type]

    assert raised.value.status == 409
    assert raised.value.details == {"review_path": f"/schedule/requests/{REQUEST_ID}"}


async def test_approve_accepts_a_confirmed_request(monkeypatch):
    _with_row(monkeypatch, _Row("confirmed", uuid.uuid4()))

    await HANDLER.approve(None, VIEW, STAFF)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "row",
    [None, _Row("pending"), _Row("confirmed", uuid.uuid4()), _Row("withdrawn")],
    ids=["missing", "pending", "confirmed", "withdrawn"],
)
async def test_reject_needs_a_rejected_request(monkeypatch, row):
    _with_row(monkeypatch, row)

    with pytest.raises(SessionRequestReviewRouteRequiredError):
        await HANDLER.reject(None, VIEW, STAFF, "Motivo")  # type: ignore[arg-type]


async def test_reject_accepts_a_rejected_request(monkeypatch):
    _with_row(monkeypatch, _Row("rejected"))

    await HANDLER.reject(None, VIEW, STAFF, "Motivo")  # type: ignore[arg-type]


def test_the_handler_keeps_its_review_rules():
    assert HANDLER.review_permission == "schedule.manage"
    assert HANDLER.supports_request_changes is False
    assert HANDLER.reason_required_on_reject is True


# --- F1: the notification params -----------------------------------------------------


def test_the_params_carry_no_tutor_room_session_or_reason():
    fields = set(request_review.SessionRequestDecisionParams.model_fields)

    assert fields == {"session_request_id", "student_name", "subject_name", "starts_at"}
    for forbidden in ("tutor", "room", "classroom", "class_session", "reason", "offer"):
        assert not any(forbidden in field for field in fields), forbidden


def test_both_types_are_registered():
    request_review.register_request_notifications()

    registered = administration.registered_notification_types()
    assert "scheduling.session_request_confirmed" in registered
    assert "scheduling.session_request_rejected" in registered


# --- C1, D1: the bodies --------------------------------------------------------------


def test_the_confirm_body_needs_a_tutor_and_forbids_extras():
    with pytest.raises(ValidationError) as missing:
        SessionRequestConfirm.model_validate({"version": 1})
    with pytest.raises(ValidationError) as extra:
        SessionRequestConfirm.model_validate(
            {"version": 1, "tutor_user_id": str(uuid.uuid4()), "kind": "individual"}
        )

    assert [e["loc"] for e in missing.value.errors()] == [("tutor_user_id",)]
    assert [e["type"] for e in extra.value.errors()] == ["extra_forbidden"]


@pytest.mark.parametrize(
    ("reason", "code"),
    [("", "missing"), (" \n ", "missing"), ("x" * 1001, "string_too_long")],
)
def test_the_reject_reason_uses_the_40_codes(reason, code):
    with pytest.raises(ValidationError) as raised:
        SessionRequestReject.model_validate({"version": 1, "reason": reason})

    assert [e["type"] for e in raised.value.errors()] == [code]


def test_the_reject_reason_is_trimmed_and_1000_characters_fit():
    assert SessionRequestReject(version=1, reason="  Sin cupo ").reason == "Sin cupo"
    assert len(SessionRequestReject(version=1, reason="y" * 1000).reason) == 1000


def test_not_pending_has_a_status_only_for_staff():
    assert SessionRequestNotPendingError().details is None
    assert SessionRequestNotPendingError("confirmed").details == {"status": "confirmed"}


# --- E3: notice_reference_at ---------------------------------------------------------


async def test_the_notice_is_measured_from_now_unless_a_reference_is_given(
    monkeypatch,
):
    seen: list[datetime] = []

    async def collect(session: Any, candidate: Any, **kwargs: Any) -> list[Any]:
        seen.append(kwargs["now"])
        return []

    monkeypatch.setattr(booking, "collect_conflicts", collect)
    body = ClassSessionSlot(
        kind="individual",
        student_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        starts_at=datetime(2031, 1, 6, 16, tzinfo=UTC),
        ends_at=datetime(2031, 1, 6, 17, tzinfo=UTC),
    )
    now = datetime(2031, 1, 1, 12, tzinfo=UTC)
    reference = datetime(2030, 12, 20, 9, tzinfo=UTC)

    with frozen_clock(now):
        await booking.find_conflicts(None, body, "UTC")  # type: ignore[arg-type]
        await booking.find_conflicts(
            None,  # type: ignore[arg-type]
            body,
            "UTC",
            notice_reference_at=reference,
        )

    assert seen == [now, reference]
