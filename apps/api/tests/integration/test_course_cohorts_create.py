"""Creating cohorts (#65 I1, D, C1-C4, A6) with real logins through the real
app.

Each test takes its own decade and freezes the clock at its
:class:`~tests.integration.series_support.Timeline` "now" (a Sunday 12:00,
Mexico City); the default horizon is 12 weeks, so a Monday/Wednesday cohort
has 24 occurrences inside it.
"""

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from app.modules.identity.permissions import ADMINISTRATOR
from app.modules.scheduling import tasks
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import get, patch
from tests.integration.cohort_support import (
    cohort_body,
    cohort_row,
    cohort_rows,
    cohort_series,
    create_cohort,
    list_cohorts,
    new_template,
    participant_count,
    put_template,
    read_cohort,
    table_counts,
)
from tests.integration.conftest import run_sql, settings_for
from tests.integration.courses_support import write_body
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
    close_center,
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
    """Two active subjects, a tutor qualified for the first and a room."""
    subject = new_subject(module_seeded_url)
    return {
        "subject": subject,
        "other": new_subject(module_seeded_url),
        "tutor": new_tutor(module_seeded_url, qualified_for=[subject]),
        "room": new_room(module_seeded_url, capacity=10),
    }


def template_for(client, cookie, refs, **values: Any) -> dict[str, Any]:
    return new_template(client, cookie, [refs["subject"], refs["other"]], **values)


def field_errors(response) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [
        (".".join(str(p) for p in e["path"][1:]), e["code"])
        for e in response.json()["errors"]
    ]


def conflicts_of(response) -> list[dict[str, Any]]:
    assert code_of(response) == CONFLICT, response.text
    found: list[dict[str, Any]] = response.json()["conflicts"]
    return found


# --- A clean create -------------------------------------------------------------------


def test_a_clean_cohort_creates_its_course_series_and_occurrences(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        payload = cohort_body(
            template=template["id"], subject=refs["subject"], starts_on=tl.monday
        )
        response = create_cohort(client, cookie, payload)
        read = read_cohort(client, cookie, response.json()["id"])

    assert code_of(response) == CREATED, response.text
    created = response.json()
    row = cohort_row(url, created["id"])
    [snapshot] = run_sql(
        url,
        "SELECT id, template_id, template_version FROM course_template_snapshots"
        " WHERE id = :id",
        {"id": row.template_snapshot_id},
    )
    assert str(snapshot.template_id) == template["id"] == str(row.template_id)
    assert snapshot.template_version == template["version"]
    [series] = cohort_series(url, created["id"])
    assert (series.kind, series.count, series.duration_minutes) == ("course", 10, 90)
    assert str(series.subject_id) == str(refs["subject"])
    assert series.tutor_user_id is None
    rows = cohort_rows(url, created["id"])
    # 10 sessions: the first 10 Mondays and Wednesdays, all in the horizon.
    assert len(rows) == 10
    assert {r.state for r in rows} == {"unassigned"}
    assert {r.kind for r in rows} == {"course"}
    assert {str(r.subject_id) for r in rows} == {str(refs["subject"])}
    assert {r.scheduled_minutes for r in rows} == {90}
    assert {str(r.series_id) for r in rows} == {str(series.id)}
    assert participant_count(url, [r.id for r in rows]) == 0

    assert created["name"] == payload["name"]
    assert created["template"] == {
        "id": template["id"],
        "name": template["name"],
        "version": template["version"],
    }
    assert created["template_snapshot_id"] == str(snapshot.id)
    assert created["subject"]["id"] == str(refs["subject"])
    assert created["max_students"] == 12
    assert created["effective_capacity"] == 12
    assert created["status"] == "upcoming"
    assert created["planned_session_count"] == 10
    assert created["session_minutes"] == 90
    assert created["first_session_on"] == tl.monday.isoformat()
    # The 10th date: Monday and Wednesday of weeks 1-5.
    assert created["last_session_on"] == tl.week(5, 3).isoformat()
    assert created["series"]["id"] == str(series.id)
    assert created["series"]["kind"] == "course"
    assert created["series"]["cohort_id"] == created["id"]
    assert created["series"]["assignment"] is None
    assert created["series"]["recurrence"] == {
        "weekdays": [1, 3],
        "interval": 1,
        "until": None,
        "count": 10,
    }
    assert "occurrences" not in created["series"]
    assert len(created["next_occurrences"]) == 10
    assert created["next_occurrences"][0]["starts_at"] == iso_z(
        tl.at(tl.monday, "16:00")
    )
    assert [m["title"] for m in created["syllabus"]["modules"]] == [
        "Introducción",
        "Práctica",
    ]
    assert created["syllabus"]["completion"]["required_module_ids"] == [
        template["modules"][0]["id"]
    ]
    assert created["warnings"] == []
    assert code_of(read) == (200, None)
    assert read.json() == {k: v for k, v in created.items() if k != "warnings"}

    # C6: the session and series details name the cohort.
    with frozen_clock(tl.now):
        session = get(client, cookie, f"/api/v1/class-sessions/{rows[0].id}").json()
        detail = get(client, cookie, f"/api/v1/class-series/{series.id}").json()
    assert session["cohort_id"] == created["id"]
    assert session["group_id"] is None
    assert detail["cohort_id"] == created["id"]

    # D3: the automatic create events, with the actor.
    for record_type, record_id in (
        ("course_cohorts", row.id),
        ("course_template_snapshots", snapshot.id),
        ("class_series", series.id),
        ("class_sessions", rows[0].id),
    ):
        [event] = events(url, action="create", record_id=record_id)
        assert event.record_type == record_type
        assert event.actor_user_id is not None
        assert event.request_id is not None


def test_a_closure_inside_the_span_adds_one_occurrence(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    close_center(url, tl.week(2, 1))

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        response = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"], subject=refs["subject"], starts_on=tl.monday
            ),
        )

    assert code_of(response) == CREATED, response.text
    created = response.json()
    [series] = cohort_series(url, created["id"])
    assert series.count == 11
    rows = cohort_rows(url, created["id"])
    assert len(rows) == 11
    skipped = [r for r in rows if r.state == "skipped_closure"]
    assert [r.starts_at for r in skipped] == [tl.at(tl.week(2, 1), "16:00")]
    assert created["last_session_on"] == tl.week(6, 1).isoformat()
    assert created["planned_session_count"] == 10


def test_a_course_longer_than_the_horizon_is_completed_by_the_nightly_job(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs, session_count=30)
        response = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"],
                subject=refs["subject"],
                starts_on=tl.monday,
                weekdays=[1],
            ),
        )

    assert code_of(response) == CREATED, response.text
    cohort_id = response.json()["id"]
    assert len(cohort_rows(url, cohort_id)) == 12  # the 12-week horizon
    assert response.json()["last_session_on"] == tl.week(30).isoformat()

    for weeks in (10, 20, 30):  # each run extends to now + 12 weeks
        later = tl.now + timedelta(weeks=weeks)
        with frozen_clock(later):
            asyncio.run(tasks.run_materialization(settings_for(url), now=later))
    rows = cohort_rows(url, cohort_id)
    assert len(rows) == 30  # stops at COUNT: no 31st
    assert {str(r.cohort_id) for r in rows} == {cohort_id}
    assert {r.kind for r in rows} == {"course"}
    assert rows[-1].starts_at == tl.at(tl.week(30), "16:00")


def test_with_a_tutor_the_rows_are_offered_in_one_series_offer(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        response = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"],
                subject=refs["subject"],
                starts_on=tl.monday,
                tutor=refs["tutor"],
                room=refs["room"],
            ),
        )

    assert code_of(response) == CREATED, response.text
    created = response.json()
    rows = cohort_rows(url, created["id"])
    assert {r.state for r in rows} == {"offered"}
    assert {str(r.tutor_user_id) for r in rows} == {str(refs["tutor"])}
    [series] = cohort_series(url, created["id"])
    [offer] = run_sql(
        url,
        "SELECT id, scope, status FROM assignment_offers WHERE class_series_id = :id",
        {"id": series.id},
    )
    assert (offer.scope, offer.status) == ("series", "pending")
    [covered] = run_sql(
        url,
        "SELECT count(*) AS n FROM assignment_offer_sessions WHERE offer_id = :id",
        {"id": offer.id},
    )
    assert covered.n == 10
    [notification] = run_sql(
        url,
        "SELECT notification_type AS type FROM notifications"
        " WHERE recipient_user_id = :id",
        {"id": refs["tutor"]},
    )
    assert notification.type == "scheduling.offer_received"
    assert created["series"]["assignment"]["status"] == "pending"
    assert created["series"]["assignment"]["offer_id"] == str(offer.id)
    assert created["series"]["assignment"]["respond_by"] is not None
    assert created["effective_capacity"] == 10  # min(12, the room's 10)


# --- Conflicts: nothing is written ---------------------------------------------------


def test_an_unqualified_tutor_is_a_soft_conflict_per_occurrence(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        payload = cohort_body(
            template=template["id"],
            subject=refs["other"],  # the tutor is qualified for "subject" only
            starts_on=tl.monday,
            tutor=refs["tutor"],
        )
        before = table_counts(url)
        refused = create_cohort(client, cookie, payload)
        after = table_counts(url)
        pairs = [
            {"occurrence_start": c["details"]["occurrence_start"], "code": c["code"]}
            for c in refused.json()["conflicts"]
        ]
        allowed = create_cohort(
            client,
            cookie,
            payload | {"override": {"pairs": pairs, "reason": "Suplencia acordada"}},
        )

    conflicts = conflicts_of(refused)
    assert {c["code"] for c in conflicts} == {"tutor_not_qualified"}
    assert len(conflicts) == 10
    assert conflicts[0]["details"]["occurrence_start"] == iso_z(
        tl.at(tl.monday, "16:00")
    )
    assert conflicts[0]["details"]["date"] == tl.monday.isoformat()
    # Nothing written, the snapshot included (it was new).
    assert after == before
    assert code_of(allowed) == CREATED, allowed.text
    [series] = cohort_series(url, allowed.json()["id"])
    [override] = run_sql(
        url,
        "SELECT count(*) AS n FROM class_session_overrides WHERE class_series_id = :id",
        {"id": series.id},
    )
    assert override.n == 10


def test_an_existing_session_in_the_room_is_a_hard_conflict(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    taken = tl.at(tl.week(2, 3), "16:00")
    existing_session(
        url, starts_at=taken, classroom_id=refs["room"], subject_id=refs["subject"]
    )

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        before = table_counts(url)
        refused = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"],
                subject=refs["subject"],
                starts_on=tl.monday,
                room=refs["room"],
            ),
        )
        after = table_counts(url)

    [conflict] = conflicts_of(refused)
    assert conflict["code"] == "room_overlap"
    assert conflict["severity"] == "hard"
    assert conflict["details"]["occurrence_start"] == iso_z(taken)
    assert after == before


# --- The 422s -------------------------------------------------------------------------


def test_an_unknown_or_inactive_template_is_refused(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        deactivated = patch(
            client,
            cookie,
            f"/api/v1/course-templates/{template['id']}",
            {"version": template["version"], "is_active": False},
        )
        assert code_of(deactivated) == (200, None), deactivated.text
        inactive = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"], subject=refs["subject"], starts_on=tl.monday
            ),
        )
        unknown = create_cohort(
            client,
            cookie,
            cohort_body(
                template=refs["room"], subject=refs["subject"], starts_on=tl.monday
            ),
        )

    assert field_errors(inactive) == [("template_id", "template_inactive")]
    assert field_errors(unknown) == [("template_id", "template_unknown")]


def test_the_subject_must_be_an_active_subject_of_the_template(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    outsider = new_subject(url)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        not_in = create_cohort(
            client,
            cookie,
            cohort_body(template=template["id"], subject=outsider, starts_on=tl.monday),
        )
        run_sql(
            url,
            "UPDATE subjects SET is_active = false WHERE id = :id",
            {"id": refs["other"]},
        )
        inactive = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"], subject=refs["other"], starts_on=tl.monday
            ),
        )

    assert field_errors(not_in) == [("subject_id", "subject_not_in_template")]
    assert field_errors(inactive) == [("subject_id", "subject_inactive")]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"max_students": 0}, ("max_students", "greater_than_equal")),
        ({"max_students": 501}, ("max_students", "less_than_equal")),
        ({"duration_minutes": 60}, ("duration_minutes", "extra_forbidden")),
        ({"student_ids": []}, ("student_ids", "extra_forbidden")),
        ({"rrule": "FREQ=WEEKLY"}, ("rrule", "extra_forbidden")),
        (
            {"recurrence": {"weekdays": [1], "count": 10}},
            ("recurrence.count", "extra_forbidden"),
        ),
        (
            {"recurrence": {"weekdays": [1], "until": "2031-12-31"}},
            ("recurrence.until", "extra_forbidden"),
        ),
        ({"name": "  "}, ("name", "name_required")),
        ({"start_time": "16:00:30"}, ("start_time", "time_format")),
    ],
)
def test_the_body_schema_is_checked(module_seeded_url, client, refs, changes, expected):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_cohort(
            client,
            cookie,
            cohort_body(
                template=refs["room"], subject=refs["subject"], starts_on=tl.monday
            )
            | changes,
        )

    assert expected in field_errors(response)


def test_every_scheduling_field_error_comes_in_one_response(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    inactive_room = new_room(url, is_active=False)

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        response = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"],
                subject=refs["subject"],
                starts_on=tl.now.date() - timedelta(days=1),
                start_time="23:00",  # + 90 min passes midnight
                weekdays=[1, 1],
                room=inactive_room,
                tutor=new_student(url),  # not a tutor
            ),
        )

    assert field_errors(response) == [
        ("recurrence.weekdays", "weekdays_duplicate"),
        ("starts_on", "starts_on_in_past"),
        ("start_time", "crosses_local_date"),
        ("tutor_user_id", "tutor_unknown"),
        ("classroom_id", "classroom_inactive"),
    ]


def test_a_deadline_without_a_tutor_is_refused(module_seeded_url, client, refs):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        template = template_for(client, cookie, refs)
        response = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"],
                subject=refs["subject"],
                starts_on=tl.monday,
                respond_by=iso_z(tl.now + timedelta(days=1)),
            ),
        )

    assert field_errors(response) == [("respond_by", "respond_by_requires_tutor")]


def test_200_sessions_and_one_closure_are_too_many_occurrences(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()
    close_center(url, tl.week(3, 3))

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs, session_count=200)
        refused = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"], subject=refs["subject"], starts_on=tl.monday
            ),
        )

    assert field_errors(refused) == [("recurrence", "too_many_occurrences")]


def test_a_name_taken_in_another_case_is_refused(module_seeded_url, client, refs):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        template = template_for(client, cookie, refs)
        payload = cohort_body(
            template=template["id"], subject=refs["subject"], starts_on=tl.monday
        )
        first = create_cohort(client, cookie, payload)
        second = create_cohort(
            client,
            cookie,
            payload | {"name": payload["name"].upper(), "start_time": "10:00"},
        )

    assert code_of(first) == CREATED, first.text
    assert code_of(second) == (409, "cohort_name_taken")


# --- Permissions ---------------------------------------------------------------------


def test_an_override_needs_schedule_override(module_seeded_url, client, refs):
    url = module_seeded_url
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        admin = sign_in(client, create_user(url, roles=(ADMINISTRATOR,)).username)
        before = table_counts(url)
        response = create_cohort(
            client,
            admin,
            cohort_body(
                template=template["id"], subject=refs["subject"], starts_on=tl.monday
            )
            | {
                "override": {
                    "pairs": [
                        {
                            "occurrence_start": iso_z(tl.at(tl.monday, "16:00")),
                            "code": "tutor_not_qualified",
                        }
                    ],
                    "reason": "Prueba",
                }
            },
        )

    assert code_of(response) == (403, "permission_denied")
    assert table_counts(url) == before


# --- D4: the template is frozen for the cohort ---------------------------------------


def test_template_edits_never_change_an_existing_cohort(
    module_seeded_url, client, refs
):
    url = module_seeded_url
    tl = timeline()

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        template = template_for(client, cookie, refs)
        body = cohort_body(
            template=template["id"], subject=refs["subject"], starts_on=tl.monday
        )
        first = create_cohort(client, cookie, body).json()
        twin = create_cohort(
            client,
            cookie,
            body | {"name": body["name"] + " B", "start_time": "14:00"},
        ).json()

        edit = write_body(template)
        edit["name"] = template["name"] + " (nuevo)"
        edit["modules"] = edit["modules"][:1]
        edit["modules"][0]["is_required"] = False
        edit["assessments"] = []
        edited = put_template(client, cookie, template["id"], edit)
        assert code_of(edited) == (200, None), edited.text
        after = read_cohort(client, cookie, first["id"]).json()
        third = create_cohort(
            client,
            cookie,
            body | {"name": body["name"] + " C", "start_time": "13:00"},
        ).json()
        # Re-read once the template has a newer snapshot: the reads must
        # join the cohort's own snapshot, not the template's latest one.
        again = read_cohort(client, cookie, first["id"]).json()
        listed = list_cohorts(
            client, cookie, template_id=template["id"], status="all"
        ).json()

    assert twin["template_snapshot_id"] == first["template_snapshot_id"]
    for read in (after, again):
        assert read["template_snapshot_id"] == first["template_snapshot_id"]
        assert read["syllabus"] == first["syllabus"]
        assert read["template"] == first["template"]
    names = {item["id"]: item["template"]["name"] for item in listed["items"]}
    assert names[first["id"]] == template["name"]
    assert names[third["id"]] == template["name"] + " (nuevo)"
    assert third["template_snapshot_id"] != first["template_snapshot_id"]
    assert third["template"]["version"] == first["template"]["version"] + 1
    assert third["template"]["name"] == template["name"] + " (nuevo)"
    assert [m["title"] for m in third["syllabus"]["modules"]] == ["Introducción"]
    assert third["syllabus"]["completion"]["required_module_ids"] == []


# --- C4: the series route stays non-course -------------------------------------------


def test_a_course_series_cannot_be_created_through_class_series(
    module_seeded_url, client, refs
):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(
            client,
            cookie,
            series_body(
                student=new_student(module_seeded_url),
                subject=refs["subject"],
                starts_on=tl.monday,
                count=4,
            )
            | {"kind": "course"},
        )

    assert code_of(response) == INVALID
