"""The pure weekly recurrence rules of class series (#53 C6; ADR-0005 D4).

The DST cases use America/Tijuana: clocks fall back on 2026-11-01 at 02:00
(01:00-01:59 happens twice) and spring forward on 2026-03-08 at 02:00
(02:00-02:59 never happens).
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from dateutil.rrule import rrulestr

from app.modules.scheduling.recurrence import (
    InvalidRecurrenceError,
    RecurrenceFields,
    build_rrule,
    expand,
    first_covered_start,
    horizon_end,
    last_occurrence_on,
    validate_recurrence,
)
from app.modules.scheduling.times import scheduled_minutes

TIJUANA = "America/Tijuana"
MEXICO_CITY = "America/Mexico_City"


def fields(
    weekdays: tuple[int, ...] = (1,),
    interval: int = 1,
    until_on: date | None = None,
    count: int | None = None,
) -> RecurrenceFields:
    return RecurrenceFields(
        weekdays=weekdays, interval=interval, until_on=until_on, count=count
    )


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def run(
    rule: RecurrenceFields,
    *,
    starts_on: date,
    start: str,
    zone: str = TIJUANA,
    minutes: int = 60,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
):
    return expand(
        build_rrule(rule),
        starts_on=starts_on,
        start_time=time.fromisoformat(start),
        duration_minutes=minutes,
        zone=zone,
        window_start=window_start or utc(2000, 1, 1),
        window_end=window_end or utc(2100, 1, 1),
    )


# --- C6 DST cases --------------------------------------------------------------------


def test_an_ambiguous_local_time_takes_the_first_instant():
    # Sundays 01:30; 2026-11-01 01:30 happens in PDT (UTC-7) and again in PST.
    found = run(fields((7,), count=3), starts_on=date(2026, 10, 25), start="01:30")

    assert [o.starts_at for o in found] == [
        utc(2026, 10, 25, 8, 30),
        utc(2026, 11, 1, 8, 30),  # the first instant (PDT), not 09:30Z
        utc(2026, 11, 8, 9, 30),
    ]
    assert [o.local_date for o in found] == [
        date(2026, 10, 25),
        date(2026, 11, 1),
        date(2026, 11, 8),
    ]


def test_a_nonexistent_local_time_moves_forward_by_the_gap():
    # Sundays 02:30; 2026-03-08 02:30 never happens in Tijuana.
    found = run(fields((7,), count=3), starts_on=date(2026, 3, 1), start="02:30")

    assert [o.starts_at for o in found] == [
        utc(2026, 3, 1, 10, 30),
        utc(2026, 3, 8, 10, 30),  # 03:30 PDT
        utc(2026, 3, 15, 9, 30),
    ]
    local = found[1].starts_at.astimezone(ZoneInfo(TIJUANA))
    assert (local.hour, local.minute) == (3, 30)


def test_the_local_time_stays_while_utc_shifts_across_both_changes():
    found = run(
        fields((2,), until_on=date(2026, 11, 17)),
        starts_on=date(2026, 3, 3),
        start="16:00",
    )
    zone = ZoneInfo(TIJUANA)

    assert {o.starts_at.astimezone(zone).time() for o in found} == {time(16)}
    by_date = {o.local_date: o.starts_at for o in found}
    assert by_date[date(2026, 3, 3)] == utc(2026, 3, 4, 0, 0)  # PST
    assert by_date[date(2026, 3, 10)] == utc(2026, 3, 10, 23, 0)  # PDT
    assert by_date[date(2026, 10, 27)] == utc(2026, 10, 27, 23, 0)  # PDT
    assert by_date[date(2026, 11, 3)] == utc(2026, 11, 4, 0, 0)  # PST


def test_a_duration_across_the_repeated_hour_is_elapsed_time():
    # 01:00 for 90 minutes on 2026-11-01: 01:00 PDT -> 01:30 PST (local
    # wall clock), 90 minutes elapsed.
    [occurrence] = run(
        fields((7,), count=1), starts_on=date(2026, 11, 1), start="01:00", minutes=90
    )

    assert occurrence.starts_at == utc(2026, 11, 1, 8, 0)
    assert occurrence.ends_at == utc(2026, 11, 1, 9, 30)
    assert scheduled_minutes(occurrence.starts_at, occurrence.ends_at) == 90
    local_end = occurrence.ends_at.astimezone(ZoneInfo(TIJUANA))
    assert (local_end.hour, local_end.minute, local_end.fold) == (1, 30, 1)


def test_mexico_city_has_no_shift():
    found = run(
        fields((1,), until_on=date(2026, 12, 28)),
        starts_on=date(2026, 1, 5),
        start="16:00",
        zone=MEXICO_CITY,
    )

    assert {o.starts_at.time() for o in found} == {time(22)}
    assert len(found) == 52


def test_original_start_equals_the_start_and_the_end_follows_the_duration():
    [first, *_] = run(
        fields((1,), count=2), starts_on=date(2026, 11, 2), start="09:15", minutes=45
    )

    assert first.original_start == first.starts_at
    assert first.ends_at - first.starts_at == timedelta(minutes=45)


# --- Rule shapes -------------------------------------------------------------------


def test_interval_2_with_monday_and_thursday_uses_monday_weeks():
    # Starts on Wednesday 2026-11-04: the first week (Mon 11-02) keeps only
    # Thursday, the next week is skipped.
    found = run(
        fields((1, 4), interval=2, count=5),
        starts_on=date(2026, 11, 4),
        start="10:00",
        zone=MEXICO_CITY,
    )

    assert [o.local_date for o in found] == [
        date(2026, 11, 5),
        date(2026, 11, 16),
        date(2026, 11, 19),
        date(2026, 11, 30),
        date(2026, 12, 3),
    ]


def test_wkst_monday_decides_the_interval_weeks():
    # With a Sunday week start, Sunday 11-08 and Monday 11-09 would share a
    # week; with WKST=MO they fall in different two-week blocks.
    found = run(
        fields((1, 7), interval=2, count=4),
        starts_on=date(2026, 11, 2),
        start="10:00",
        zone=MEXICO_CITY,
    )

    assert [o.local_date for o in found] == [
        date(2026, 11, 2),
        date(2026, 11, 8),
        date(2026, 11, 16),
        date(2026, 11, 22),
    ]


def test_until_is_inclusive_on_a_matching_day():
    found = run(
        fields((3,), until_on=date(2026, 11, 25)),
        starts_on=date(2026, 11, 4),
        start="23:00",
        zone=MEXICO_CITY,
    )

    assert [o.local_date for o in found][-1] == date(2026, 11, 25)
    assert len(found) == 4


@pytest.mark.parametrize("count", [1, 200])
def test_count_bounds(count):
    found = run(
        fields((1, 3, 5), count=count),
        starts_on=date(2026, 11, 2),
        start="10:00",
        zone=MEXICO_CITY,
    )

    assert len(found) == count


def test_a_start_date_off_the_weekdays_begins_on_the_next_match():
    found = run(
        fields((1,), count=1),
        starts_on=date(2026, 11, 4),
        start="10:00",
        zone=MEXICO_CITY,
    )

    assert [o.local_date for o in found] == [date(2026, 11, 9)]


def test_window_bounds_are_half_open():
    starts = [
        o.starts_at
        for o in run(
            fields((1,), count=3),
            starts_on=date(2026, 11, 2),
            start="10:00",
            zone=MEXICO_CITY,
        )
    ]

    inside = run(
        fields((1,), count=3),
        starts_on=date(2026, 11, 2),
        start="10:00",
        zone=MEXICO_CITY,
        window_start=starts[0],
        window_end=starts[2],
    )

    assert [o.starts_at for o in inside] == starts[:2]


def test_an_open_ended_rule_expands_only_the_window():
    found = run(
        fields((1, 3)),
        starts_on=date(2031, 1, 6),
        start="16:00",
        zone=MEXICO_CITY,
        window_start=datetime(2031, 1, 5, 12, tzinfo=ZoneInfo(MEXICO_CITY)),
        window_end=horizon_end(
            datetime(2031, 1, 5, 12, tzinfo=ZoneInfo(MEXICO_CITY)), 12
        ),
    )

    assert len(found) == 24
    assert found[-1].local_date == date(2031, 3, 26)


def test_horizon_end_adds_whole_weeks():
    now = utc(2026, 10, 21, 8)

    assert horizon_end(now, 12) == utc(2027, 1, 13, 8)


# --- build_rrule ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        (fields((3, 1)), "FREQ=WEEKLY;INTERVAL=1;WKST=MO;BYDAY=MO,WE"),
        (
            fields((7,), interval=4, until_on=date(2026, 12, 18)),
            "FREQ=WEEKLY;INTERVAL=4;WKST=MO;BYDAY=SU;UNTIL=20261218T235959",
        ),
        (
            fields((1, 2, 3, 4, 5, 6, 7), interval=2, count=10),
            "FREQ=WEEKLY;INTERVAL=2;WKST=MO;BYDAY=MO,TU,WE,TH,FR,SA,SU;COUNT=10",
        ),
    ],
)
def test_build_rrule_text(rule, text):
    assert build_rrule(rule) == text


@pytest.mark.parametrize(
    "rule",
    [
        fields((1, 3)),
        fields((2, 4), interval=3, until_on=date(2027, 3, 1)),
        fields((5, 6, 7), interval=2, count=17),
    ],
)
def test_the_text_round_trips_through_rrulestr(rule):
    starts_on = date(2026, 11, 4)
    dtstart = datetime.combine(starts_on, time(16))
    parsed = rrulestr(build_rrule(rule), dtstart=dtstart)
    end = datetime(2027, 6, 1)
    expected = [
        o.local_date
        for o in run(rule, starts_on=starts_on, start="16:00", zone=MEXICO_CITY)
        if o.local_date < end.date()
    ]

    assert [d.date() for d in parsed.between(dtstart, end, inc=True)] == expected
    if rule.count is None and rule.until_on is None:
        # Spot-check the open rule independently: the first dates.
        assert expected[:3] == [
            date(2026, 11, 4),
            date(2026, 11, 9),
            date(2026, 11, 11),
        ]


# --- validate_recurrence and last_occurrence_on --------------------------------------


@pytest.mark.parametrize(
    ("rule", "field", "reason"),
    [
        (fields(()), "weekdays", "weekdays_empty"),
        (fields((0,)), "weekdays", "weekday_out_of_range"),
        (fields((8,)), "weekdays", "weekday_out_of_range"),
        (fields((1, 1)), "weekdays", "weekdays_duplicate"),
        (fields(interval=0), "interval", "interval_out_of_range"),
        (fields(interval=5), "interval", "interval_out_of_range"),
        (fields(until_on=date(2026, 12, 1), count=3), "until", "until_and_count"),
        (fields(count=0), "count", "count_out_of_range"),
        (fields(count=201), "count", "count_out_of_range"),
        (fields(until_on=date(2026, 11, 1)), "until", "until_before_start"),
    ],
)
def test_every_invalid_recurrence_reason(rule, field, reason):
    with pytest.raises(InvalidRecurrenceError) as caught:
        validate_recurrence(rule, date(2026, 11, 2))

    assert (caught.value.field, caught.value.reason) == (field, reason)


def test_valid_rules_pass():
    validate_recurrence(fields((1, 7), interval=4, count=200), date(2026, 11, 2))
    validate_recurrence(fields(until_on=date(2026, 11, 2)), date(2026, 11, 2))


def test_last_occurrence_on():
    assert last_occurrence_on(fields((1,)), date(2026, 11, 2)) is None
    assert last_occurrence_on(fields((1, 3), count=3), date(2026, 11, 2)) == date(
        2026, 11, 9
    )
    assert last_occurrence_on(
        fields((1,), until_on=date(2026, 11, 22)), date(2026, 11, 2)
    ) == date(2026, 11, 16)


def test_a_finite_rule_without_occurrences_is_rejected():
    # Until Tuesday 11-03, Mondays only, starting Tuesday 11-03.
    with pytest.raises(InvalidRecurrenceError) as caught:
        last_occurrence_on(fields((1,), until_on=date(2026, 11, 3)), date(2026, 11, 3))

    assert (caught.value.field, caught.value.reason) == ("recurrence", "no_occurrences")


# --- first_covered_start (#233 B4) ---------------------------------------------------


def covered(
    rule: RecurrenceFields,
    *,
    starts_on: date,
    from_date: date,
    now: datetime,
    zone: str = MEXICO_CITY,
    start_time: time = time(16),
) -> datetime | None:
    return first_covered_start(
        build_rrule(rule),
        starts_on=starts_on,
        start_time=start_time,
        zone=zone,
        from_date=from_date,
        now=now,
    )


def test_the_first_covered_start_is_the_first_occurrence_from_the_date():
    # Monday 2026-10-26 and Wednesday 2026-10-28, weekly, open-ended.
    rule = fields((1, 3))
    now = utc(2026, 10, 20, 12)
    assert covered(
        rule, starts_on=date(2026, 10, 26), from_date=date(2026, 10, 26), now=now
    ) == utc(2026, 10, 26, 22)
    # From a Tuesday: the Wednesday.
    assert covered(
        rule, starts_on=date(2026, 10, 26), from_date=date(2026, 10, 27), now=now
    ) == utc(2026, 10, 28, 22)
    # Far beyond any horizon (open-ended).
    assert covered(
        rule, starts_on=date(2026, 10, 26), from_date=date(2028, 1, 1), now=now
    ) == utc(2028, 1, 3, 22)


def test_the_from_date_itself_is_covered_but_a_started_occurrence_is_not():
    rule = fields((1,))
    monday = date(2026, 10, 26)
    # 16:00 local is 22:00Z: at 21:59Z it is still to come, at 22:00Z not.
    assert covered(
        rule, starts_on=monday, from_date=monday, now=utc(2026, 10, 26, 21, 59)
    ) == utc(2026, 10, 26, 22)
    assert covered(
        rule, starts_on=monday, from_date=monday, now=utc(2026, 10, 26, 22)
    ) == utc(2026, 11, 2, 22)


def test_a_finite_rule_ends():
    monday = date(2026, 10, 26)
    now = utc(2026, 10, 20)
    assert covered(
        fields((1,), count=2), starts_on=monday, from_date=date(2026, 11, 2), now=now
    ) == utc(2026, 11, 2, 22)
    assert (
        covered(
            fields((1,), count=2),
            starts_on=monday,
            from_date=date(2026, 11, 3),
            now=now,
        )
        is None
    )
    assert (
        covered(
            fields((1,), until_on=date(2026, 11, 9)),
            starts_on=monday,
            from_date=date(2026, 11, 10),
            now=now,
        )
        is None
    )


def test_an_interval_keeps_the_rule_weeks():
    # Every 2 weeks from Monday 2026-10-26: 11-09 is a rule week, 11-02 not.
    assert covered(
        fields((1,), interval=2),
        starts_on=date(2026, 10, 26),
        from_date=date(2026, 10, 27),
        now=utc(2026, 10, 20),
    ) == utc(2026, 11, 9, 22)


def test_the_first_covered_start_follows_the_dst_rule_in_tijuana():
    # Sunday 2026-11-01: 01:30 happens twice; fold=0 takes the first (PDT).
    assert covered(
        fields((7,)),
        starts_on=date(2026, 10, 25),
        from_date=date(2026, 11, 1),
        now=utc(2026, 10, 30),
        zone=TIJUANA,
        start_time=time(1, 30),
    ) == utc(2026, 11, 1, 8, 30)
    # Sunday 2027-03-14: 02:30 doesn't exist; it moves forward to 03:30 PDT.
    assert covered(
        fields((7,)),
        starts_on=date(2027, 3, 7),
        from_date=date(2027, 3, 8),
        now=utc(2027, 3, 1),
        zone=TIJUANA,
        start_time=time(2, 30),
    ) == utc(2027, 3, 14, 10, 30)
