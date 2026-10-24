"""Enrolling in and withdrawing from course cohorts (#66 J1; C, D, E) with
real logins through the real app; ``clock.now`` is frozen per step and the
center is in ``America/Tijuana`` (a zone with DST, unlike the default).

The cohort is a Monday/Wednesday 16:00 course from the Monday after the
timeline's "now" (a Sunday 12:00): a 10-session template gives 10
occurrences in the 12-week horizon.
"""

import asyncio
import uuid
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.identity.permissions import ADMINISTRATOR
from app.modules.scheduling import tasks
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.cohort_support import (
    cohort_body,
    cohort_rows,
    cohort_series,
    create_cohort,
    enroll,
    enrollment_overrides,
    enrollment_rows,
    new_template,
    participants,
    read_cohort,
    series_students,
    update_cohort,
    withdraw,
    write_counts,
)
from tests.integration.conftest import run_sql, settings_for
from tests.integration.group_support import set_state
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_support import iso_z, owner_cookie, timeline
from tests.integration.test_class_session_create import (
    CREATED,
    INVALID,
    existing_session,
    new_room,
    new_student,
    new_subject,
    new_tutor,
)

TIJUANA = ZoneInfo("America/Tijuana")
CONFLICT = (409, "scheduling_conflict")
OK = (200, None)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module", autouse=True)
def tijuana(module_seeded_url: str) -> Any:
    run_sql(
        module_seeded_url,
        "UPDATE business_settings SET time_zone = 'America/Tijuana'",
    )
    yield
    run_sql(
        module_seeded_url,
        "UPDATE business_settings SET time_zone = 'America/Mexico_City'",
    )


class Tl:
    """The series timeline in Tijuana: "now" is a Sunday 12:00 local."""

    def __init__(self) -> None:
        base = timeline()
        self.monday = base.monday
        self.now = datetime.combine(base.monday - timedelta(days=1), time(12), TIJUANA)

    def week(self, n: int, weekday: int = 1) -> Any:
        return self.monday + timedelta(weeks=n - 1, days=weekday - 1)

    def at(self, day: Any, hhmm: str) -> datetime:
        return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=TIJUANA)


@pytest.fixture
def refs(module_seeded_url: str) -> dict[str, Any]:
    subject = new_subject(module_seeded_url)
    return {
        "students": [new_student(module_seeded_url) for _ in range(5)],
        "subject": subject,
        "tutor": new_tutor(module_seeded_url, qualified_for=[subject]),
        "room": new_room(module_seeded_url, capacity=10),
    }


def make_cohort(
    client: Any,
    cookie: Any,
    refs: dict[str, Any],
    starts_on: Any,
    *,
    session_count: int = 10,
    weekdays: list[int] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    changes.setdefault("room", refs["room"])
    changes.setdefault("tutor", refs["tutor"])
    template = new_template(
        client, cookie, [refs["subject"]], session_count=session_count
    )
    response = create_cohort(
        client,
        cookie,
        cohort_body(
            template=template["id"],
            subject=refs["subject"],
            starts_on=starts_on,
            weekdays=weekdays,
            **changes,
        ),
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def enrolled(client: Any, cookie: Any, cohort_id: Any, student: Any) -> dict[str, Any]:
    response = enroll(client, cookie, cohort_id, student)
    assert code_of(response) == CREATED, response.text
    item: dict[str, Any] = response.json()
    return item


def materialize(url: str, now: datetime) -> tasks.MaterializationResult:
    return asyncio.run(tasks.run_materialization(settings_for(url), now=now))


def ids(*students: uuid.UUID) -> set[str]:
    return {str(s) for s in students}


# --- C2: enrolling before the start --------------------------------------------------


def test_an_enrollment_before_the_start_reaches_every_occurrence(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = Tl()
    a, b = refs["students"][:2]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
    rows = cohort_rows(url, cohort["id"])
    assert len(rows) == 10
    set_state(url, rows[4].id, "skipped_closure")

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        first = enrolled(client, cookie, cohort["id"], a)
        second = enrolled(client, cookie, cohort["id"], b)

    assert first["student"]["id"] == str(a)
    assert first["enrolled_at"] == iso_z(tl.now)
    assert first["withdrawn_at"] is None
    assert first["overridden"] is False
    assert first["warnings"] == []
    assert second["version"] == 1
    assert [str(e.student_id) for e in enrollment_rows(url, cohort["id"])] == [
        str(a),
        str(b),
    ]
    [series] = cohort_series(url, cohort["id"])
    assert series_students(url, series.id) == ids(a, b)
    for row in rows:
        assert participants(url, row.id) == ids(a, b), row.state


# --- C2, D2: mid-course --------------------------------------------------------------


def test_mid_course_enroll_and_withdraw_touch_only_future_occurrences(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = Tl()
    a, c = refs["students"][0], refs["students"][2]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
        enrolled(client, cookie, cohort["id"], a)
    rows = cohort_rows(url, cohort["id"])
    set_state(url, rows[0].id, "completed")
    set_state(url, rows[1].id, "cancelled")
    set_state(url, rows[3].id, "conflict")
    # Row 2 starts exactly now: it has started (B3: starts_at > now).
    started_at = rows[2].starts_at
    versions = {r.id: r.version for r in cohort_rows(url, cohort["id"])}
    [series] = cohort_series(url, cohort["id"])

    with frozen_clock(started_at):
        cookie = owner_cookie(url, client)
        item = enrolled(client, cookie, cohort["id"], c)

    assert item["enrolled_at"] == iso_z(started_at)
    for row in rows[:3]:  # completed, cancelled, started
        assert participants(url, row.id) == ids(a), row.state
    for row in rows[3:]:  # the conflict row and every later one
        assert participants(url, row.id) == ids(a, c), row.state
    after = {r.id: r.version for r in cohort_rows(url, cohort["id"])}
    assert all(after[r.id] == versions[r.id] for r in rows[:3])
    assert all(after[r.id] == versions[r.id] + 1 for r in rows[3:])
    assert series_students(url, series.id) == ids(a, c)
    assert cohort_series(url, cohort["id"])[0].version == series.version + 1

    # Withdraw: a stale version, the real one, then again.
    withdraw_at = rows[5].starts_at + timedelta(minutes=1)
    with frozen_clock(withdraw_at):
        cookie = owner_cookie(url, client)
        stale = withdraw(client, cookie, cohort["id"], item["id"], version=99)
        done = withdraw(client, cookie, cohort["id"], item["id"], reason=" Mudanza ")
        again = withdraw(client, cookie, cohort["id"], item["id"], version=2)

    assert code_of(stale) == (409, "version_conflict")
    assert code_of(done) == OK, done.text
    withdrawn = done.json()
    assert withdrawn["enrolled_at"] == item["enrolled_at"]
    assert withdrawn["enrolled_by"] == item["enrolled_by"]
    assert withdrawn["withdrawn_at"] == iso_z(withdraw_at)
    assert withdrawn["withdrawal_reason"] == "Mudanza"
    assert withdrawn["withdrawn_by"] is not None
    assert withdrawn["version"] == 2
    assert code_of(again) == (409, "enrollment_not_active")
    [row_db] = [e for e in enrollment_rows(url, cohort["id"]) if e.student_id == c]
    assert row_db.withdrawn_by_user_id is not None
    for row in rows:
        kept = row.id in {r.id for r in rows[3:6]}  # joined, then started
        expected = ids(a, c) if kept else ids(a)
        assert participants(url, row.id) == expected, row
    assert series_students(url, series.id) == ids(a)

    # Re-enrolling makes a second period; an active duplicate is refused.
    with frozen_clock(withdraw_at + timedelta(minutes=1)):
        cookie = owner_cookie(url, client)
        again_enrolled = enroll(client, cookie, cohort["id"], c)
        duplicate = enroll(client, cookie, cohort["id"], c)
    assert code_of(again_enrolled) == CREATED, again_enrolled.text
    assert code_of(duplicate) == (409, "already_enrolled")
    assert duplicate.json()["details"] == {"enrollment_id": again_enrolled.json()["id"]}
    periods = [e for e in enrollment_rows(url, cohort["id"]) if e.student_id == c]
    assert [p.withdrawn_at is not None for p in periods] == [True, False]


def test_a_course_longer_than_the_horizon(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = Tl()
    a = refs["students"][0]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(
            client, cookie, refs, tl.monday, session_count=30, weekdays=[1]
        )
    created = cohort_rows(url, cohort["id"])
    assert len(created) == 12  # the horizon

    week2 = tl.at(tl.week(2), "16:30")  # the week 2 session has started
    with frozen_clock(week2):
        cookie = owner_cookie(url, client)
        item = enrolled(client, cookie, cohort["id"], a)
    assert all(str(a) not in participants(url, r.id) for r in created[:2])
    assert all(participants(url, r.id) == ids(a) for r in created[2:])

    # The nightly run past the horizon: the new rows include the student.
    week10 = tl.at(tl.week(10), "12:00")
    assert materialize(url, week10).failed == 0
    grown = cohort_rows(url, cohort["id"])
    new_rows = [r for r in grown if r.id not in {c.id for c in created}]
    assert new_rows
    assert all(participants(url, r.id) == ids(a) for r in new_rows)

    # Withdraw in week 15; the later rows (now and created later) leave them out.
    week15 = tl.at(tl.week(15), "12:00")
    with frozen_clock(week15):
        cookie = owner_cookie(url, client)
        assert code_of(withdraw(client, cookie, cohort["id"], item["id"])) == OK
    assert materialize(url, week15 + timedelta(days=7)).failed == 0
    for row in cohort_rows(url, cohort["id"]):
        expected = ids(a) if week2 < row.starts_at <= week15 else set()
        assert participants(url, row.id) == expected, row.starts_at


# --- E1: capacity --------------------------------------------------------------------


def test_the_maximum_is_soft_and_a_withdrawal_frees_a_seat(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = Tl()
    a, b, c, d = refs["students"][:4]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday, max_students=2)
        first, _ = (
            enrolled(client, cookie, cohort["id"], a),
            enrolled(client, cookie, cohort["id"], b),
        )
        refused = enroll(client, cookie, cohort["id"], c)
        admin = sign_in(client, create_user(url, roles=(ADMINISTRATOR,)).username)
        forbidden = enroll(
            client,
            admin,
            cohort["id"],
            c,
            override={"codes": ["group_capacity_exceeded"], "reason": "Ok"},
        )
        overridden = enroll(
            client,
            cookie,
            cohort["id"],
            c,
            override={"codes": ["group_capacity_exceeded"], "reason": "Hermanos"},
        )

    assert code_of(refused) == CONFLICT
    assert refused.json()["conflicts"] == [
        {
            "code": "group_capacity_exceeded",
            "severity": "soft",
            "details": {
                "cohort_id": cohort["id"],
                "max_students": 2,
                "enrolled_count": 3,
            },
        }
    ]
    assert code_of(forbidden) == (403, "permission_denied")
    assert code_of(overridden) == CREATED, overridden.text
    assert overridden.json()["overridden"] is True
    [override] = enrollment_overrides(url, overridden.json()["id"])
    assert list(override.codes) == ["group_capacity_exceeded"]
    assert override.class_session_id is None
    assert override.group_enrollment_id is None
    [event] = events(url, action="scheduling.enrollment_override")[-1:]
    assert event.record_type == "cohort_enrollments"
    assert event.details["cohort_enrollment_id"] == overridden.json()["id"]
    assert event.details["reason"] == "Hermanos"
    assert event.details["codes"] == ["group_capacity_exceeded"]

    # Two withdrawals bring the count below the maximum: a clean enroll.
    with frozen_clock(tl.now):
        assert code_of(withdraw(client, cookie, cohort["id"], first["id"])) == OK
        roster = read_cohort(client, cookie, cohort["id"]).json()["roster"]
        b_item = next(i for i in roster if i["student"]["id"] == str(b))
        assert code_of(withdraw(client, cookie, cohort["id"], b_item["id"])) == OK
        clean = enroll(client, cookie, cohort["id"], d)
    assert code_of(clean) == CREATED, clean.text
    assert clean.json()["overridden"] is False


def test_a_smaller_room_on_one_occurrence_is_hard(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = Tl()
    small = new_room(url, capacity=2)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday, max_students=5)
        for student in refs["students"][:2]:
            enrolled(client, cookie, cohort["id"], student)
    rows = cohort_rows(url, cohort["id"])
    run_sql(
        url,
        "UPDATE class_sessions SET classroom_id = :room WHERE id = :id",
        {"room": str(small), "id": str(rows[5].id)},
    )
    before = write_counts(url)

    with frozen_clock(tl.now):
        refused = enroll(client, cookie, cohort["id"], refs["students"][2])
        overridden = enroll(
            client,
            cookie,
            cohort["id"],
            refs["students"][2],
            override={"codes": ["group_capacity_exceeded"], "reason": "No"},
        )

    assert code_of(refused) == CONFLICT
    assert refused.json()["conflicts"] == [
        {
            "code": "room_capacity_exceeded",
            "severity": "hard",
            "details": {
                "class_session_id": str(rows[5].id),
                "occurrence_start": iso_z(rows[5].starts_at),
                "date": rows[5].starts_at.astimezone(TIJUANA).date().isoformat(),
                "classroom_id": str(small),
                "room_capacity": 2,
                "participant_count": 3,
            },
        }
    ]
    assert code_of(overridden) == CONFLICT
    assert write_counts(url) == before


def test_a_student_overlap_names_the_occurrence(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = Tl()
    student = refs["students"][3]
    week3 = tl.week(3, 3)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
    existing_session(
        url, starts_at=tl.at(week3, "15:30"), state="confirmed", student_id=student
    )
    rows = cohort_rows(url, cohort["id"])
    target = next(r for r in rows if r.starts_at == tl.at(week3, "16:00"))
    before = write_counts(url)

    with frozen_clock(tl.now):
        response = enroll(client, cookie, cohort["id"], student)

    [conflict] = response.json()["conflicts"]
    assert code_of(response) == CONFLICT
    assert conflict["code"] == "student_overlap"
    assert conflict["details"]["occurrence_start"] == iso_z(target.starts_at)
    assert conflict["details"]["date"] == week3.isoformat()
    assert write_counts(url) == before


# --- The other refusals ---------------------------------------------------------------


def test_ended_unknown_and_invalid(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = Tl()
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        short = make_cohort(client, cookie, refs, tl.monday, session_count=2)
        other = make_cohort(client, cookie, refs, tl.week(5), start_time="18:00")
        mine = enrolled(client, cookie, short["id"], refs["students"][0])
        unknown_cohort = enroll(client, cookie, uuid.uuid4(), refs["students"][1])
        unknown_withdraw = withdraw(client, cookie, uuid.uuid4(), mine["id"])
        inactive = enroll(
            client, cookie, other["id"], new_student(url, is_active=False)
        )
        unknown_student = enroll(client, cookie, other["id"], uuid.uuid4())
        foreign = withdraw(client, cookie, other["id"], mine["id"])
        missing = withdraw(client, cookie, other["id"], uuid.uuid4())
        too_long = withdraw(client, cookie, short["id"], mine["id"], reason="x" * 1001)

    assert code_of(unknown_cohort) == (404, "cohort_not_found")
    assert code_of(unknown_withdraw) == (404, "cohort_not_found")
    assert code_of(inactive) == INVALID
    assert [(e["path"], e["code"]) for e in inactive.json()["errors"]] == [
        (["body", "student_id"], "student_inactive")
    ]
    assert [e["code"] for e in unknown_student.json()["errors"]] == ["student_unknown"]
    assert code_of(foreign) == (404, "enrollment_not_found")
    assert code_of(missing) == (404, "enrollment_not_found")
    assert code_of(too_long) == INVALID
    assert [e["code"] for e in too_long.json()["errors"]] == ["reason_too_long"]

    # Every session has started: no enrollment, but a withdrawal is allowed
    # and changes no occurrence (D2).
    rows = cohort_rows(url, short["id"])
    assert len(rows) == 2
    after_end = rows[-1].starts_at + timedelta(minutes=1)
    with frozen_clock(after_end + timedelta(days=1)):
        cookie = owner_cookie(url, client)
        ended = enroll(client, cookie, short["id"], refs["students"][1])
        late = withdraw(client, cookie, short["id"], mine["id"])
    assert code_of(ended) == (409, "cohort_ended")
    assert code_of(late) == OK, late.text
    assert late.json()["withdrawn_at"] is not None
    assert [(r.id, r.state, r.version) for r in cohort_rows(url, short["id"])] == [
        (r.id, r.state, r.version) for r in rows
    ]
    assert all(participants(url, r.id) == ids(refs["students"][0]) for r in rows)


# --- E2: PATCH ------------------------------------------------------------------------


def test_patch_keeps_the_maximum_at_or_above_the_enrolled(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = Tl()
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday, max_students=5)
        for student in refs["students"][:3]:
            enrolled(client, cookie, cohort["id"], student)
        below = update_cohort(
            client, cookie, cohort["id"], {"version": 1, "max_students": 2}
        )
        stale_below = update_cohort(
            client, cookie, cohort["id"], {"version": 9, "max_students": 2}
        )
        equal = update_cohort(
            client, cookie, cohort["id"], {"version": 1, "max_students": 3}
        )
        raised = update_cohort(
            client, cookie, cohort["id"], {"version": 2, "max_students": 40}
        )

    assert code_of(below) == INVALID
    assert [(e["path"], e["code"]) for e in below.json()["errors"]] == [
        (["body", "max_students"], "max_students_below_enrolled")
    ]
    assert code_of(stale_below) == (409, "version_conflict")  # version first
    assert code_of(equal) == OK, equal.text
    assert code_of(raised) == OK, raised.text
    assert raised.json()["max_students"] == 40
    assert raised.json()["effective_capacity"] == 10  # the room still limits
    assert raised.json()["enrolled_count"] == 3


# --- Audit and the write-once trigger -------------------------------------------------


def test_audit_events_and_the_write_once_trigger(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = Tl()
    student = refs["students"][1]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
        item = enrolled(client, cookie, cohort["id"], student)
        assert code_of(withdraw(client, cookie, cohort["id"], item["id"])) == OK

    enrollment_id = uuid.UUID(item["id"])
    trail = events(url, record_type="cohort_enrollments", record_id=enrollment_id)
    assert [e.action for e in trail] == ["create", "update"]
    removed = [
        e
        for e in events(url, record_type="class_session_participants", action="delete")
        if str(student) in str(e.changes)
    ]
    assert len(removed) == 10
    removed_series = [
        e
        for e in events(url, record_type="class_series_participants", action="delete")
        if str(student) in str(e.changes)
    ]
    assert len(removed_series) == 1

    for statement in (
        "UPDATE cohort_enrollments SET enrolled_at = enrolled_at - interval '1 day'"
        " WHERE id = :id",
        "UPDATE cohort_enrollments SET withdrawal_reason = 'Otra' WHERE id = :id",
        "DELETE FROM cohort_enrollments WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(url, statement, {"id": str(enrollment_id)})
        assert getattr(raised.value.orig, "sqlstate", None) == "42501"


def test_the_detail_lists_the_roster(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = Tl()
    a, b, c = refs["students"][:3]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        cohort = make_cohort(client, cookie, refs, tl.monday)
        for student in (a, b):
            enrolled(client, cookie, cohort["id"], student)
    with frozen_clock(tl.now + timedelta(minutes=5)):
        cookie = owner_cookie(url, client)
        gone = enrolled(client, cookie, cohort["id"], c)
        assert code_of(withdraw(client, cookie, cohort["id"], gone["id"])) == OK
        read = read_cohort(client, cookie, cohort["id"])

    body = read.json()
    assert body["enrolled_count"] == 2
    roster = body["roster"]
    assert [i["withdrawn_at"] is None for i in roster] == [True, True, False]
    assert {i["student"]["id"] for i in roster[:2]} == ids(a, b)
    assert roster[2]["student"]["id"] == str(c)
    assert set(roster[0]) == {
        "id",
        "student",
        "enrolled_at",
        "enrolled_by",
        "withdrawn_at",
        "withdrawn_by",
        "withdrawal_reason",
        "overridden",
        "version",
    }
    assert {p["student_id"] for p in body["series"]["participants"]} == ids(a, b)
