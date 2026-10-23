"""Reading and renaming cohorts (#65 I3, E1, F1-F5) with real logins through
the real app.

The list is module-wide (other tests' cohorts are there too), so the list
tests filter by their own template or name. Each test takes its own decade
and its own "now" (a Sunday 12:00, Mexico City).
"""

from datetime import timedelta
from typing import Any

import pytest

from app.core.pagination import OffsetParams
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    TUTOR,
)
from app.modules.scheduling.schemas import CohortSessionsQuery
from app.workflows import cohorts
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements
from tests.integration.cohort_support import (
    cohort_body,
    cohort_rows,
    cohort_sessions,
    create_cohort,
    list_cohorts,
    new_template,
    read_cohort,
    update_cohort,
)
from tests.integration.conftest import run_sql
from tests.integration.group_support import set_state
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of
from tests.integration.series_support import iso_z, owner_cookie, timeline
from tests.integration.test_class_session_create import new_room, new_subject

CREATED = (201, None)
OK = (200, None)
INVALID = (422, "validation_error")


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


def make(url: str, client, tl, *, template=None, subject=None, **changes: Any):
    """A cohort of a 10 × 90 min template on a new subject, created at
    ``tl.now``; ``(cohort, template, cookie)``."""
    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        if template is None:
            subject = new_subject(url)
            template = new_template(client, cookie, [subject])
        response = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"],
                subject=subject,
                starts_on=tl.monday,
                **changes,
            ),
        )
    assert code_of(response) == CREATED, response.text
    return response.json(), template, cookie, subject


def field_errors(response) -> list[tuple[str, str]]:
    assert code_of(response) == INVALID, response.text
    return [
        (".".join(str(p) for p in e["path"][1:]), e["code"])
        for e in response.json()["errors"]
    ]


# --- F1: the detail ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("room_capacity", "expected"), [(None, 12), (8, 8), (12, 12), (20, 12)]
)
def test_the_effective_capacity_is_the_smaller_of_the_maximum_and_the_room(
    module_seeded_url, client, room_capacity, expected
):
    url = module_seeded_url
    room = None if room_capacity is None else new_room(url, capacity=room_capacity)
    cohort, *_ = make(url, client, timeline(), room=room, max_students=12)

    assert cohort["effective_capacity"] == expected
    if room is not None:
        assert cohort["series"]["classroom"]["id"] == str(room)


def test_the_detail_has_every_field(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    cohort, template, cookie, subject = make(url, client, tl)

    assert set(cohort) == {
        "id",
        "name",
        "version",
        "created_at",
        "updated_at",
        "template",
        "template_snapshot_id",
        "subject",
        "max_students",
        "effective_capacity",
        "status",
        "planned_session_count",
        "session_minutes",
        "first_session_on",
        "last_session_on",
        "series",
        "next_occurrences",
        "syllabus",
        "warnings",
    }
    assert cohort["version"] == 1
    assert cohort["subject"]["id"] == str(subject)
    assert set(cohort["subject"]["level"]) == {"id", "name"}
    assert set(cohort["next_occurrences"][0]) == {"id", "state", "starts_at", "ends_at"}
    assert set(cohort["syllabus"]) == {"modules", "assessments", "completion"}
    assert [a["title"] for a in cohort["syllabus"]["assessments"]] == ["Final"]


def test_the_status_follows_the_clock(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    cohort, *_ = make(url, client, tl)
    first = tl.at(tl.monday, "16:00")
    last_end = tl.at(tl.week(5, 3), "16:00") + timedelta(minutes=90)

    seen = []
    for at in (tl.now, first, last_end - timedelta(minutes=1), last_end):
        with frozen_clock(at):
            cookie = owner_cookie(url, client)
            seen.append(read_cohort(client, cookie, cohort["id"]).json()["status"])

    assert seen == ["upcoming", "in_progress", "in_progress", "ended"]


def test_an_unknown_cohort_is_404(module_seeded_url, client):
    tl = timeline()
    with frozen_clock(tl.now):
        cookie = owner_cookie(module_seeded_url, client)
        detail = read_cohort(client, cookie, "00000000-0000-4000-8000-000000000065")
        sessions = cohort_sessions(
            client,
            cookie,
            "00000000-0000-4000-8000-000000000065",
            **{"from": iso_z(tl.now), "to": iso_z(tl.now + timedelta(days=7))},
        )

    assert code_of(detail) == (404, "cohort_not_found")
    assert code_of(sessions) == (404, "cohort_not_found")


def test_the_detail_takes_at_most_eight_statements(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    cohort, *_ = make(url, client, tl, room=new_room(url))

    count, detail = count_statements(
        url, lambda session: cohorts.get_cohort_detail(session, cohort["id"])
    )

    assert detail.series is not None
    assert count <= 8


# --- F2: the list ------------------------------------------------------------------


def test_the_list_filters_and_pages(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    first, template, cookie, subject = make(url, client, tl, name="Álgebra Mañana")
    second, *_ = make(
        url,
        client,
        tl,
        template=template,
        subject=subject,
        name="Álgebra Tarde",
        start_time="14:00",
    )

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        by_template = list_cohorts(client, cookie, template_id=template["id"])
        by_name = list_cohorts(
            client, cookie, template_id=template["id"], q="TARDE"
        ).json()
        by_subject = list_cohorts(client, cookie, subject_id=str(subject)).json()
        paged = list_cohorts(
            client, cookie, template_id=template["id"], limit=1, offset=1
        ).json()
        past_end = list_cohorts(
            client, cookie, template_id=template["id"], offset=5
        ).json()
        ended = list_cohorts(
            client, cookie, template_id=template["id"], status="ended"
        ).json()
        everything = list_cohorts(
            client, cookie, template_id=template["id"], status="all"
        ).json()
        too_many = list_cohorts(client, cookie, limit=101)
        too_few = list_cohorts(client, cookie, limit=0)
        unknown_status = list_cohorts(client, cookie, status="cancelled")

    assert code_of(by_template) == OK
    page = by_template.json()
    assert (page["total"], page["limit"], page["offset"]) == (2, 25, 0)
    assert [item["id"] for item in page["items"]] == [first["id"], second["id"]]
    item = page["items"][0]
    assert set(item) == {
        "id",
        "name",
        "template",
        "subject",
        "max_students",
        "tutor",
        "assignment_status",
        "first_session_on",
        "last_session_on",
        "next_starts_at",
        "status",
    }
    assert item["template"] == {"id": template["id"], "name": template["name"]}
    assert item["status"] == "upcoming"
    assert item["first_session_on"] == tl.monday.isoformat()
    assert item["last_session_on"] == tl.week(5, 3).isoformat()
    assert item["next_starts_at"] == iso_z(tl.at(tl.monday, "16:00"))
    assert (item["tutor"], item["assignment_status"]) == (None, None)
    assert [i["id"] for i in by_name["items"]] == [second["id"]]
    assert {i["id"] for i in by_subject["items"]} == {first["id"], second["id"]}
    assert [i["id"] for i in paged["items"]] == [second["id"]]
    assert paged["total"] == 2
    assert (past_end["items"], past_end["total"]) == ([], 2)
    assert ended["items"] == []
    assert everything["total"] == 2
    assert code_of(too_many) == INVALID
    assert code_of(too_few) == INVALID
    assert code_of(unknown_status) == INVALID


def test_the_status_filter_defaults_to_current(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    cohort, template, *_ = make(url, client, tl)
    after = tl.at(tl.week(6), "12:00")

    with frozen_clock(after):
        cookie = owner_cookie(url, client)
        default = list_cohorts(client, cookie, template_id=template["id"]).json()
        ended = list_cohorts(
            client, cookie, template_id=template["id"], status="ended"
        ).json()
    with frozen_clock(tl.at(tl.week(2), "12:00")):
        cookie = owner_cookie(url, client)
        running = list_cohorts(
            client, cookie, template_id=template["id"], status="in_progress"
        ).json()
        current = list_cohorts(client, cookie, template_id=template["id"]).json()

    assert default["items"] == []
    assert [i["id"] for i in ended["items"]] == [cohort["id"]]
    assert ended["items"][0]["status"] == "ended"
    assert [i["id"] for i in running["items"]] == [cohort["id"]]
    assert [i["id"] for i in current["items"]] == [cohort["id"]]


@pytest.mark.parametrize("size", [1, 25])
def test_the_list_takes_at_most_five_statements(module_seeded_url, client, size):
    url = module_seeded_url
    tl = timeline()
    first, template, _, subject = make(url, client, tl)
    for _ in range(size - 1):
        make(url, client, tl, template=template, subject=subject)

    count, page = count_statements(
        url,
        lambda session: cohorts.list_cohorts(
            session,
            q=None,
            template_id=template["id"],
            subject_id=None,
            status="all",
            page=OffsetParams(limit=25, offset=0),
        ),
    )

    assert len(page.items) == size
    assert count <= 5


# --- F4: the sessions ----------------------------------------------------------------


def test_the_sessions_read_every_state_in_the_range(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    cohort, *_ = make(url, client, tl, room=new_room(url))
    rows = cohort_rows(url, cohort["id"])
    set_state(url, rows[1].id, "cancelled")
    set_state(url, rows[2].id, "skipped_closure")

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        week = cohort_sessions(
            client,
            cookie,
            cohort["id"],
            **{"from": iso_z(tl.now), "to": iso_z(tl.now + timedelta(days=11))},
        )
        empty = cohort_sessions(
            client,
            cookie,
            cohort["id"],
            **{
                "from": iso_z(tl.now + timedelta(days=300)),
                "to": iso_z(tl.now + timedelta(days=301)),
            },
        )
        backwards = cohort_sessions(
            client,
            cookie,
            cohort["id"],
            **{"from": iso_z(tl.now), "to": iso_z(tl.now - timedelta(days=1))},
        )
        too_long = cohort_sessions(
            client,
            cookie,
            cohort["id"],
            **{"from": iso_z(tl.now), "to": iso_z(tl.now + timedelta(days=46))},
        )

    assert code_of(week) == OK, week.text
    items = week.json()["items"]
    # Mon, Wed of week 1 and Mon, Wed of week 2 start within 11 days.
    assert [i["id"] for i in items] == [str(r.id) for r in rows[:4]]
    assert [i["state"] for i in items] == [
        "unassigned",
        "cancelled",
        "skipped_closure",
        "unassigned",
    ]
    assert set(items[0]) == {
        "id",
        "state",
        "starts_at",
        "ends_at",
        "original_start",
        "is_exception",
        "tutor",
        "classroom",
    }
    assert items[0]["classroom"]["id"] == cohort["series"]["classroom"]["id"]
    assert empty.json() == {"items": []}
    assert ("to", "range_invalid") in field_errors(backwards)
    assert ("to", "range_too_long") in field_errors(too_long)


def test_the_sessions_take_at_most_six_statements(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    cohort, *_ = make(url, client, tl, room=new_room(url))
    query = CohortSessionsQuery.model_validate(
        {"from": tl.now, "to": tl.now + timedelta(days=20)}
    )

    count, result = count_statements(
        url,
        lambda session: cohorts.list_cohort_sessions(session, cohort["id"], query),
    )

    assert len(result.items) == 6
    assert count <= 6


# --- F5: who reads -------------------------------------------------------------------


def test_staff_roles_read_and_a_tutor_is_refused(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    cohort, *_ = make(url, client, tl)

    outcomes = {}
    with frozen_clock(tl.now):
        for role in (ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF, TUTOR):
            cookie = sign_in(client, create_user(url, roles=(role,)).username)
            outcomes[role] = (
                code_of(read_cohort(client, cookie, cohort["id"])),
                code_of(list_cohorts(client, cookie)),
            )

    assert outcomes[ADMINISTRATOR] == (OK, OK)
    assert outcomes[ACADEMIC_COORDINATOR] == (OK, OK)
    assert outcomes[FINANCE_STAFF] == (OK, OK)
    denied = (403, "permission_denied")
    assert outcomes[TUTOR] == (denied, denied)


# --- E1: PATCH -----------------------------------------------------------------------


def audit_count(url: str, record_id: Any) -> int:
    [row] = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events WHERE record_id = :id",
        {"id": record_id},
    )
    return int(row.n)


def test_patch_renames_and_raises_the_maximum(module_seeded_url, client):
    url = module_seeded_url
    tl = timeline()
    small = new_room(url, capacity=8)
    cohort, template, _, subject = make(url, client, tl, room=small, max_students=6)
    other, *_ = make(
        url, client, tl, template=template, subject=subject, start_time="14:00"
    )

    with frozen_clock(tl.now):
        cookie = owner_cookie(url, client)
        renamed = update_cohort(
            client,
            cookie,
            cohort["id"],
            {"version": 1, "name": " Cohorte renombrada ", "max_students": 20},
        )
        before_noop = audit_count(url, cohort["id"])
        noop = update_cohort(
            client,
            cookie,
            cohort["id"],
            {"version": 2, "name": "Cohorte renombrada", "max_students": 20},
        )
        after_noop = audit_count(url, cohort["id"])
        stale = update_cohort(client, cookie, cohort["id"], {"version": 1, "name": "X"})
        taken = update_cohort(
            client,
            cookie,
            other["id"],
            {"version": 1, "name": "COHORTE RENOMBRADA"},
        )
        nothing = update_cohort(client, cookie, cohort["id"], {"version": 2})
        forbidden_field = update_cohort(
            client,
            cookie,
            cohort["id"],
            {"version": 2, "template_id": template["id"]},
        )
        coordinator = sign_in(
            client, create_user(url, roles=(ACADEMIC_COORDINATOR,)).username
        )
        refused = update_cohort(
            client, coordinator, cohort["id"], {"version": 2, "name": "Y"}
        )

    assert code_of(renamed) == OK, renamed.text
    body = renamed.json()
    assert (body["name"], body["max_students"], body["version"]) == (
        "Cohorte renombrada",
        20,
        2,
    )
    # Above the room's capacity: the room still limits the sessions (D7).
    assert body["effective_capacity"] == 8
    assert body["template_snapshot_id"] == cohort["template_snapshot_id"]
    assert code_of(noop) == OK
    assert noop.json()["version"] == 2
    assert after_noop == before_noop
    assert code_of(stale) == (409, "version_conflict")
    assert code_of(taken) == (409, "cohort_name_taken")
    assert "no_changes" in {code for _, code in field_errors(nothing)}
    assert ("template_id", "extra_forbidden") in field_errors(forbidden_field)
    assert code_of(refused) == (403, "permission_denied")
