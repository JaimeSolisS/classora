"""Creating and reading recurring individual series (#53 J1, H1, I1) with
real logins through the real app.

Each test takes its own decade (closures are global in the module database)
and freezes the clock at its :class:`~tests.integration.series_support.Timeline`
"now" (a Sunday 12:00, Mexico City); the default horizon is 12 weeks.
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.modules.catalog.models import Classroom
from app.modules.families.models import Student
from app.modules.identity.permissions import OWNER
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.recurrence import (
    RecurrenceFields,
    build_rrule,
    expand,
    horizon_end,
)
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch
from tests.integration.invitation_support import reauthenticate, sign_in
from tests.integration.lifecycle_support import code_of, events, in_uow
from tests.integration.series_support import (
    counts,
    create_series,
    iso_z,
    owner_cookie,
    read_series,
    series_body,
    series_rows,
    timeline,
)
from tests.integration.test_class_session_create import (
    CREATED,
    INVALID,
    change,
    close_center,
    create,
    existing_session,
    new_room,
    new_student,
    new_subject,
    new_tutor,
)
from tests.integration.test_class_session_create import body as session_body

CONFLICT = (409, "scheduling_conflict")


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture
def refs(module_seeded_url: str) -> dict[str, uuid.UUID]:
    """An active student, a subject, a tutor qualified for it (all week) and
    a room without windows."""
    subject = new_subject(module_seeded_url)
    return {
        "student": new_student(module_seeded_url),
        "subject": subject,
        "tutor": new_tutor(module_seeded_url, qualified_for=[subject]),
        "room": new_room(module_seeded_url),
    }


def payload(refs: dict[str, uuid.UUID], starts_on, **changes: Any) -> dict[str, Any]:
    return series_body(
        student=refs["student"],
        subject=refs["subject"],
        tutor=refs["tutor"],
        room=refs["room"],
        starts_on=starts_on,
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


# --- A clean series ------------------------------------------------------------------


@pytest.mark.parametrize("with_tutor", [True, False])
def test_a_clean_monday_wednesday_series_fills_the_horizon(
    module_seeded_url, client, refs, with_tutor
):
    tl = timeline()
    body = payload(refs, tl.monday)
    if not with_tutor:
        body["tutor_user_id"] = None

    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, body)

    assert code_of(response) == CREATED, response.text
    created = response.json()
    rows = series_rows(module_seeded_url, created["id"])
    expected = expand(
        build_rrule(RecurrenceFields((1, 3), 1, None, None)),
        starts_on=tl.monday,
        start_time=tl.at(tl.monday, "16:00").time(),
        duration_minutes=60,
        zone="America/Mexico_City",
        window_start=tl.now,
        window_end=horizon_end(tl.now, 12),
    )
    state = "offered" if with_tutor else "unassigned"
    assert len(rows) == len(expected) == 24
    assert [r.starts_at for r in rows] == [o.starts_at for o in expected]
    assert all(r.original_start == r.starts_at for r in rows)
    assert {r.state for r in rows} == {state}
    assert {r.scheduled_minutes for r in rows} == {60}
    assert {r.is_exception for r in rows} == {False}
    assert {tuple(r.conflict_codes) for r in rows} == {()}
    assert created["rrule"] == "FREQ=WEEKLY;INTERVAL=1;WKST=MO;BYDAY=MO,WE"
    assert created["recurrence"] == {
        "weekdays": [1, 3],
        "interval": 1,
        "until": None,
        "count": None,
    }
    assert created["last_occurrence_on"] is None
    assert created["start_time"] == "16:00"
    assert created["time_zone"] == "America/Mexico_City"
    assert created["warnings"] == []
    assert [o["state"] for o in created["occurrences"]] == [state] * 24
    assert created["occurrences"][0]["starts_at"] == iso_z(tl.at(tl.monday, "16:00"))
    assert (created["tutor"] is not None) is with_tutor


def test_count_3_creates_3_rows_and_stores_the_last_date(
    module_seeded_url, client, refs
):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(refs, tl.monday, count=3))

    assert code_of(response) == CREATED, response.text
    created = response.json()
    assert created["last_occurrence_on"] == tl.week(2).isoformat()
    assert created["rrule"].endswith(";COUNT=3")
    assert len(series_rows(module_seeded_url, created["id"])) == 3


def test_count_3_with_a_closure_on_the_second_date_counts_the_skipped_one(
    module_seeded_url, client, refs
):
    tl = timeline()
    close_center(module_seeded_url, tl.week(1, 3))
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(refs, tl.monday, count=3))

    assert code_of(response) == CREATED, response.text
    rows = series_rows(module_seeded_url, response.json()["id"])
    assert [r.state for r in rows] == ["offered", "skipped_closure", "offered"]
    assert response.json()["last_occurrence_on"] == tl.week(2).isoformat()


def test_until_creates_nothing_after_it(module_seeded_url, client, refs):
    tl = timeline()
    until = tl.week(3, 3)
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(refs, tl.monday, until=until))

    assert code_of(response) == CREATED, response.text
    rows = series_rows(module_seeded_url, response.json()["id"])
    assert len(rows) == 6
    assert response.json()["last_occurrence_on"] == until.isoformat()
    assert response.json()["rrule"].endswith(f";UNTIL={until:%Y%m%d}T235959")


def test_a_series_starting_after_the_horizon_has_no_rows(
    module_seeded_url, client, refs
):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(refs, tl.week(14)))

    assert code_of(response) == CREATED, response.text
    assert response.json()["occurrences"] == []
    assert series_rows(module_seeded_url, response.json()["id"]) == []


def test_a_closure_date_is_skipped_and_holds_nothing(module_seeded_url, client, refs):
    tl = timeline()
    closed = tl.week(2, 3)
    close_center(module_seeded_url, closed)
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(refs, tl.monday, count=6))
        # The skipped occurrence occupies nothing: a one-off session for the
        # same tutor and room at that time is accepted (center_closed is
        # overridden: one-off sessions still check it).
        one_off = create(
            client,
            cookie,
            session_body(
                student_id=new_student(module_seeded_url),
                subject_id=refs["subject"],
                tutor_user_id=refs["tutor"],
                classroom_id=refs["room"],
                starts_at=tl.at(closed, "16:00"),
                ends_at=tl.at(closed, "17:00"),
            ),
        )

    assert code_of(response) == CREATED, response.text
    rows = series_rows(module_seeded_url, response.json()["id"])
    skipped = [r for r in rows if r.state == "skipped_closure"]
    assert [r.starts_at for r in skipped] == [tl.at(closed, "16:00")]
    assert skipped[0].tutor_user_id == refs["tutor"]
    assert skipped[0].classroom_id == refs["room"]
    assert list(skipped[0].conflict_codes) == []
    # The one-off only meets the closure itself (no overlap with the skipped row).
    assert [c["code"] for c in conflicts_of(one_off)] == ["center_closed"]


# --- Per-occurrence 409s -------------------------------------------------------------


def test_a_tutor_overlap_in_week_3_refuses_the_whole_series(
    module_seeded_url, client, refs
):
    tl = timeline()
    blocker = existing_session(
        module_seeded_url,
        state="confirmed",
        tutor_user_id=refs["tutor"],
        student_id=new_student(module_seeded_url),
        starts_at=tl.at(tl.week(3), "16:30"),
    )
    before = counts(module_seeded_url)

    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(refs, tl.monday))

    occurrence = tl.at(tl.week(3), "16:00")
    assert conflicts_of(response) == [
        {
            "code": "tutor_overlap",
            "severity": "hard",
            "details": {
                "class_session_ids": [str(blocker)],
                "occurrence_start": iso_z(occurrence),
                "date": tl.week(3).isoformat(),
            },
        }
    ]
    assert counts(module_seeded_url) == before


def test_a_student_overlap_in_week_2(module_seeded_url, client, refs):
    tl = timeline()
    existing_session(
        module_seeded_url,
        state="unassigned",
        tutor=False,
        student_id=refs["student"],
        starts_at=tl.at(tl.week(2, 3), "15:30"),
    )
    before = counts(module_seeded_url)

    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(refs, tl.monday))

    [conflict] = conflicts_of(response)
    assert conflict["code"] == "student_overlap"
    assert conflict["details"]["date"] == tl.week(2, 3).isoformat()
    assert conflict["details"]["student_id"] == str(refs["student"])
    assert counts(module_seeded_url) == before


def test_every_occurrence_lists_its_conflicts_in_order(module_seeded_url, client, refs):
    tl = timeline()
    unqualified = new_tutor(module_seeded_url)

    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(
            client,
            cookie,
            payload(refs, tl.monday, count=4) | {"tutor_user_id": str(unqualified)},
        )

    found = conflicts_of(response)
    assert [c["code"] for c in found] == ["tutor_not_qualified"] * 4
    assert [c["details"]["date"] for c in found] == [
        tl.week(1).isoformat(),
        tl.week(1, 3).isoformat(),
        tl.week(2).isoformat(),
        tl.week(2, 3).isoformat(),
    ]
    assert [c["details"]["occurrence_start"] for c in found] == sorted(
        c["details"]["occurrence_start"] for c in found
    )


def test_the_notice_window_breaks_only_the_first_occurrence(
    module_seeded_url, client, refs
):
    tl = timeline()
    # Sunday 20:00: the Monday 16:00 occurrence is 20 hours away (notice 24).
    with frozen_clock(tl.now + timedelta(hours=8)):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(refs, tl.monday))

    assert conflicts_of(response) == [
        {
            "code": "notice_window",
            "severity": "soft",
            "details": {
                "booking_notice_hours": 24,
                "occurrence_start": iso_z(tl.at(tl.monday, "16:00")),
                "date": tl.monday.isoformat(),
            },
        }
    ]


# --- 422s ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("recurrence", "error"),
    [
        ({"weekdays": []}, ("recurrence.weekdays", "weekdays_empty")),
        ({"weekdays": [0]}, ("recurrence.weekdays", "weekday_out_of_range")),
        ({"weekdays": [8]}, ("recurrence.weekdays", "weekday_out_of_range")),
        ({"weekdays": [1, 1]}, ("recurrence.weekdays", "weekdays_duplicate")),
        ({"interval": 0}, ("recurrence.interval", "interval_out_of_range")),
        ({"interval": 5}, ("recurrence.interval", "interval_out_of_range")),
        ({"count": 0}, ("recurrence.count", "count_out_of_range")),
        ({"count": 201}, ("recurrence.count", "count_out_of_range")),
        ({"count": 3, "until": "WEEK3"}, ("recurrence.until", "until_and_count")),
        ({"until": "BEFORE"}, ("recurrence.until", "until_before_start")),
        (
            {"weekdays": [2], "until": "START"},
            ("recurrence", "no_occurrences"),
        ),
    ],
)
def test_every_recurrence_reason_is_422(
    module_seeded_url, client, refs, recurrence, error
):
    tl = timeline()
    dates = {
        "WEEK3": tl.week(3).isoformat(),
        "BEFORE": (tl.monday - timedelta(days=1)).isoformat(),
        "START": tl.monday.isoformat(),
    }
    body = payload(refs, tl.monday)
    body["recurrence"] |= {
        key: dates.get(value, value) if isinstance(value, str) else value
        for key, value in recurrence.items()
    }
    before = counts(module_seeded_url)

    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, body)

    assert field_errors(response) == [error]
    assert counts(module_seeded_url) == before


def test_other_422s(module_seeded_url, client, refs):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)

        def errors(**changes: Any) -> list[tuple[str, str]]:
            return field_errors(
                create_series(client, cookie, payload(refs, tl.monday) | changes)
            )

        past = errors(starts_on=(tl.now.date() - timedelta(days=1)).isoformat())
        crossing = errors(start_time="23:30", duration_minutes=60)
        midnight = create_series(
            client,
            cookie,
            payload(refs, tl.monday, count=1)
            | {"start_time": "23:00", "duration_minutes": 60, "tutor_user_id": None},
        )
        override = errors(override={"codes": ["notice_window"], "reason": "x"})
        rrule = errors(rrule="FREQ=WEEKLY;BYDAY=MO")
        group = errors(kind="group")
        seconds = errors(start_time="16:00:30")
        short = errors(duration_minutes=10)

    assert past == [("starts_on", "starts_on_in_past")]
    assert crossing == [("start_time", "crosses_local_date")]
    # Ending exactly at midnight is allowed (only operating hours refuse it).
    assert [c["code"] for c in conflicts_of(midnight)] == ["outside_operating_hours"]
    assert override == [("override", "extra_forbidden")]
    assert rrule == [("rrule", "extra_forbidden")]
    assert group == [("kind", "literal_error")]
    assert seconds == [("start_time", "time_format")]
    assert short == [("duration_minutes", "greater_than_equal")]


def test_every_reference_error_comes_in_one_response(module_seeded_url, client):
    tl = timeline()
    unknown = {
        "student": uuid.uuid4(),
        "subject": uuid.uuid4(),
        "tutor": uuid.uuid4(),
        "room": uuid.uuid4(),
    }
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(unknown, tl.monday))

    assert field_errors(response) == [
        ("student_id", "student_unknown"),
        ("subject_id", "subject_unknown"),
        ("tutor_user_id", "tutor_unknown"),
        ("classroom_id", "classroom_unknown"),
    ]


def test_inactive_references_are_422(module_seeded_url, client):
    tl = timeline()
    subject = new_subject(module_seeded_url, is_active=False)
    refs = {
        "student": new_student(module_seeded_url, is_active=False),
        "subject": subject,
        "tutor": new_tutor(
            module_seeded_url, qualified_for=[subject], state="suspended"
        ),
        "room": new_room(module_seeded_url, is_active=False),
    }
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        response = create_series(client, cookie, payload(refs, tl.monday))

    assert field_errors(response) == [
        ("student_id", "student_inactive"),
        ("subject_id", "subject_inactive"),
        ("tutor_user_id", "tutor_inactive"),
        ("classroom_id", "classroom_inactive"),
    ]


# --- Audit, read, session detail -----------------------------------------------------


def test_the_create_writes_only_the_automatic_create_events(
    module_seeded_url, client, refs
):
    tl = timeline()
    owner = create_user(module_seeded_url, roles=(OWNER,))
    with frozen_clock(tl.now):
        cookie = sign_in(client, owner.username)
        response = create_series(client, cookie, payload(refs, tl.monday, count=2))
    series_id = uuid.UUID(response.json()["id"])
    session_ids = {r.id for r in series_rows(module_seeded_url, series_id)}

    [series_event] = events(module_seeded_url, record_id=series_id)
    session_events = [
        e
        for e in events(module_seeded_url, record_type="class_sessions")
        if e.record_id in session_ids
    ]
    participant_events = [
        e
        for e in events(module_seeded_url, record_type="class_session_participants")
        if e.changes
        and e.changes.get("class_session_id", {}).get("new")
        in {str(i) for i in session_ids}
    ]
    series_participants = [
        e
        for e in events(module_seeded_url, record_type="class_series_participants")
        if e.changes
        and e.changes.get("class_series_id", {}).get("new") == str(series_id)
    ]

    assert (series_event.action, series_event.record_type) == ("create", "class_series")
    assert series_event.actor_user_id == owner.id
    assert series_event.request_id
    all_events = [*session_events, *participant_events, *series_participants]
    assert len(session_events) == 2
    assert len(participant_events) == 2
    assert len(series_participants) == 1
    assert {e.action for e in all_events} == {"create"}
    assert {e.actor_user_id for e in all_events} == {owner.id}
    assert {e.request_id for e in all_events} == {series_event.request_id}


def test_the_read_matches_the_create_in_at_most_eight_statements(
    module_seeded_url, client, refs
):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        created = create_series(
            client, cookie, payload(refs, tl.monday, count=3)
        ).json()
        read = read_series(client, cookie, created["id"])
        count, detail = count_statements(
            module_seeded_url,
            lambda session: scheduling.get_class_series(
                session, uuid.UUID(created["id"])
            ),
        )

    assert code_of(read) == (200, None)
    expected = {k: v for k, v in created.items() if k != "warnings"}
    assert read.json() == expected
    assert count <= 9  # #54 A2: +1 for the lineage
    assert detail.model_dump(mode="json") == expected


def test_the_read_lists_only_occurrences_from_now(module_seeded_url, client, refs):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        created = create_series(
            client, cookie, payload(refs, tl.monday, count=4)
        ).json()
    with frozen_clock(tl.now + timedelta(days=3)):  # Wednesday 12:00
        cookie = owner_cookie(module_seeded_url, client)
        later = read_series(client, cookie, created["id"]).json()

    assert [o["starts_at"] for o in later["occurrences"]] == [
        o["starts_at"] for o in created["occurrences"][1:]
    ]


def test_an_unknown_series_is_404(module_seeded_url, client):
    with frozen_clock(timeline().now):
        cookie = owner_cookie(module_seeded_url, client)
        response = read_series(client, cookie, uuid.uuid4())

    assert code_of(response) == (404, "not_found")


def test_the_read_shows_inactive_references_as_such(module_seeded_url, client, refs):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        created = create_series(
            client, cookie, payload(refs, tl.monday, count=1)
        ).json()

        async def _deactivate(session) -> None:
            await change(session, Classroom, refs["room"], is_active=False)
            await change(session, Student, refs["student"], is_active=False)

        in_uow(module_seeded_url, _deactivate)
        detail = read_series(client, cookie, created["id"]).json()

    assert detail["classroom"]["is_active"] is False
    assert detail["participants"][0]["is_active"] is False


def test_a_session_detail_shows_its_series(module_seeded_url, client, refs):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        created = create_series(
            client, cookie, payload(refs, tl.monday, count=1)
        ).json()
        [occurrence] = created["occurrences"]
        detail = get(client, cookie, f"/api/v1/class-sessions/{occurrence['id']}")
        one_off = create(
            client,
            cookie,
            session_body(
                student_id=new_student(module_seeded_url),
                subject_id=refs["subject"],
                starts_at=tl.at(tl.week(5), "10:00"),
                ends_at=tl.at(tl.week(5), "11:00"),
            ),
        )

    assert code_of(detail) == (200, None)
    assert {
        k: detail.json()[k]
        for k in ("series_id", "original_start", "is_exception", "conflict_codes")
    } == {
        "series_id": created["id"],
        "original_start": occurrence["original_start"],
        "is_exception": False,
        "conflict_codes": [],
    }
    assert code_of(one_off) == CREATED, one_off.text
    assert {
        k: one_off.json()[k]
        for k in ("series_id", "original_start", "is_exception", "conflict_codes")
    } == {
        "series_id": None,
        "original_start": None,
        "is_exception": False,
        "conflict_codes": [],
    }


# --- The time-zone guard (own database) ----------------------------------------------


def test_a_series_without_rows_blocks_a_zone_change(seeded_url):
    tl = timeline()
    owner = create_user(seeded_url, roles=(OWNER,))
    subject = new_subject(seeded_url)
    with auth_client(seeded_url) as client, frozen_clock(tl.now):
        cookie = sign_in(client, owner.username)
        response = create_series(
            client,
            cookie,
            series_body(
                student=new_student(seeded_url),
                subject=subject,
                starts_on=tl.week(20),
            ),
        )
        assert code_of(response) == CREATED, response.text
        assert response.json()["occurrences"] == []
        reauthenticate(client, cookie)
        blocked = patch(
            client,
            cookie,
            "/api/v1/business-settings",
            {"version": 1, "time_zone": "America/Tijuana"},
        )

    assert code_of(blocked) == (409, "tz_change_requires_migration")
