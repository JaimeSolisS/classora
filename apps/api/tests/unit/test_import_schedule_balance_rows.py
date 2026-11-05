"""The pure row checks of the ``schedules`` and ``opening_balances`` import
kinds (#123 B1, B2, C1, C2): weekday and amount parsing, and the rule codes
from ``ClassSeriesCreate``/``check_series_rule`` and ``ManualChargeCreate``.
The lookups, duplicates and commits are integration tests
(``test_imports_schedules.py``, ``test_imports_opening_balances.py``)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.modules.billing import imports as balances
from app.modules.scheduling import imports as schedules
from tests.conftest import frozen_clock

TODAY = date(2031, 3, 10)
MX = "America/Mexico_City"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", [1]),
        ("1 3", [1, 3]),
        ("  5   2 ", [5, 2]),
        ("1 1", [1, 1]),
        ("8", [8]),
        ("lunes", None),
        ("1,3", None),
        ("-1", None),
        ("1.5", None),
    ],
)
def test_weekdays_parse_to_integers_and_keep_rule_errors_for_the_rule(value, expected):
    assert schedules.parse_weekdays(value) == expected


def schedule_row(**change: str) -> dict[str, str]:
    row = {
        "student_given_names": "Ana",
        "student_family_names": "Pérez",
        "student_birthdate": "2015-01-01",
        "subject": "Matemáticas",
        "education_level": "Primaria",
        "starts_on": "2031-03-17",
        "start_time": "16:00",
        "duration_minutes": "60",
        "weekdays": "1 3",
    }
    row.update(change)
    return row


def schedule_issues(**change: str) -> set[tuple[str | None, str]]:
    parsed = schedules._parse(schedule_row(**change))
    issues = list(parsed.errors)
    if parsed.data is not None:
        with frozen_clock(datetime(2031, 3, 10, 12, tzinfo=ZoneInfo(MX))):
            _, rule = schedules._rule_body(parsed.data, MX)
        issues.extend(rule)
    return {(issue.column, issue.code) for issue in issues}


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({}, set()),
        ({"start_time": "9:30"}, set()),
        ({"until": "17/04/2031"}, set()),
        ({"subject": ""}, {("subject", "required")}),
        ({"starts_on": "2031/03/17"}, {("starts_on", "invalid_date")}),
        ({"start_time": "16:00:00"}, {("start_time", "time_format")}),
        ({"duration_minutes": "481"}, {("duration_minutes", "duration_out_of_range")}),
        ({"duration_minutes": "1h"}, {("duration_minutes", "invalid")}),
        ({"weekdays": "0"}, {("weekdays", "weekday_out_of_range")}),
        ({"weekdays": "3 3"}, {("weekdays", "weekdays_duplicate")}),
        ({"interval": "0"}, {("interval", "interval_out_of_range")}),
        ({"interval": "x"}, {("interval", "invalid")}),
        ({"until": "2031-04-01", "count": "2"}, {("until", "until_and_count")}),
        ({"count": "0"}, {("count", "count_out_of_range")}),
        ({"until": "2031-03-16"}, {("until", "until_before_start")}),
        ({"until": "2031-03-17", "weekdays": "2"}, {(None, "no_occurrences")}),
        ({"starts_on": "2031-03-09"}, {("starts_on", "starts_on_in_past")}),
        (
            {"start_time": "23:15", "duration_minutes": "60"},
            {("start_time", "crosses_local_date")},
        ),
        ({"start_time": "23:00", "duration_minutes": "60"}, set()),
        ({"tutor_username": "no valid!"}, {("tutor_username", "tutor_unknown")}),
    ],
)
def test_schedule_rule_codes(change, expected):
    assert schedule_issues(**change) == expected


def balance_row(**change: str) -> dict[str, str]:
    row = {
        "student_given_names": "Ana",
        "student_family_names": "Pérez",
        "student_birthdate": "01/01/2015",
        "description": "  Colegiatura  ",
        "amount": "1250.50",
        "service_date": "2031-03-01",
        "due_date": "2031-03-15",
    }
    row.update(change)
    return row


def balance_issues(**change: str) -> set[tuple[str | None, str]]:
    parsed = balances._parse(balance_row(**change), TODAY)
    return {(issue.column, issue.code) for issue in parsed.errors}


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("1", set()),
        ("1.5", set()),
        ("1.50", set()),
        ("999999999999.99", set()),
        ("0", {("amount", "amount_not_positive")}),
        ("0.00", {("amount", "amount_not_positive")}),
        ("1.505", {("amount", "invalid_amount")}),
        ("1,50", {("amount", "invalid_amount")}),
        ("1,000", {("amount", "invalid_amount")}),
        ("$5", {("amount", "invalid_amount")}),
        ("+5", {("amount", "invalid_amount")}),
        ("-5", {("amount", "invalid_amount")}),
        ("5e2", {("amount", "invalid_amount")}),
        (".5", {("amount", "invalid_amount")}),
        ("1000000000000", {("amount", "invalid_amount")}),
        ("", {("amount", "required")}),
    ],
)
def test_balance_amounts(amount, expected):
    assert balance_issues(amount=amount) == expected


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"service_date": "2031-03-10"}, set()),
        ({"service_date": "2031-03-11"}, {("service_date", "future_date")}),
        ({"service_date": "1999-12-31"}, {("service_date", "date_out_of_range")}),
        ({"due_date": "2032-03-10"}, set()),
        ({"due_date": "2032-03-11"}, {("due_date", "date_out_of_range")}),
        ({"due_date": "2031-13-01"}, {("due_date", "invalid_date")}),
        ({"description": "x" * 201}, {("description", "too_long")}),
        ({"description": "   "}, {("description", "required")}),
        ({"student_birthdate": "1/1/15"}, {("student_birthdate", "invalid_date")}),
    ],
)
def test_balance_rule_codes(change, expected):
    assert balance_issues(**change) == expected


def test_a_balance_body_is_trimmed():
    parsed = balances._parse(balance_row(), TODAY)
    assert parsed.body is not None
    assert parsed.body.description == "Colegiatura"
    assert str(parsed.body.amount) == "1250.50"
    assert parsed.student_key == ("Ana", "Pérez", date(2015, 1, 1))
