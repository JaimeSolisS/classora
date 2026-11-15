"""Progress report schedule rules (#316 A4, F1): periods, ``starts_on``,
reactivation, eligibility, the latest-period status and the instant →
center-local ``today`` step. No database."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.modules.academics import progress_report_schedule_rules as rules
from app.modules.academics.progress_report_rules import period_for

MONDAY = date(2026, 10, 19)
WEEK = [MONDAY + timedelta(days=n) for n in range(7)]


# --- Periods --------------------------------------------------------------------------


@pytest.mark.parametrize("today", WEEK)
def test_every_weekday_ends_the_previous_week(today):
    assert rules.current_period_start("weekly", today) == MONDAY
    assert rules.latest_ended_period("weekly", today) == (
        date(2026, 10, 12),
        date(2026, 10, 18),
    )


@pytest.mark.parametrize(
    ("today", "start", "end"),
    [
        (date(2026, 10, 1), date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 10, 31), date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 2, 1), date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 3, 15), date(2026, 2, 1), date(2026, 2, 28)),
        (date(2028, 3, 1), date(2028, 2, 1), date(2028, 2, 29)),
        (date(2026, 5, 31), date(2026, 4, 1), date(2026, 4, 30)),
        (date(2026, 1, 15), date(2025, 12, 1), date(2025, 12, 31)),
    ],
)
def test_the_latest_ended_month(today, start, end):
    assert rules.current_period_start("monthly", today) == today.replace(day=1)
    assert rules.latest_ended_period("monthly", today) == (start, end)
    assert period_for("monthly", start, None) == (start, end)


def test_the_year_boundary_week():
    # ISO week 1 of 2026 starts on Monday 29 Dec 2025.
    assert rules.latest_ended_period("weekly", date(2026, 1, 5)) == (
        date(2025, 12, 29),
        date(2026, 1, 4),
    )
    assert rules.current_period_start("weekly", date(2026, 1, 1)) == date(2025, 12, 29)
    assert rules.latest_ended_period("weekly", date(2026, 1, 1)) == (
        date(2025, 12, 22),
        date(2025, 12, 28),
    )


def test_a_period_is_due_from_starts_on():
    today = date(2026, 10, 21)
    assert rules.due_period("weekly", date(2026, 10, 12), today) == (
        date(2026, 10, 12),
        date(2026, 10, 18),
    )
    assert rules.due_period("weekly", date(2026, 10, 5), today) is not None
    # Created this week: the first placeholder after this week ends.
    assert rules.due_period("weekly", MONDAY, today) is None
    assert rules.due_period("monthly", date(2026, 9, 1), today) == (
        date(2026, 9, 1),
        date(2026, 9, 30),
    )
    assert rules.due_period("monthly", date(2026, 10, 1), today) is None


# --- starts_on ------------------------------------------------------------------------

TODAY = date(2026, 10, 21)  # a Wednesday


@pytest.mark.parametrize(
    ("cadence", "starts_on", "valid"),
    [
        ("weekly", MONDAY, True),  # today's period
        ("weekly", date(2026, 10, 26), True),
        ("weekly", date(2026, 10, 20), False),  # a Tuesday
        ("weekly", date(2026, 10, 12), False),  # the previous period
        ("weekly", date(2027, 10, 18), True),  # 362 days ahead
        ("weekly", date(2027, 10, 25), False),  # 369 days ahead
        ("monthly", date(2026, 10, 1), True),
        ("monthly", date(2026, 10, 2), False),  # the 2nd
        ("monthly", date(2026, 9, 1), False),  # the previous period
        ("monthly", date(2027, 10, 1), True),  # 345 days ahead
        ("monthly", date(2027, 11, 1), False),  # 376 days ahead
    ],
)
def test_starts_on(cadence, starts_on, valid):
    expected = None if valid else "schedule_starts_on_invalid"
    assert rules.starts_on_error(cadence, starts_on, TODAY) == expected


def test_starts_on_366_and_367_days_ahead():
    # A Monday exactly 366 days after a Saturday today, then 367 days.
    today = date(2026, 10, 24)
    monday_366 = today + timedelta(days=366)
    assert monday_366.isoweekday() == 1
    assert rules.starts_on_error("weekly", monday_366, today) is None
    today_367 = today - timedelta(days=1)
    assert rules.starts_on_error("weekly", monday_366, today_367) == (
        "schedule_starts_on_invalid"
    )
    first = date(2027, 9, 1)
    assert rules.starts_on_error("monthly", first, first - timedelta(days=366)) is None
    assert rules.starts_on_error("monthly", first, first - timedelta(days=367)) == (
        "schedule_starts_on_invalid"
    )


def test_reactivation_never_reaches_back():
    assert rules.reactivated_starts_on("weekly", date(2026, 9, 7), TODAY) == MONDAY
    assert rules.reactivated_starts_on("weekly", date(2026, 11, 2), TODAY) == date(
        2026, 11, 2
    )
    assert rules.reactivated_starts_on("monthly", date(2026, 1, 1), TODAY) == date(
        2026, 10, 1
    )


# --- Eligibility ----------------------------------------------------------------------

ELIGIBLE = {
    "student_active": True,
    "author_tutor": True,
    "author_current": True,
    "subject_set": True,
    "subject_active": True,
    "subject_assigned": True,
}


@pytest.mark.parametrize(
    ("failing", "expected"),
    [
        ((), None),
        (("student_active", "author_tutor"), "student_inactive"),
        (("author_tutor", "author_current"), "author_not_tutor"),
        (("author_current", "subject_active"), "author_not_current"),
        (("subject_active", "subject_assigned"), "subject_inactive"),
        (("subject_assigned",), "subject_not_assigned"),
    ],
)
def test_eligibility_order(failing, expected):
    facts = ELIGIBLE | dict.fromkeys(failing, False)
    assert rules.eligibility(**facts) == expected


def test_a_schedule_without_subject_ignores_the_subject_rules():
    facts = ELIGIBLE | {
        "subject_set": False,
        "subject_active": False,
        "subject_assigned": False,
    }
    assert rules.eligibility(**facts) is None


# --- Latest status (B3) ---------------------------------------------------------------


def report(status: str, *, deleted: bool = False) -> rules.PeriodReport:
    return rules.PeriodReport(id=f"id-{status}", status=status, deleted=deleted)


@pytest.mark.parametrize(
    ("placeholder", "manual", "expected"),
    [
        (None, None, ("not_created", None)),
        (report("draft"), None, ("draft", None)),
        (report("draft", deleted=True), None, ("deleted", None)),
        (report("changes_requested", deleted=True), None, ("deleted", None)),
        (report("submitted"), None, ("submitted", "id-submitted")),
        (
            report("changes_requested"),
            None,
            ("changes_requested", "id-changes_requested"),
        ),
        (
            report("approved_for_family"),
            None,
            ("approved_for_family", "id-approved_for_family"),
        ),
        (report("retracted"), None, ("retracted", "id-retracted")),
        (None, report("draft"), ("draft", None)),
        (None, report("submitted"), ("submitted", "id-submitted")),
        # The schedule's own placeholder wins over a matching report.
        (report("draft", deleted=True), report("submitted"), ("deleted", None)),
    ],
)
def test_latest_status(placeholder, manual, expected):
    assert rules.latest_status(placeholder, manual) == expected


# --- The instant → today step ---------------------------------------------------------


def at(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


@pytest.mark.parametrize(
    ("zone", "instant", "today"),
    [
        # Mexico City (UTC-6, no DST since 2022): Sunday 23:30 local is
        # Monday 05:30 UTC and still ends the previous week.
        ("America/Mexico_City", at(2026, 10, 26, 5, 30), date(2026, 10, 25)),
        ("America/Mexico_City", at(2026, 10, 26, 6, 30), date(2026, 10, 26)),
        # Tijuana, DST starts on Sunday 8 March 2026 (UTC-7 after).
        ("America/Tijuana", at(2026, 3, 9, 6, 30), date(2026, 3, 8)),
        ("America/Tijuana", at(2026, 3, 9, 7, 30), date(2026, 3, 9)),
        # Tijuana, DST ends on Sunday 1 November 2026 (UTC-8 after).
        ("America/Tijuana", at(2026, 11, 2, 7, 30), date(2026, 11, 1)),
        ("America/Tijuana", at(2026, 11, 2, 8, 30), date(2026, 11, 2)),
        # Tokyo (UTC+9): a UTC Sunday evening is already Monday.
        ("Asia/Tokyo", at(2026, 10, 25, 20, 0), date(2026, 10, 26)),
    ],
)
def test_the_center_local_today(zone, instant, today):
    assert rules.local_today(instant, zone) == today


def test_sunday_night_in_mexico_city_fills_the_previous_week():
    sunday_night = rules.local_today(at(2026, 10, 26, 5, 30), "America/Mexico_City")
    monday = rules.local_today(at(2026, 10, 26, 6, 30), "America/Mexico_City")
    assert rules.latest_ended_period("weekly", sunday_night) == (
        date(2026, 10, 12),
        date(2026, 10, 18),
    )
    assert rules.latest_ended_period("weekly", monday) == (
        date(2026, 10, 19),
        date(2026, 10, 25),
    )
    tokyo = rules.local_today(at(2026, 10, 25, 20, 0), "Asia/Tokyo")
    assert rules.latest_ended_period("weekly", tokyo) == (
        date(2026, 10, 19),
        date(2026, 10, 25),
    )
