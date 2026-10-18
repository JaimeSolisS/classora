"""Audit JSON values and explicit-event validation (M2-04 C3, C6, D1, D2)."""

import enum
import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core import audit
from app.core.audit import (
    AUTOMATIC_ACTIONS,
    AuditEvent,
    bind_actor,
    record_audit_event,
    sensitive_keys,
    to_json_value,
)
from app.core.logging import request_id_var

# --- C3: JSON values ----------------------------------------------------------


class Colour(enum.Enum):
    RED = "red"


class Level(enum.IntEnum):
    HIGH = 3


ID = uuid.UUID("0199a000-0000-7000-8000-000000000001")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (False, False),
        (7, 7),
        ("texto ñ", "texto ñ"),
        (ID, "0199a000-0000-7000-8000-000000000001"),
        (
            datetime(2026, 10, 18, 9, 30, tzinfo=timezone(timedelta(hours=-6))),
            "2026-10-18T09:30:00-06:00",
        ),
        (
            datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC),
            "2026-01-02T03:04:05.000006+00:00",
        ),
        (date(2026, 2, 28), "2026-02-28"),
        (time(16, 5, 0), "16:05:00"),
        (Decimal("1250.00"), "1250.00"),
        (Decimal("0.10"), "0.10"),
        (Decimal("1E+3"), "1000"),
        (Decimal("-3.5000"), "-3.5000"),
        (Colour.RED, "red"),
        (Level.HIGH, 3),
        ([1, "a", None, ID], [1, "a", None, str(ID)]),
        ({"a": {"b": [Decimal("2.50")]}}, {"a": {"b": ["2.50"]}}),
    ],
)
def test_supported_values(value, expected):
    assert to_json_value(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        b"raw",
        bytearray(b"raw"),
        memoryview(b"raw"),
        1.5,
        datetime(2026, 10, 18, 9, 30),  # naive
        Decimal("NaN"),
        Decimal("Infinity"),
        {1: "non-string key"},
        {"nested": [b"bytes"]},
        object(),
        {"set"},
    ],
)
def test_unsupported_values_raise_type_error(value):
    with pytest.raises(TypeError):
        to_json_value(value)


# --- D1: record_audit_event validation ----------------------------------------


def session() -> Session:
    # Never bound to an engine: these tests only inspect what is added.
    return Session()


def added(db: Session) -> list[AuditEvent]:
    return [obj for obj in db.new if isinstance(obj, AuditEvent)]


def test_valid_event_without_a_record():
    db = session()

    record_audit_event(db, action="identity.login_failed", details={"reason": "x"})

    [event] = added(db)
    assert event.action == "identity.login_failed"
    assert (event.record_type, event.record_id) == (None, None)
    assert event.details == {"reason": "x"}
    assert event.actor_user_id is None
    assert event.request_id is None
    assert event.id.version == 7


def test_valid_event_with_a_record_actor_and_request_id():
    db = session()
    actor = uuid.uuid7()
    bind_actor(db, lambda: actor)
    token = request_id_var.set("req-123")
    try:
        record_audit_event(
            db,
            action="identity.owner_bootstrapped",
            record_type="users",
            record_id=ID,
            details={"source": "cli", "when": date(2026, 10, 18)},
        )
    finally:
        request_id_var.reset(token)

    [event] = added(db)
    assert (event.record_type, event.record_id) == ("users", ID)
    assert event.actor_user_id == actor
    assert event.request_id == "req-123"
    assert event.details == {"source": "cli", "when": "2026-10-18"}


@pytest.mark.parametrize(
    "action",
    [
        *AUTOMATIC_ACTIONS,
        "identity",
        "Identity.login",
        "identity.",
        ".login",
        "identity.login-failed",
        "identity..login",
        "identity.login ",
        "",
    ],
)
def test_invalid_and_automatic_actions_are_rejected(action):
    db = session()

    with pytest.raises(ValueError, match="not an explicit audit action"):
        record_audit_event(db, action=action)

    assert added(db) == []


@pytest.mark.parametrize(
    "details",
    [
        {"password": "x"},
        {"user": {"new_PASSWORD": "x"}},
        {"items": [{"reset_token": "x"}]},
        {"client_secret": "x"},
        {"password_hash": "x"},
    ],
)
def test_details_with_a_sensitive_key_are_rejected(details):
    db = session()

    with pytest.raises(ValueError, match="secrets"):
        record_audit_event(db, action="identity.login_failed", details=details)

    assert added(db) == []


def test_details_must_be_an_object_of_supported_values():
    db = session()

    with pytest.raises(ValueError, match="JSON object"):
        record_audit_event(db, action="a.b", details=["x"])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        record_audit_event(db, action="a.b", details={"raw": b"x"})

    assert added(db) == []


def test_record_type_and_id_come_together():
    db = session()

    with pytest.raises(ValueError, match="together"):
        record_audit_event(db, action="a.b", record_type="users")
    with pytest.raises(ValueError, match="together"):
        record_audit_event(db, action="a.b", record_id=ID)

    assert added(db) == []


def test_sensitive_keys_lists_every_path():
    assert sensitive_keys({"a": {"Token": 1}, "b": [{"secret": 2}], "c": 3}) == [
        "a.Token",
        "b[0].secret",
    ]


# --- C6: actor binding --------------------------------------------------------


def test_actor_defaults_to_none_without_a_binding():
    assert audit.current_actor_id(session()) is None
