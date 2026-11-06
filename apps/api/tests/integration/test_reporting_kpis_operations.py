"""K4, K5 and K6/K7 against their worked examples (#126 B3-B5, B6 DST;
``_docs/kpis.md``). Each example has a database of its own (a KPI is
center-wide), dates in ``America/Mexico_City`` and a frozen clock."""

from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reporting import kpis
from app.modules.scheduling import service as scheduling
from tests.conftest import frozen_clock
from tests.factories.families import persist_student
from tests.integration import kpi_support as ks
from tests.integration.lifecycle_support import in_uow
from tests.integration.report_schedule_support import set_zone


def rooms(url, start, end, now):
    with frozen_clock(now):
        return in_uow(url, lambda s: kpis.room_utilization(s, start=start, end=end))


def retention(url, start, end, now):
    with frozen_clock(now):
        return in_uow(url, lambda s: kpis.student_retention(s, start=start, end=end))


def tutors(url, start, end, now):
    with frozen_clock(now):
        return in_uow(url, lambda s: kpis.tutor_performance(s, start=start, end=end))


def row_of(report, room_id):
    [row] = [r for r in report.rooms if r.room.id == room_id]
    return row.available_minutes, row.used_minutes, row.utilization_percent


# --- B3 K4 ------------------------------------------------------------------------


def test_b3_room_utilization_worked_example(seeded_url):
    url = seeded_url
    made = ks.build_b3(url)
    report = rooms(url, *ks.B3_RANGE, ks.B3_NOW)
    assert [r.room.id for r in report.rooms] == [made.a, made.b]
    assert row_of(report, made.a) == (3180, 210, Decimal("6.60"))
    assert row_of(report, made.b) == (300, 120, Decimal("40.00"))
    assert (
        report.total.available_minutes,
        report.total.used_minutes,
        report.total.utilization_percent,
    ) == (3480, 330, Decimal("9.48"))
    body = report.model_dump(mode="json", by_alias=True)
    assert body["total"]["utilization_percent"] == "9.48"
    assert (body["from"], body["to"]) == ("2031-03-03", "2031-03-09")


def test_b3_no_available_minutes_is_null_and_bookings_outside_windows_count(
    seeded_url,
):
    url = seeded_url
    ks.set_hours(url, ks.B3_HOURS)

    async def _make(session: AsyncSession):
        sunday_only = await ks.room(session, "Aula Domingo", [(7, "09:00", "12:00")])
        narrow = await ks.room(session, "Aula Estrecha", [(1, "09:00", "10:00")])
        # Booked over its window by an override: 120 used of 60 available.
        await ks.class_session(
            session,
            starts_at=ks.local("2031-03-03", 9),
            minutes=120,
            classroom_id=narrow,
        )
        await ks.class_session(
            session, starts_at=ks.local("2031-03-04", 9), classroom_id=sunday_only
        )
        return sunday_only, narrow

    with frozen_clock(ks.B3_NOW):
        sunday_only, narrow = in_uow(url, _make)
    report = rooms(url, *ks.B3_RANGE, ks.B3_NOW)
    assert row_of(report, sunday_only) == (0, 60, None)
    assert row_of(report, narrow) == (60, 120, Decimal("200.00"))


def test_b3_a_session_that_has_not_started_is_not_counted_yet(seeded_url):
    url = seeded_url
    made = ks.build_b3(url)
    # On Thursday at 10:00 the 10:00 session has started; at 09:59 not yet.
    thursday = date(2031, 3, 6)
    before = rooms(url, ks.B3_RANGE[0], thursday, ks.local(thursday, 9, 59))
    after = rooms(url, ks.B3_RANGE[0], thursday, ks.local(thursday, 10))
    assert row_of(before, made.a)[1] == 120
    assert row_of(after, made.a)[1] == 210


# --- B4 K5 ------------------------------------------------------------------------


def test_b4_student_retention_worked_example(seeded_url):
    url = seeded_url
    ks.build_b4(url)
    report = retention(url, *ks.B4_RANGE, ks.B4_NOW)
    assert (report.previous_from, report.previous_to) == (
        date(2031, 1, 29),
        date(2031, 2, 28),
    )
    assert (
        report.active_previous,
        report.active_current,
        report.retained,
        report.new,
        report.lost,
        report.retention_percent,
    ) == (5, 5, 2, 3, 3, Decimal("40.00"))
    assert [(m.month, m.active_students) for m in report.months] == [("2031-03", 5)]
    body = report.model_dump(mode="json", by_alias=True)
    assert body["retention_percent"] == "40.00"
    # No previous activity: null.
    early = retention(url, date(2031, 1, 20), date(2031, 1, 31), ks.B4_NOW)
    assert (early.active_previous, early.active_current) == (0, 1)
    assert early.retention_percent is None
    # Two months: March's five and nobody yet in April's first day... April
    # is after S7's local date, which stays in March.
    spring = retention(url, date(2031, 3, 1), date(2031, 4, 1), ks.B4_NOW)
    assert [(m.month, m.active_students) for m in spring.months] == [
        ("2031-03", 5),
        ("2031-04", 0),
    ]


# --- B5 K6/K7 ---------------------------------------------------------------------


def figures(row):
    return (
        row.sessions,
        row.completed,
        row.pending_closeout,
        row.cancelled,
        row.scheduled_minutes,
        row.students,
        (
            row.attendance.present,
            row.attendance.late,
            row.attendance.absent,
            row.attendance.not_recorded,
        ),
        row.attendance_percent,
    )


def test_b5_tutor_performance_worked_example_and_the_84_identity(seeded_url):
    url = seeded_url
    made = ks.build_b5(url)
    report = tutors(url, *ks.B5_RANGE, ks.B5_NOW)
    assert [(r.tutor.id, r.tutor.display_name) for r in report.tutors] == [
        (made.t1, "Ana Tutora"),
        (made.t2, "Beto Tutor"),
    ]
    t1, t2 = report.tutors
    assert figures(t1) == (3, 2, 1, 1, 210, 4, (2, 1, 1, 1), Decimal("75.00"))
    assert figures(t2) == (1, 1, 0, 0, 60, 1, (1, 0, 0, 0), Decimal("100.00"))
    assert figures(report.totals) == (
        4,
        3,
        1,
        1,
        270,
        5,
        (3, 1, 1, 1),
        Decimal("80.00"),
    )

    async def _summaries(session: AsyncSession):
        return [
            await scheduling.student_attendance_summary(
                session,
                student,
                *ks.B5_RANGE,
                subject_id=None,
                time_zone="America/Mexico_City",
                now=ks.B5_NOW,
            )
            for student in made.students
        ]

    summaries = in_uow(url, _summaries)
    attendance = report.totals.attendance
    assert (
        attendance.present,
        attendance.late,
        attendance.absent,
        attendance.not_recorded,
    ) == (
        sum(x.present for x in summaries),
        sum(x.late for x in summaries),
        sum(x.absent for x in summaries),
        sum(x.not_recorded for x in summaries),
    )
    assert report.totals.sessions == sum(row.sessions for row in report.tutors)


def test_no_data_gives_zeros_and_null_percentages(seeded_url):
    url = seeded_url
    now = ks.B5_NOW
    empty = tutors(url, *ks.B5_RANGE, now)
    assert empty.tutors == []
    assert figures(empty.totals) == (0, 0, 0, 0, 0, 0, (0, 0, 0, 0), None)
    kept = retention(url, *ks.B5_RANGE, now)
    assert (kept.active_current, kept.retention_percent) == (0, None)
    assert [(m.month, m.active_students) for m in kept.months] == [("2031-03", 0)]
    used = rooms(url, *ks.B5_RANGE, now)
    assert used.total.used_minutes == 0


# --- B6 DST -----------------------------------------------------------------------


def test_b6_a_dst_change_day_buckets_by_local_date(seeded_url):
    """New York springs forward on Sun 2031-03-09 (a 23-hour day) and falls
    back on Sun 2031-11-02 (25 hours). Sessions at 00:30 and 23:30 local on
    those days belong to them, and not to the neighbours."""
    url = seeded_url
    set_zone(url, "America/New_York")
    zone = ZoneInfo("America/New_York")
    now = ks.local("2031-11-10", 12, zone=zone)

    async def _make(session: AsyncSession):
        student = (await persist_student(session, contacts=0)).id
        for day, hour, minute in (
            ("2031-03-08", 23, 30),
            ("2031-03-09", 0, 30),
            ("2031-03-09", 23, 30),
            ("2031-03-10", 0, 30),
            ("2031-11-02", 0, 30),
            ("2031-11-02", 23, 30),
            ("2031-11-03", 0, 30),
        ):
            await ks.class_session(
                session,
                starts_at=ks.local(day, hour, minute, zone=zone),
                students=[student],
            )

    with frozen_clock(now):
        in_uow(url, _make)
    spring = tutors(url, date(2031, 3, 9), date(2031, 3, 9), now)
    assert spring.totals.sessions == 2
    autumn = tutors(url, date(2031, 11, 2), date(2031, 11, 2), now)
    assert autumn.totals.sessions == 2
    # Ranges that start and end on the change days; partial months clipped.
    across = retention(url, date(2031, 3, 9), date(2031, 11, 2), now)
    assert [m.month for m in across.months][0] == "2031-03"
    assert (across.months[0].from_, across.months[-1].to) == (
        date(2031, 3, 9),
        date(2031, 11, 2),
    )
    assert across.active_current == 1
    assert tutors(url, date(2031, 3, 9), date(2031, 11, 2), now).totals.sessions == 5
