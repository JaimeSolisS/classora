"""Enrolling in and withdrawing from tutoring groups (#56 J2, E1-E4, B3, C2)
with real logins through the real app; ``clock.now`` is frozen per step.

The group is a Monday/Wednesday 16:00 series from the Monday after the
timeline's "now" (a Sunday 12:00, Mexico City): 24 occurrences in the
12-week horizon.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.identity.permissions import ADMINISTRATOR
from app.modules.scheduling import tasks
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.conftest import run_sql, settings_for
from tests.integration.group_support import (
    create_group,
    enroll,
    enrollment_overrides,
    enrollments,
    group_body,
    group_rows,
    participants,
    read_group,
    series_of,
    series_students,
    set_state,
    update_group,
    withdraw,
)
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

CONFLICT = (409, "scheduling_conflict")
OK = (200, None)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def refs(module_seeded_url: str) -> dict[str, Any]:
    subject = new_subject(module_seeded_url)
    return {
        "students": [new_student(module_seeded_url) for _ in range(5)],
        "subject": subject,
        "tutor": new_tutor(module_seeded_url, qualified_for=[subject]),
        "room": new_room(module_seeded_url, capacity=10),
    }


def make_group(
    client: Any,
    cookie: Any,
    refs: dict[str, Any],
    starts_on: Any,
    *,
    students: int = 2,
    **changes: Any,
) -> dict[str, Any]:
    changes.setdefault("room", refs["room"])
    changes.setdefault("tutor", refs["tutor"])
    response = create_group(
        client,
        cookie,
        group_body(
            subject=refs["subject"],
            starts_on=starts_on,
            students=refs["students"][:students],
            **changes,
        ),
    )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def materialize(url: str, now: datetime) -> tasks.MaterializationResult:
    return asyncio.run(tasks.run_materialization(settings_for(url), now=now))


def ids(*students: uuid.UUID) -> set[str]:
    return {str(s) for s in students}


# --- Mid-term enroll and withdraw (B3, C2) --------------------------------------------


def test_mid_term_enroll_and_withdraw_touch_only_future_occurrences(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    a, b, c = refs["students"][:3]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = make_group(client, cookie, refs, tl.monday)
    rows = group_rows(url, group["id"])
    set_state(url, rows[0].id, "completed")
    set_state(url, rows[1].id, "cancelled")
    set_state(url, rows[3].id, "conflict")
    set_state(url, rows[4].id, "skipped_closure")
    started_at = rows[2].starts_at + timedelta(minutes=30)
    versions = {r.id: r.version for r in group_rows(url, group["id"])}
    [series] = series_of(url, group["id"])

    with frozen_clock(started_at):
        cookie = owner_cookie(url, client)
        enrolled = enroll(client, cookie, group["id"], c)

    assert code_of(enrolled) == CREATED, enrolled.text
    item = enrolled.json()
    assert item["student"]["id"] == str(c)
    assert item["withdrawn_at"] is None
    assert item["overridden"] is False
    assert item["warnings"] == []
    assert item["enrolled_at"] == iso_z(started_at)
    for row in rows[:3]:
        assert participants(url, row.id) == ids(a, b)
    for row in rows[3:]:
        assert participants(url, row.id) == ids(a, b, c)
    after = {r.id: r.version for r in group_rows(url, group["id"])}
    assert all(after[r.id] == versions[r.id] for r in rows[:3])
    assert all(after[r.id] == versions[r.id] + 1 for r in rows[3:])
    assert series_students(url, series.id) == ids(a, b, c)
    assert series_of(url, group["id"])[0].version == series.version + 1

    # C2: the next materialization includes the new student.
    first_job = started_at + timedelta(days=7)
    result = materialize(url, first_job)
    assert result.failed == 0
    new_rows = [r for r in group_rows(url, group["id"]) if r.id not in versions]
    assert new_rows
    assert all(participants(url, r.id) == ids(a, b, c) for r in new_rows)

    # Withdraw: a stale version first, then the real one.
    withdraw_at = rows[6].starts_at + timedelta(minutes=1)
    with frozen_clock(withdraw_at):
        cookie = owner_cookie(url, client)
        stale = withdraw(client, cookie, group["id"], item["id"], version=99)
        done = withdraw(client, cookie, group["id"], item["id"], reason=" Mudanza ")
        again = withdraw(client, cookie, group["id"], item["id"], version=2)

    assert code_of(stale) == (409, "version_conflict")
    assert code_of(done) == OK, done.text
    withdrawn = done.json()
    assert withdrawn["enrolled_at"] == item["enrolled_at"]
    assert withdrawn["withdrawn_at"] == iso_z(withdraw_at)
    assert withdrawn["withdrawal_reason"] == "Mudanza"
    assert withdrawn["withdrawn_by"] is not None
    assert withdrawn["enrolled_by"] == item["enrolled_by"]
    assert withdrawn["version"] == 2
    assert code_of(again) == (409, "enrollment_not_active")
    current = group_rows(url, group["id"])
    for row in current:
        expected = (
            ids(a, b, c)
            if row.starts_at <= withdraw_at and row.id in {r.id for r in rows[3:]}
            else ids(a, b)
        )
        assert participants(url, row.id) == expected, row
    assert series_students(url, series.id) == ids(a, b)

    # The next materialization leaves the withdrawn student out.
    known = {r.id for r in current}
    materialize(url, first_job + timedelta(days=7))
    later_rows = [r for r in group_rows(url, group["id"]) if r.id not in known]
    assert later_rows
    assert all(participants(url, r.id) == ids(a, b) for r in later_rows)

    # The period is kept (write-once); re-enrolling makes a second one.
    with frozen_clock(withdraw_at + timedelta(minutes=1)):
        cookie = owner_cookie(url, client)
        again_enrolled = enroll(client, cookie, group["id"], c)
        duplicate = enroll(client, cookie, group["id"], c)
    assert code_of(again_enrolled) == CREATED, again_enrolled.text
    assert code_of(duplicate) == (409, "already_enrolled")
    assert duplicate.json()["details"] == {"enrollment_id": again_enrolled.json()["id"]}
    periods = [e for e in enrollments(url, group["id"]) if str(e.student_id) == str(c)]
    assert len(periods) == 2
    assert periods[0].withdrawn_at is not None
    assert periods[1].withdrawn_at is None


# --- Capacity -------------------------------------------------------------------------


def test_the_group_maximum_is_soft_and_a_withdrawal_frees_a_seat(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    c, d = refs["students"][2:4]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = make_group(client, cookie, refs, tl.monday, max_students=2)
        refused = enroll(client, cookie, group["id"], c)
        admin = sign_in(client, create_user(url, roles=(ADMINISTRATOR,)).username)
        forbidden = enroll(
            client,
            admin,
            group["id"],
            c,
            override={"codes": ["group_capacity_exceeded"], "reason": "Ok"},
        )
        overridden = enroll(
            client,
            cookie,
            group["id"],
            c,
            override={"codes": ["group_capacity_exceeded"], "reason": "Hermanos"},
        )

    assert code_of(refused) == CONFLICT
    assert refused.json()["conflicts"] == [
        {
            "code": "group_capacity_exceeded",
            "severity": "soft",
            "details": {
                "group_id": group["id"],
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
    [event] = events(url, action="scheduling.enrollment_override")[-1:]
    assert event.details["group_enrollment_id"] == overridden.json()["id"]
    assert event.details["reason"] == "Hermanos"

    # Two withdrawals free a seat below the maximum: the next enroll is clean.
    roster = {e.student_id: e for e in enrollments(url, group["id"])}
    with frozen_clock(tl.now):
        for student in refs["students"][:2]:
            assert (
                code_of(withdraw(client, cookie, group["id"], roster[student].id)) == OK
            )
        clean = enroll(client, cookie, group["id"], d)
    assert code_of(clean) == CREATED, clean.text
    assert clean.json()["overridden"] is False


def test_a_room_over_capacity_on_one_occurrence_is_hard(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    small = new_room(url, capacity=3)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = make_group(client, cookie, refs, tl.monday, students=3, max_students=5)
    rows = group_rows(url, group["id"])
    run_sql(
        url,
        "UPDATE class_sessions SET classroom_id = :room WHERE id = :id",
        {"room": str(small), "id": str(rows[5].id)},
    )

    with frozen_clock(tl.now):
        refused = enroll(client, cookie, group["id"], refs["students"][3])
        overridden = enroll(
            client,
            cookie,
            group["id"],
            refs["students"][3],
            override={"codes": ["group_capacity_exceeded"], "reason": "No"},
        )

    [conflict] = refused.json()["conflicts"]
    assert code_of(refused) == CONFLICT
    assert conflict == {
        "code": "room_capacity_exceeded",
        "severity": "hard",
        "details": {
            "class_session_id": str(rows[5].id),
            "occurrence_start": iso_z(rows[5].starts_at),
            "date": rows[5].starts_at.astimezone(tl.now.tzinfo).date().isoformat(),
            "classroom_id": str(small),
            "room_capacity": 3,
            "participant_count": 4,
        },
    }
    assert code_of(overridden) == CONFLICT
    assert len(enrollments(url, group["id"])) == 3


def test_a_student_overlap_names_the_occurrence(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    student = refs["students"][3]
    week3 = tl.week(3, 3)
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = make_group(client, cookie, refs, tl.monday)
    existing_session(
        url, starts_at=tl.at(week3, "15:30"), state="confirmed", student_id=student
    )
    rows = group_rows(url, group["id"])
    target = next(r for r in rows if r.starts_at == tl.at(week3, "16:00"))

    with frozen_clock(tl.now):
        response = enroll(client, cookie, group["id"], student)

    [conflict] = response.json()["conflicts"]
    assert code_of(response) == CONFLICT
    assert conflict["code"] == "student_overlap"
    assert conflict["details"]["occurrence_start"] == iso_z(target.starts_at)
    assert conflict["details"]["date"] == week3.isoformat()
    assert all(str(student) not in participants(url, r.id) for r in rows)


# --- Refusals -------------------------------------------------------------------------


def test_group_ended_not_found_and_invalid_student(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        short = make_group(client, cookie, refs, tl.monday, count=2)
        other = make_group(
            client, cookie, refs, tl.week(5), students=0, start_time="18:00"
        )
        [mine] = enrollments(url, short["id"])[:1]
        unknown_group = enroll(client, cookie, uuid.uuid4(), refs["students"][2])
        inactive = enroll(
            client, cookie, other["id"], new_student(url, is_active=False)
        )
        unknown_student = enroll(client, cookie, other["id"], uuid.uuid4())
        foreign = withdraw(client, cookie, other["id"], mine.id)
        missing = withdraw(client, cookie, other["id"], uuid.uuid4())

    with frozen_clock(tl.now + timedelta(days=10)):
        cookie = owner_cookie(url, client)
        ended = enroll(client, cookie, short["id"], refs["students"][2])

    assert code_of(unknown_group) == (404, "group_not_found")
    assert code_of(inactive) == INVALID
    assert [e["code"] for e in inactive.json()["errors"]] == ["student_inactive"]
    assert [e["code"] for e in unknown_student.json()["errors"]] == ["student_unknown"]
    assert code_of(foreign) == (404, "enrollment_not_found")
    assert code_of(missing) == (404, "enrollment_not_found")
    assert code_of(ended) == (409, "group_ended")


def test_patch_renames_and_changes_the_maximum(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = make_group(client, cookie, refs, tl.monday, students=3)
        taken = make_group(
            client, cookie, refs, tl.week(6), students=0, start_time="18:00"
        )
        renamed = update_group(
            client, cookie, group["id"], {"version": 1, "name": " Nuevo nombre "}
        )
        raised = update_group(
            client, cookie, group["id"], {"version": 2, "max_students": 20}
        )
        below = update_group(
            client, cookie, group["id"], {"version": 3, "max_students": 2}
        )
        stale = update_group(client, cookie, group["id"], {"version": 1, "name": "X"})
        name_taken = update_group(
            client, cookie, group["id"], {"version": 3, "name": taken["name"].upper()}
        )
        nothing = update_group(client, cookie, group["id"], {"version": 3})
        unknown = update_group(
            client, cookie, uuid.uuid4(), {"version": 1, "name": "Y"}
        )

    assert code_of(renamed) == OK, renamed.text
    assert renamed.json()["name"] == "Nuevo nombre"
    assert code_of(raised) == OK
    assert raised.json()["max_students"] == 20
    assert raised.json()["effective_capacity"] == 10  # the room still limits
    assert code_of(below) == INVALID
    assert [(e["path"], e["code"]) for e in below.json()["errors"]] == [
        (["body", "max_students"], "max_students_below_enrolled")
    ]
    assert code_of(stale) == (409, "version_conflict")
    assert code_of(name_taken) == (409, "group_name_taken")
    assert code_of(nothing) == INVALID
    assert code_of(unknown) == (404, "group_not_found")


# --- Audit and the write-once trigger -------------------------------------------------


def test_audit_events_and_the_write_once_trigger(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    student = refs["students"][2]
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        group = make_group(client, cookie, refs, tl.monday)
        item = enroll(client, cookie, group["id"], student).json()
        assert code_of(withdraw(client, cookie, group["id"], item["id"])) == OK

    enrollment_id = uuid.UUID(item["id"])
    assert [e.action for e in events(url, record_id=enrollment_id)] == [
        "create",
        "update",
    ]
    assert events(url, record_type="class_groups", record_id=uuid.UUID(group["id"]))
    removed = [
        e
        for e in events(url, record_type="class_session_participants", action="delete")
        if str(student) in str(e.changes)
    ]
    assert len(removed) == 24

    for statement in (
        "UPDATE group_enrollments SET enrolled_at = enrolled_at - interval '1 day'"
        " WHERE id = :id",
        "UPDATE group_enrollments SET withdrawal_reason = 'Otra' WHERE id = :id",
        "DELETE FROM group_enrollments WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as raised:
            run_sql(url, statement, {"id": str(enrollment_id)})
        assert getattr(raised.value.orig, "sqlstate", None) == "42501"


def test_a_group_read_after_enrollment_shows_the_roster(
    module_seeded_url, client, refs
):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        group = make_group(client, cookie, refs, tl.monday, students=1)
        enroll(client, cookie, group["id"], refs["students"][1])
        read = read_group(client, cookie, group["id"])

    assert read.json()["enrolled_count"] == 2
    assert {i["student"]["id"] for i in read.json()["roster"]} == ids(
        *refs["students"][:2]
    )
