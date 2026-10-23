"""The pure family slot rules (#59 B3, B4, B6): the 30-minute grid, DST, the
notice boundary, each reason a candidate is hidden, the tutor-agnostic
"at least one eligible tutor" rule and the ``already_requested`` hint."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.modules.administration.service import SchedulingPolicyValues
from app.modules.scheduling.family_slots import (
    FamilySlotContext,
    TutorSlotFacts,
    available_days,
    candidates_on,
    family_candidates,
    family_slot_ok,
)
from app.modules.scheduling.suggestions import (
    Busy,
    Candidate,
    OperatingDay,
    SlotContext,
)
from app.modules.staff.service import (
    DatedException,
    TeachingLimits,
    TutorAvailabilityRules,
    WeeklyWindow,
)

MX_ZONE = "America/Mexico_City"
MX = ZoneInfo(MX_ZONE)
TJ_ZONE = "America/Tijuana"
TJ = ZoneInfo(TJ_ZONE)
MONDAY = date(2031, 1, 6)
TUESDAY = MONDAY + timedelta(days=1)
SUNDAY = MONDAY + timedelta(days=6)
NOW = datetime(2030, 12, 1, 12, 0, tzinfo=UTC)

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
WEEKDAY_HOURS = tuple(OperatingDay(day, "08:00", "20:00") for day in range(1, 7))
ALL_DAY = TutorAvailabilityRules(
    windows=tuple(WeeklyWindow(day, time(0), time(23, 59)) for day in range(1, 8)),
    exceptions=(),
)
NO_WINDOWS = TutorAvailabilityRules(windows=(), exceptions=())


def at(day: date, hhmm: str, zone: ZoneInfo = MX) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=zone).astimezone(UTC)


def cand(day: date, hhmm: str, minutes: int = 60) -> Candidate:
    start = at(day, hhmm)
    return Candidate(start, start + timedelta(minutes=minutes))


def busy(day: date, hhmm: str, minutes: int = 60) -> Busy:
    start = at(day, hhmm)
    return Busy(uuid.uuid4(), start, start + timedelta(minutes=minutes))


def tutor(
    rules: TutorAvailabilityRules = ALL_DAY, sessions: tuple[Busy, ...] = ()
) -> TutorSlotFacts:
    return TutorSlotFacts(user_id=uuid.uuid4(), rules=rules, sessions=sessions)


def context(
    *,
    tutors: tuple[TutorSlotFacts, ...] | None = None,
    pending: tuple[tuple[datetime, datetime], ...] = (),
    zone: str = MX_ZONE,
    hours: tuple[OperatingDay, ...] = WEEKDAY_HOURS,
    **slot: Any,
) -> FamilySlotContext:
    """Everything passes by default: no sessions, one tutor available all
    week, open 08:00-20:00 Monday to Saturday, far beyond the notice."""
    values: dict[str, Any] = {
        "zone": zone,
        "kind": "individual",
        "student_id": uuid.uuid4(),
        "subject_id": uuid.uuid4(),
        "tutor_user_id": None,
        "classroom_id": None,
        "student_sessions": (),
        "tutor_sessions": (),
        "room_sessions": (),
        "tutor_rules": None,
        "tutor_qualified": True,
        "limits": TeachingLimits(),
        "room_windows": (),
        "room_capacity": None,
        "closed_dates": frozenset(),
        "operating_hours": hours,
        "policy": POLICY,
        "now": NOW,
    } | slot
    return FamilySlotContext(
        slot=SlotContext(**values),
        tutors=(tutor(),) if tutors is None else tutors,
        pending=pending,
    )


def local_starts(candidates: list[Candidate], zone: ZoneInfo = MX) -> list[str]:
    return [c.starts_at.astimezone(zone).strftime("%H:%M") for c in candidates]


def shown(ctx: FamilySlotContext, day: date = MONDAY, minutes: int = 60) -> list[str]:
    [(found_day, slots)] = available_days(ctx, day, day, minutes)
    assert found_day == day
    zone = ZoneInfo(ctx.slot.zone)
    return [c.starts_at.astimezone(zone).strftime("%H:%M") for c, _ in slots]


# --- B3: candidates -----------------------------------------------------------------


def test_the_grid_runs_from_the_opening_rounded_up_to_the_last_start():
    hours = (OperatingDay(1, "08:15", "12:00"),)

    assert local_starts(candidates_on(MONDAY, MX_ZONE, 60, hours)) == [
        "08:30",
        "09:00",
        "09:30",
        "10:00",
        "10:30",
        "11:00",
    ]
    assert local_starts(candidates_on(MONDAY, MX_ZONE, 90, hours))[-1] == "10:30"


def test_a_start_on_the_grid_ending_exactly_at_closing_is_a_candidate():
    found = candidates_on(MONDAY, MX_ZONE, 120, WEEKDAY_HOURS)

    assert local_starts(found)[0] == "08:00"
    assert local_starts(found)[-1] == "18:00"


def test_a_closed_weekday_and_a_closure_date_give_no_candidates():
    assert candidates_on(SUNDAY, MX_ZONE, 60, WEEKDAY_HOURS) == []
    assert candidates_on(MONDAY, MX_ZONE, 60, WEEKDAY_HOURS, {MONDAY}) == []
    by_day = family_candidates(MONDAY, SUNDAY, MX_ZONE, 60, WEEKDAY_HOURS, {TUESDAY})
    assert list(by_day) == [MONDAY + timedelta(days=n) for n in range(7)]
    assert by_day[TUESDAY] == [] and by_day[SUNDAY] == []
    assert by_day[MONDAY]


def test_the_spring_forward_gap_is_skipped():
    # America/Tijuana jumps from 02:00 to 03:00 on Sunday 2031-03-09.
    spring = date(2031, 3, 9)
    hours = (OperatingDay(7, "00:00", "23:59"),)

    starts = local_starts(candidates_on(spring, TJ_ZONE, 30, hours), TJ)

    assert "01:30" in starts and "03:00" in starts
    assert "02:00" not in starts and "02:30" not in starts


def test_the_fall_back_hour_uses_the_first_instant():
    # America/Tijuana repeats 01:00-02:00 on Sunday 2031-11-02 (fold=0 is
    # PDT, UTC-7).
    fall = date(2031, 11, 2)
    hours = (OperatingDay(7, "00:00", "23:59"),)

    found = candidates_on(fall, TJ_ZONE, 30, hours)
    at_one = [
        c for c in found if c.starts_at.astimezone(TJ).strftime("%H:%M") == "01:00"
    ]

    assert [c.starts_at for c in at_one] == [datetime(2031, 11, 2, 8, 0, tzinfo=UTC)]


# --- B4: the checks -----------------------------------------------------------------


def test_exactly_the_notice_is_shown_and_one_minute_under_is_hidden():
    exactly = at(MONDAY, "10:00") - timedelta(hours=24)

    assert "10:00" in shown(context(now=exactly))
    assert "09:30" not in shown(context(now=exactly))
    late = shown(context(now=exactly + timedelta(minutes=1)))
    assert "10:00" not in late and "10:30" in late


def test_a_student_session_hides_exactly_the_overlapping_candidates():
    starts = shown(context(student_sessions=(busy(MONDAY, "10:00"),)))

    assert "09:00" in starts and "11:00" in starts
    assert not {"09:30", "10:00", "10:30"} & set(starts)


def test_a_booked_tutor_hides_the_overlapping_candidates_when_alone():
    booked = tutor(sessions=(busy(MONDAY, "10:00"),))

    starts = shown(context(tutors=(booked,)))

    assert "09:00" in starts and "11:00" in starts
    assert not {"09:30", "10:00", "10:30"} & set(starts)


def test_time_off_hides_the_tutor_for_that_day_only():
    off = TutorAvailabilityRules(
        windows=ALL_DAY.windows,
        exceptions=(DatedException("unavailable", MONDAY, MONDAY),),
    )
    ctx = context(tutors=(tutor(off),))

    assert shown(ctx, MONDAY) == []
    assert shown(ctx, TUESDAY)


def test_weekly_windows_limit_the_candidates():
    mornings = TutorAvailabilityRules(
        windows=(WeeklyWindow(1, time(9, 0), time(11, 0)),), exceptions=()
    )

    assert shown(context(tutors=(tutor(mornings),))) == ["09:00", "09:30", "10:00"]


def test_without_an_eligible_tutor_nothing_is_shown():
    assert shown(context(tutors=())) == []
    assert shown(context(tutors=(tutor(NO_WINDOWS),))) == []


def test_one_eligible_tutor_out_of_three_is_enough():
    booked = tutor(sessions=(busy(MONDAY, "08:00", 720),))
    off = tutor(
        TutorAvailabilityRules(
            windows=ALL_DAY.windows,
            exceptions=(DatedException("unavailable", MONDAY, MONDAY),),
        )
    )
    mornings = tutor(
        TutorAvailabilityRules(
            windows=(WeeklyWindow(1, time(9, 0), time(10, 0)),), exceptions=()
        )
    )

    assert shown(context(tutors=(booked, off, mornings))) == ["09:00"]
    assert family_slot_ok(
        cand(MONDAY, "09:00"), context(tutors=(booked, off, mornings))
    )
    assert not family_slot_ok(
        cand(MONDAY, "11:00"), context(tutors=(booked, off, mornings))
    )


def test_a_closure_never_drops_a_tutor_but_closes_the_date():
    # The closure is a slot reason (center_closed), applied once by the grid
    # and once by slot_conflicts; the tutor stays eligible elsewhere.
    ctx = context(closed_dates=frozenset({MONDAY}))

    assert shown(ctx, MONDAY) == []
    assert shown(ctx, TUESDAY)
    assert not family_slot_ok(cand(MONDAY, "10:00"), ctx)


def test_outside_operating_hours_is_refused_for_an_exact_time():
    ctx = context()

    assert not family_slot_ok(cand(MONDAY, "19:30"), ctx)
    assert not family_slot_ok(cand(SUNDAY, "10:00"), ctx)
    assert family_slot_ok(cand(MONDAY, "19:00"), ctx)


def test_pending_requests_do_not_hide_slots_and_are_flagged():
    pending = ((at(MONDAY, "10:00"), at(MONDAY, "11:00")),)
    ctx = context(pending=pending)

    [(_, slots)] = available_days(ctx, MONDAY, MONDAY, 60)
    flags = {
        c.starts_at.astimezone(MX).strftime("%H:%M"): requested
        for c, requested in slots
    }

    assert flags["09:00"] is False and flags["11:00"] is False
    assert flags["09:30"] is True and flags["10:00"] is True and flags["10:30"] is True
    assert shown(ctx) == shown(context())


def test_every_date_of_the_range_is_listed_in_order():
    days = available_days(context(), MONDAY, SUNDAY, 60)

    assert [day for day, _ in days] == [MONDAY + timedelta(days=n) for n in range(7)]
    assert days[-1][1] == []
