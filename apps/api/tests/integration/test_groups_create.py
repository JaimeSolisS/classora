"""Creating tutoring groups (#56 J1, D, C1, C3, C4) with real logins through
the real app.

Each test takes its own decade and freezes the clock at its
:class:`~tests.integration.series_support.Timeline` "now" (a Sunday 12:00,
Mexico City); the default horizon is 12 weeks, so a Monday/Wednesday group
gets 24 occurrences.
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.modules.identity.permissions import ADMINISTRATOR
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import get
from tests.integration.conftest import run_sql
from tests.integration.group_support import (
    create_group,
    enrollment_overrides,
    enrollments,
    group_body,
    group_rows,
    participants,
    read_group,
    series_of,
    series_students,
)
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.series_support import (
    create_series,
    iso_z,
    owner_cookie,
    series_body,
    timeline,
)
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


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def refs(module_seeded_url: str) -> dict[str, Any]:
    """Seven active students, a subject, a tutor qualified for it and a room
    for 10."""
    subject = new_subject(module_seeded_url)
    return {
        "students": [new_student(module_seeded_url) for _ in range(7)],
        "subject": subject,
        "tutor": new_tutor(module_seeded_url, qualified_for=[subject]),
        "room": new_room(module_seeded_url, capacity=10),
    }


def body(refs: dict[str, Any], starts_on, *, students: int = 3, **changes: Any):
    changes.setdefault("room", refs["room"])
    return group_body(
        subject=refs["subject"],
        starts_on=starts_on,
        students=refs["students"][:students],
        **changes,
    )


def conflicts_of(response) -> list[dict[str, Any]]:
    assert code_of(response) == CONFLICT, response.text
    found: list[dict[str, Any]] = response.json()["conflicts"]
    return found


def field_errors(response) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [
        (".".join(str(p) for p in e["path"][1:]), e["code"])
        for e in response.json()["errors"]
    ]


def group_count(url: str) -> tuple[int, int, int]:
    [row] = run_sql(
        url,
        "SELECT (SELECT count(*) FROM class_groups) AS a,"
        " (SELECT count(*) FROM group_enrollments) AS b,"
        " (SELECT count(*) FROM class_sessions) AS c",
    )
    return (int(row.a), int(row.b), int(row.c))


# --- A clean group --------------------------------------------------------------------


@pytest.mark.parametrize("with_tutor", [False, True])
def test_a_clean_group_creates_its_series_enrollments_and_occurrences(
    module_seeded_url, client, refs, with_tutor
):
    url = module_seeded_url
    tl = timeline()
    payload = body(refs, tl.monday, tutor=refs["tutor"] if with_tutor else None)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = create_group(client, cookie, payload)
        read = read_group(client, cookie, response.json()["id"])

    assert code_of(response) == CREATED, response.text
    created = response.json()
    students = {str(s) for s in refs["students"][:3]}
    [series] = series_of(url, created["id"])
    assert series.kind == "group"
    assert str(series.group_id) == created["id"]
    assert series_students(url, series.id) == students
    assert {str(e.student_id) for e in enrollments(url, created["id"])} == students
    rows = group_rows(url, created["id"])
    assert len(rows) == 24
    assert {r.state for r in rows} == {"offered" if with_tutor else "unassigned"}
    assert {r.kind for r in rows} == {"group"}
    assert {str(r.series_id) for r in rows} == {str(series.id)}
    assert all(participants(url, r.id) == students for r in rows)

    assert created["name"] == payload["name"]
    assert created["max_students"] == 6
    assert created["enrolled_count"] == 3
    assert created["effective_capacity"] == 6
    assert created["warnings"] == []
    assert created["current_series"]["id"] == str(series.id)
    assert created["current_series"]["recurrence"]["weekdays"] == [1, 3]
    assert len(created["next_occurrences"]) == 10
    names = [item["student"]["display_name"].casefold() for item in created["roster"]]
    assert names == sorted(names)
    assert {item["student"]["id"] for item in created["roster"]} == students
    assert {item["overridden"] for item in created["roster"]} == {False}
    assert code_of(read) == (200, None)
    assert read.json() == {k: v for k, v in created.items() if k != "warnings"}

    # C4: the session and series details name the group.
    with frozen_clock(tl.now):
        session = get(client, cookie, f"/api/v1/class-sessions/{rows[0].id}").json()
        detail = get(client, cookie, f"/api/v1/class-series/{series.id}").json()
    assert session["group_id"] == created["id"]
    assert session["kind"] == "group"
    assert detail["group_id"] == created["id"]
    assert detail["kind"] == "group"


def test_an_empty_group_opens_with_no_participant(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = create_group(client, cookie, body(refs, tl.monday, students=0))

    assert code_of(response) == CREATED, response.text
    created = response.json()
    rows = group_rows(url, created["id"])
    assert len(rows) == 24
    assert all(participants(url, r.id) == set() for r in rows)
    assert created["enrolled_count"] == 0
    assert created["roster"] == []
    assert enrollments(url, created["id"]) == []


def test_the_room_limits_the_effective_capacity(module_seeded_url, client, refs):
    tl = timeline()
    small = new_room(module_seeded_url, capacity=4)

    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_group(client, cookie, body(refs, tl.monday, room=small))

    assert code_of(response) == CREATED, response.text
    assert response.json()["effective_capacity"] == 4
    assert response.json()["current_series"]["classroom"]["id"] == str(small)


# --- Capacity -------------------------------------------------------------------------


def test_more_students_than_the_maximum_is_one_soft_conflict(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    payload = body(refs, tl.monday, students=7)
    before = group_count(url)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        refused = create_group(client, cookie, payload)
        admin = sign_in(client, create_user(url, roles=(ADMINISTRATOR,)).username)
        forbidden = create_group(
            client,
            admin,
            payload
            | {"override": {"codes": ["group_capacity_exceeded"], "reason": "Ok"}},
        )
        after_refusals = group_count(url)
        allowed = create_group(
            client,
            cookie,
            payload
            | {
                "override": {
                    "codes": ["group_capacity_exceeded"],
                    "reason": " Demanda alta ",
                }
            },
        )

    assert conflicts_of(refused) == [
        {
            "code": "group_capacity_exceeded",
            "severity": "soft",
            "details": {
                "group_id": refused.json()["conflicts"][0]["details"]["group_id"],
                "max_students": 6,
                "enrolled_count": 7,
            },
        }
    ]
    assert code_of(forbidden) == (403, "permission_denied")
    assert after_refusals == before
    assert code_of(allowed) == CREATED, allowed.text
    created = allowed.json()
    assert created["enrolled_count"] == 7
    rows = enrollments(url, created["id"])
    overridden = [r for r in rows if enrollment_overrides(url, r.id)]
    assert [str(r.student_id) for r in overridden] == [str(refs["students"][6])]
    [override] = enrollment_overrides(url, overridden[0].id)
    assert override.class_session_id is None
    assert override.class_series_id is None
    assert list(override.codes) == ["group_capacity_exceeded"]
    assert override.reason == "Demanda alta"
    [event] = events(
        url, action="scheduling.enrollment_override", record_id=overridden[0].id
    )
    assert event.details["codes"] == ["group_capacity_exceeded"]
    assert event.details["reason"] == "Demanda alta"
    assert event.details["group_enrollment_id"] == str(overridden[0].id)
    flags = {item["student"]["id"]: item["overridden"] for item in created["roster"]}
    assert flags[str(refs["students"][6])] is True
    assert sum(flags.values()) == 1


def test_more_students_than_the_room_is_hard_on_every_occurrence(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    room = new_room(url, capacity=6)
    payload = body(refs, tl.monday, students=7, room=room, max_students=8)
    before = group_count(url)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        refused = create_group(client, cookie, payload)
        overridden = create_group(
            client,
            cookie,
            payload
            | {"override": {"codes": ["group_capacity_exceeded"], "reason": "No"}},
        )

    found = conflicts_of(refused)
    assert len(found) == 24
    assert {c["code"] for c in found} == {"room_capacity_exceeded"}
    assert {c["severity"] for c in found} == {"hard"}
    assert {c["details"]["participants"] for c in found} == {7}
    assert {c["details"]["room_capacity"] for c in found} == {6}
    assert len({c["details"]["occurrence_start"] for c in found}) == 24
    assert conflicts_of(overridden) == found
    assert group_count(url) == before


def test_a_student_overlap_names_the_occurrence_and_creates_nothing(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    week2 = tl.week(2)
    existing_session(
        url,
        starts_at=tl.at(week2, "16:30"),
        state="confirmed",
        student_id=refs["students"][1],
    )
    before = group_count(url)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        response = create_group(client, cookie, body(refs, tl.monday))

    [conflict] = conflicts_of(response)
    assert conflict["code"] == "student_overlap"
    assert conflict["details"]["student_id"] == str(refs["students"][1])
    assert conflict["details"]["occurrence_start"] == iso_z(tl.at(week2, "16:00"))
    assert conflict["details"]["date"] == week2.isoformat()
    assert group_count(url) == before


# --- 422s and the name ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"name": "  "}, [("name", "name_required")]),
        ({"name": "x" * 121}, [("name", "name_too_long")]),
        ({"max_students": 0}, [("max_students", "greater_than_equal")]),
        ({"max_students": 501}, [("max_students", "less_than_equal")]),
        ({"rrule": "FREQ=WEEKLY"}, [("rrule", "extra_forbidden")]),
        ({"start_time": "16:00:30"}, [("start_time", "time_format")]),
    ],
)
def test_schema_errors(module_seeded_url, client, refs, changes, expected):
    tl = timeline()

    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_group(client, cookie, body(refs, tl.monday) | changes)

    assert field_errors(response) == expected


def test_duplicate_students_and_the_reference_errors(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    student = refs["students"][0]
    inactive = new_student(url, is_active=False)
    payload = body(refs, tl.monday)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        duplicate = create_group(
            client, cookie, payload | {"student_ids": [str(student), str(student)]}
        )
        references = create_group(
            client,
            cookie,
            payload
            | {
                "student_ids": [str(student), str(inactive), str(uuid.uuid4())],
                "subject_id": str(uuid.uuid4()),
            },
        )
        rule = create_group(
            client,
            cookie,
            payload | {"recurrence": {"weekdays": [], "interval": 1}},
        )
        past = create_group(
            client, cookie, body(refs, tl.now.date() - timedelta(days=1))
        )

    assert field_errors(duplicate) == [("student_ids", "student_ids_duplicate")]
    assert field_errors(references) == [
        ("student_ids.1", "student_inactive"),
        ("student_ids.2", "student_unknown"),
        ("subject_id", "subject_unknown"),
    ]
    assert field_errors(rule) == [("recurrence.weekdays", "weekdays_empty")]
    assert field_errors(past) == [("starts_on", "starts_on_in_past")]


def test_a_name_is_unique_in_any_case(module_seeded_url, client, refs):
    tl = timeline()
    name = f"Álgebra {uuid.uuid4().hex[:6]}"

    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        first = create_group(client, cookie, body(refs, tl.monday, name=name))
        second = create_group(
            client, cookie, body(refs, tl.monday, students=0, name=name.upper())
        )

    assert code_of(first) == CREATED, first.text
    assert code_of(second) == (409, "group_name_taken")


def test_class_series_stay_individual(module_seeded_url, client, refs):
    tl = timeline()
    payload = series_body(
        student=refs["students"][0], subject=refs["subject"], starts_on=tl.monday
    ) | {"kind": "group"}

    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload)

    assert field_errors(response) == [("kind", "literal_error")]
