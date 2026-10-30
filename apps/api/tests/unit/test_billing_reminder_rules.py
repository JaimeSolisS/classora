"""Reminder stages, the payment reminder params and the due-date bodies
(#97 F1, F6, C1, D1; I7)."""

import importlib.util
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from app.core.money import Money
from app.modules.administration.notifications import (
    InvalidNotificationParamsError,
    validated_params,
)
from app.modules.billing import service as billing
from app.modules.billing.models import ChargeReminder
from app.modules.billing.reminders import (
    DUE_SOON_LEAD_DAYS,
    FOLLOWUP_AFTER_DAYS,
    REMINDER_STAGES,
    reminder_stage,
)
from app.workflows.charge_reminders import (
    PAYMENT_REMINDER,
    PaymentReminderParams,
    register_reminder_notifications,
)

DUE = date(2031, 3, 10)


@pytest.mark.parametrize(
    ("offset", "stage"),
    [
        (-400, None),
        (-4, None),
        (-3, "due_soon"),
        (-1, "due_soon"),
        (0, "due_soon"),
        (1, "overdue"),
        (13, "overdue"),
        (14, "overdue_followup"),
        (400, "overdue_followup"),
    ],
)
def test_every_window_edge(offset, stage):
    assert reminder_stage(DUE, DUE + timedelta(days=offset)) == stage


def test_the_constants_and_stage_codes():
    assert (DUE_SOON_LEAD_DAYS, FOLLOWUP_AFTER_DAYS) == (3, 14)
    assert REMINDER_STAGES == ("due_soon", "overdue", "overdue_followup")
    # The web's `_<kind>` variants need letters and `_` only (#55 F4).
    assert all(stage.replace("_", "").isalpha() for stage in REMINDER_STAGES)


def _migration_stages() -> str:
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "0067_billing_due_dates_reminders.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0067", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stages: str = module.STAGES
    return stages


def test_the_stage_check_matches_the_codes():
    [check] = [
        c
        for c in ChargeReminder.__table__.constraints
        if isinstance(c, CheckConstraint)
    ]
    expected = ", ".join(repr(stage) for stage in REMINDER_STAGES)
    assert str(check.sqltext) == f"stage IN ({expected})"
    assert _migration_stages() == f"({expected})"


def test_the_service_exports_the_rules():
    assert billing.reminder_stage is reminder_stage
    assert billing.REMINDER_STAGES == REMINDER_STAGES


# --- F6: the params ------------------------------------------------------------------


@pytest.fixture
def registered() -> None:
    register_reminder_notifications()


def test_the_params_are_flat_data(registered):
    family = uuid.uuid7()
    params = PaymentReminderParams(
        family_id=family,
        kind="overdue",
        count=2,
        open_total=Money("1250.50"),
        due_on=DUE,
    )

    assert validated_params(PAYMENT_REMINDER, params) == {
        "family_id": str(family),
        "kind": "overdue",
        "count": 2,
        "open_total": "1250.50",
        "due_on": "2031-03-10",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "late"},
        {"count": 0},
        {"open_total": "1.234"},
        {"due_on": "10/03/2031"},
        {"student_name": "Ana"},
    ],
)
def test_bad_params_are_refused(registered, change):
    params = {
        "family_id": str(uuid.uuid7()),
        "kind": "due_soon",
        "count": 1,
        "open_total": "100.00",
        "due_on": "2031-03-10",
        **change,
    }
    with pytest.raises(InvalidNotificationParamsError):
        validated_params(PAYMENT_REMINDER, params)


def test_registering_again_changes_nothing():
    register_reminder_notifications()
    register_reminder_notifications()


# --- C1, D1: the bodies --------------------------------------------------------------


@pytest.mark.parametrize("days", [0, 7, 60])
def test_the_offset_body_accepts_0_to_60(days):
    body = billing.BillingAccountUpdate.model_validate(
        {"version": None, "due_offset_days": days}
    )
    assert (body.version, body.due_offset_days) == (None, days)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "due_offset_days": -1},
        {"version": 1, "due_offset_days": 61},
        {"due_offset_days": 5},
        {"version": 1},
        {"version": 1, "due_offset_days": 5, "other": 1},
    ],
)
def test_the_offset_body_refuses(payload):
    with pytest.raises(ValidationError):
        billing.BillingAccountUpdate.model_validate(payload)


def test_the_due_date_change_body_trims_the_reason():
    body = billing.DueDateChangeCreate.model_validate(
        {"due_date": "2000-01-01", "reason": "  Acuerdo  "}
    )
    assert (body.due_date, body.reason) == (date(2000, 1, 1), "Acuerdo")


@pytest.mark.parametrize(
    ("payload", "location", "code"),
    [
        ({"due_date": "1999-12-31", "reason": "x"}, ("due_date",), "date_out_of_range"),
        ({"due_date": "2100-01-01", "reason": "x"}, ("due_date",), "date_out_of_range"),
        ({"due_date": "2031-03-10", "reason": "   "}, ("reason",), "string_too_short"),
        (
            {"due_date": "2031-03-10", "reason": "x" * 501},
            ("reason",),
            "string_too_long",
        ),
        ({"due_date": "2031-03-10", "reason": "x", "x": 1}, ("x",), "extra_forbidden"),
    ],
)
def test_the_due_date_change_body_refuses(payload, location, code):
    with pytest.raises(ValidationError) as caught:
        billing.DueDateChangeCreate.model_validate(payload)
    [error] = caught.value.errors()
    assert (error["loc"], error["type"]) == (location, code)
