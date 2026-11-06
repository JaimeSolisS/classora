"""The M8-demo plan (#539 A7): :func:`plan_demo_admin` is pure, for ``T`` on
each weekday of one week, a month's 1st and a 31st."""

from datetime import date, timedelta

import pytest

from app.modules.administration.imports.parsing import parse_csv
from app.modules.billing import imports as balances_kind
from app.modules.families import imports as students_kind
from app.modules.identity import imports as tutors_kind
from app.modules.scheduling import imports as schedules_kind
from tests.factories.demo_admin import (
    IMPORTED_USERNAME_PREFIX,
    RECEIPT_MESSAGE_EN,
    RECEIPT_MESSAGE_ES,
    SERIES_COUNT,
    BookedSlot,
    StudentName,
    plan_demo_admin,
)
from tests.factories.demo_schedule import plan_demo_schedule, upcoming_weekdays

WEEK = [date(2026, 11, 2) + timedelta(days=k) for k in range(7)]
TODAYS = [*WEEK, date(2026, 12, 1), date(2026, 12, 31)]
DUPLICATE = StudentName("Andrés", "Ramírez Jáquez", date(2014, 8, 21))
F3 = StudentName("Delia", "Godoy Gaona", date(2000, 1, 1))


def _ids(today: date) -> str:
    return today.strftime("%a-%m-%d")


@pytest.mark.parametrize("today", TODAYS, ids=_ids)
def test_the_dates_follow_today(today):
    plan = plan_demo_admin(today)

    assert plan == plan_demo_admin(today)  # pure
    # The series: a weekday after T, its weekly dates off #299's busy days.
    assert plan.series_starts_on > today + timedelta(days=1)
    assert plan.series_starts_on.isoweekday() <= 5
    schedule = plan_demo_schedule(today)
    taken = {*upcoming_weekdays(today, 3), schedule.closure_day, schedule.time_off_day}
    dates = {plan.series_starts_on + timedelta(weeks=k) for k in range(SERIES_COUNT)}
    assert not dates & taken
    # The opening balances: served by T, due after it.
    for balance in plan.balances:
        assert balance.service_date <= today < balance.due_date
        assert balance.due_date >= today + timedelta(days=14)
        assert balance.due_date <= today + timedelta(days=366)
    # The Finance setup: the month so far.
    assert plan.incurred_from <= plan.incurred_to == today
    assert plan.incurred_from == today.replace(day=1)
    assert (plan.incurred_to - plan.incurred_from).days <= 366


@pytest.mark.parametrize("today", TODAYS, ids=_ids)
def test_the_csvs_parse_with_the_kinds_columns(today):
    plan = plan_demo_admin(today)
    conflict = BookedSlot(today + timedelta(days=2), "16:00", 60)
    files = [
        (plan.students_csv(DUPLICATE), students_kind.COLUMNS, 3),
        (plan.tutors_csv(), tutors_kind.COLUMNS, 2),
        (plan.schedules_csv(F3, conflict), schedules_kind.COLUMNS, 2),
        (plan.balances_csv(F3), balances_kind.COLUMNS, 2),
    ]
    for text, columns, rows in files:
        parsed = parse_csv(text, columns)
        assert len(parsed.rows) == rows
        assert list(parsed.ignored_columns) == []
        required = {column.name for column in columns if column.required}
        for row in parsed.rows:
            assert all(row.values.get(name) for name in required), row.values


def test_the_texts_are_fixed_and_fictional():
    plan = plan_demo_admin(WEEK[2])

    assert (plan.receipt_message_es, plan.receipt_message_en) == (
        RECEIPT_MESSAGE_ES,
        RECEIPT_MESSAGE_EN,
    )
    assert all(t["username"].startswith(IMPORTED_USERNAME_PREFIX) for t in plan.tutors)
    assert all(
        row["contact_phone"].startswith("55 5555 01") for row in plan.new_students
    )
    # The duplicate is the stored name upper-cased, without accents.
    text = plan.students_csv(DUPLICATE)
    duplicate = parse_csv(text, students_kind.COLUMNS).rows[-1].values
    assert (duplicate["given_names"], duplicate["family_names"]) == (
        "ANDRES",
        "RAMIREZ JAQUEZ",
    )
    assert duplicate["birthdate"] == DUPLICATE.birthdate.isoformat()


def test_the_conflicting_row_copies_the_booked_slot():
    today = WEEK[2]
    plan = plan_demo_admin(today)
    conflict = BookedSlot(date(2026, 11, 6), "16:00", 60)

    rows = parse_csv(plan.schedules_csv(F3, conflict), schedules_kind.COLUMNS).rows
    first, second = (row.values for row in rows)
    assert (first["starts_on"], first["start_time"], first["count"]) == (
        plan.series_starts_on.isoformat(),
        "19:00",
        str(SERIES_COUNT),
    )
    assert first["weekdays"] == str(plan.series_starts_on.isoweekday())
    assert (second["starts_on"], second["start_time"], second["weekdays"]) == (
        "2026-11-06",
        "16:00",
        "5",
    )
    assert second["duration_minutes"] == "60"
    assert first["tutor_username"] == second["tutor_username"] == "demo.tutor"
