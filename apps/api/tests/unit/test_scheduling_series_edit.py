"""The pure parts of the series split algorithm (#54 C2, E6; ADR-0005 D4).

Matching is by **local date**. The DST cases use America/Tijuana: clocks
fall back on 2026-11-01 (a Sunday) and spring forward on 2026-03-08; a
late-evening class there is on the next UTC date, so a match by UTC date
would pair the wrong rows.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.modules.scheduling.recurrence import RecurrenceFields, build_rrule, expand
from app.modules.scheduling.series_edit import (
    EditPlan,
    ExistingRow,
    classify,
    in_scope,
    last_rule_date_before,
)

TIJUANA = "America/Tijuana"


def row(day: date, state: str = "confirmed", *, started: bool = False) -> ExistingRow:
    return ExistingRow(id=uuid.uuid4(), state=state, local_date=day, started=started)


def ids(plan_part: tuple[tuple[uuid.UUID, date], ...]) -> list[uuid.UUID]:
    return [row_id for row_id, _ in plan_part]


def weekly(weekdays: tuple[int, ...], start: date, hhmm: str, weeks: int = 3) -> list:
    rule = build_rrule(
        RecurrenceFields(weekdays=weekdays, interval=1, until_on=None, count=None)
    )
    window_start = datetime.combine(start, time(0), tzinfo=ZoneInfo(TIJUANA))
    return expand(
        rule,
        starts_on=start,
        start_time=time.fromisoformat(hhmm),
        duration_minutes=60,
        zone=TIJUANA,
        window_start=window_start,
        window_end=window_start + timedelta(weeks=weeks),
    )


# --- in_scope ----------------------------------------------------------------------


def test_in_scope_keeps_rows_on_or_after_the_date_that_have_not_started():
    d = date(2026, 10, 26)
    before = row(d - timedelta(days=1))
    on = row(d)
    started = row(d + timedelta(days=2), started=True)
    later = row(d + timedelta(days=7), "cancelled")

    assert in_scope([later, started, before, on], d) == [on, later]


# --- classify ----------------------------------------------------------------------


def test_matched_dates_move_and_unmatched_movable_rows_are_cancelled():
    monday, wednesday = date(2026, 10, 26), date(2026, 10, 28)
    rows = [
        row(monday, "confirmed"),
        row(wednesday, "offered"),
        row(monday + timedelta(weeks=1), "unassigned"),
        row(wednesday + timedelta(weeks=1), "conflict"),
    ]
    plan = classify(rows, [monday, monday + timedelta(weeks=1)])

    assert ids(plan.moved) == [rows[0].id, rows[2].id]
    assert list(plan.cancelled) == [rows[1].id, rows[3].id]
    assert plan.repointed == () and plan.untouched == () and plan.new_dates == ()


def test_a_matched_cancelled_row_is_repointed_and_its_date_is_taken():
    monday = date(2026, 10, 26)
    cancelled = row(monday, "cancelled")
    plan = classify([cancelled], [monday, monday + timedelta(days=1)])

    assert plan.repointed == ((cancelled.id, monday),)
    assert plan.new_dates == (monday + timedelta(days=1),)
    assert plan.moved == () and plan.cancelled == ()


def test_skipped_rows_move_when_matched_and_stay_when_not():
    monday = date(2026, 10, 26)
    matched = row(monday, "skipped_closure")
    unmatched = row(monday + timedelta(days=2), "skipped_closure")
    unmatched_cancelled = row(monday + timedelta(days=9), "cancelled")
    plan = classify([matched, unmatched, unmatched_cancelled], [monday])

    assert plan.moved == ((matched.id, monday),)
    assert list(plan.untouched) == [unmatched.id, unmatched_cancelled.id]
    assert plan.cancelled == ()


def test_a_completed_row_is_untouched_and_keeps_its_date():
    monday = date(2026, 10, 26)
    done = row(monday, "completed")
    plan = classify([done], [monday])

    assert plan == EditPlan(untouched=(done.id,))


def test_new_dates_without_an_old_row_are_inserted_in_order():
    tuesday, thursday = date(2026, 10, 27), date(2026, 10, 29)
    plan = classify([], [thursday, tuesday])

    assert plan.new_dates == (tuesday, thursday)


# --- local dates across DST (Tijuana) ---------------------------------------------


def test_rows_are_matched_by_local_date_across_the_fall_back():
    # Mon/Wed 23:00 local (the next UTC date) around 2026-11-01.
    start = date(2026, 10, 26)
    old = weekly((1, 3), start, "23:00")
    assert old[0].starts_at.astimezone(UTC).date() == date(2026, 10, 27)
    rows = [row(o.local_date) for o in old]

    # The same weekdays at 22:30 keep every date: every row moves.
    new = weekly((1, 3), start, "22:30")
    plan = classify(rows, [o.local_date for o in new])

    assert ids(plan.moved) == [r.id for r in rows]
    assert plan.cancelled == () and plan.new_dates == ()
    # The week after the change is still matched (UTC offset -8, not -7).
    after = [o for o in new if o.local_date == date(2026, 11, 2)]
    assert after[0].starts_at == datetime(2026, 11, 3, 6, 30, tzinfo=UTC)


def test_moving_to_other_weekdays_across_the_spring_forward():
    start = date(2026, 3, 2)
    old = weekly((1, 3), start, "23:00")
    rows = [row(o.local_date) for o in old]

    new = weekly((2, 4), start, "18:00")
    plan = classify(rows, [o.local_date for o in new])

    assert plan.moved == ()
    assert sorted(plan.cancelled) == sorted(r.id for r in rows)
    assert plan.new_dates == tuple(o.local_date for o in new)


# --- the old series' end ------------------------------------------------------------


def test_last_rule_date_before_a_split_date():
    rule = build_rrule(
        RecurrenceFields(weekdays=(1, 3), interval=1, until_on=None, count=None)
    )
    monday = date(2026, 10, 26)

    assert last_rule_date_before(
        rule, starts_on=monday, before=monday + timedelta(14)
    ) == monday + timedelta(9)
    assert last_rule_date_before(rule, starts_on=monday, before=monday) is None
