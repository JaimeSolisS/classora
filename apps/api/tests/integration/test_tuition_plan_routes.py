"""The tuition plan routes (#93 D, H4) through the real app: the shapes, the
create codes, ending and extending, pauses and resumes, delete, the key
trigger, the list filters and its statement bound, and the after-commit
defer of the daily job."""

import uuid
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.exc import DBAPIError

from app.core import jobs
from app.core.pagination import OffsetParams
from app.modules.identity.permissions import FINANCE_STAFF
from app.workflows import tuition_plans
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import tuition_support as ts
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, patch, post
from tests.integration.conftest import run_sql
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events

NOW = datetime(2031, 3, 10, 12, 0, tzinfo=ts.MX)
TODAY = date(2031, 3, 10)
INVALID = (422, "validation_error")
ITEM_KEYS = {
    "id",
    "version",
    "student",
    "family",
    "service_price",
    "group",
    "group_enrollment_id",
    "frequency",
    "starts_on",
    "ends_on",
    "due_offset_days",
    "status",
    "current_amount",
}
DETAIL_KEYS = ITEM_KEYS | {
    "pauses",
    "posted_through",
    "recent_periods",
    "upcoming_periods",
}


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(client: TestClient, module_seeded_url: str) -> SetCookie:
    return sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )


@pytest.fixture(autouse=True)
def _reset(module_seeded_url: str) -> None:
    ts.reset(module_seeded_url)
    run_sql(
        module_seeded_url,
        "DELETE FROM procrastinate_jobs WHERE task_name = :t",
        {"t": jobs.TUITION_CHARGES_TASK},
    )


def body(w: ts.World, **values: Any) -> dict[str, Any]:
    return {
        "student_id": str(w.student),
        "service_price_id": str(w.item),
        "starts_on": str(TODAY),
        **values,
    }


def create(client: TestClient, cookie: SetCookie, w: ts.World, **values: Any) -> Any:
    return post(client, cookie, ts.PLANS, body(w, **values))


def created(
    client: TestClient, cookie: SetCookie, w: ts.World, **values: Any
) -> dict[str, Any]:
    response = create(client, cookie, w, **values)
    assert response.status_code == 201, response.text
    detail: dict[str, Any] = response.json()
    return detail


def path(plan: Any, suffix: str = "") -> str:
    return f"{ts.PLANS}/{plan}{suffix}"


def detail_of(client: TestClient, cookie: SetCookie, plan: Any) -> dict[str, Any]:
    response = get(client, cookie, path(plan))
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def field_code(response: Response) -> tuple[int, str, list[str], str]:
    problem = response.json()
    [error] = problem["errors"]
    return response.status_code, problem["code"], error["path"], error["code"]


def pause_at(
    client: TestClient, cookie: SetCookie, plan: Any, version: int, **values: Any
) -> Response:
    return post(client, cookie, path(plan, "/pauses"), {"version": version, **values})


def resume_at(
    client: TestClient, cookie: SetCookie, plan: Any, version: int, day: str
) -> Response:
    return post(
        client, cookie, path(plan, "/resume"), {"version": version, "resume_on": day}
    )


def queued(url: str) -> int:
    rows = run_sql(
        url,
        "SELECT count(*) AS n FROM procrastinate_jobs WHERE task_name = :t",
        {"t": jobs.TUITION_CHARGES_TASK},
    )
    return int(rows[0].n)


def test_create_a_standalone_plan(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ts.world(url, amount="1500.00")
    ts.set_floor(url, date(2031, 1, 1))

    response = create(
        client,
        staff,
        w,
        starts_on="2031-03-01",
        ends_on="2031-08-31",
        due_offset_days=4,
    )

    assert response.status_code == 201, response.text
    detail = response.json()
    assert set(detail) == DETAIL_KEYS
    assert detail["version"] == 1
    assert detail["student"]["id"] == str(w.student)
    assert detail["family"]["id"] == str(w.family)
    assert detail["service_price"]["id"] == str(w.item)
    assert detail["service_price"]["pricing_model"] == "per_period"
    assert detail["service_price"]["period_frequency"] == "monthly"
    assert detail["frequency"] == "monthly"
    assert (detail["starts_on"], detail["ends_on"]) == ("2031-03-01", "2031-08-31")
    assert detail["due_offset_days"] == 4
    assert detail["status"] == "active"
    assert detail["current_amount"] == "1500.00"
    assert detail["group"] is None and detail["group_enrollment_id"] is None
    assert detail["pauses"] == [] and detail["recent_periods"] == []
    assert detail["posted_through"] is None
    upcoming = detail["upcoming_periods"]
    assert [p["period_start"] for p in upcoming] == [
        "2031-03-01",
        "2031-04-01",
        "2031-05-01",
        "2031-06-01",
        "2031-07-01",
        "2031-08-01",
    ]
    assert upcoming[0]["state"] == "due" and upcoming[0]["reason"] is None
    assert upcoming[1]["state"] == "not_due"
    assert len(events(url, record_type="tuition_plans", record_id=detail["id"])) == 1
    assert queued(url) == 1  # starts today or earlier: the job is deferred


def test_a_future_plan_is_scheduled_and_queues_nothing(
    client, staff, module_seeded_url
):
    w = ts.world(module_seeded_url, frequency="weekly")

    detail = created(client, staff, w, starts_on="2031-04-02")

    assert detail["status"] == "scheduled"
    assert detail["frequency"] == "weekly"
    assert queued(module_seeded_url) == 0


def test_create_refusals(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ts.world(url)
    session_item = ts.world(url).item
    run_sql(
        url,
        "UPDATE service_prices SET pricing_model = 'per_session',"
        " period_frequency = NULL WHERE id = :i",
        {"i": session_item},
    )
    inactive = ts.world(url)
    run_sql(
        url,
        "UPDATE students SET is_active = false WHERE id = :s",
        {"s": inactive.student},
    )
    orphan = ts.world(url, family=False)

    def code(**values: Any) -> tuple[int, str, list[str], str]:
        return field_code(create(client, staff, w, **values))

    assert code(student_id=str(uuid.uuid4())) == (
        *INVALID,
        ["body", "student_id"],
        "student_unknown",
    )
    assert field_code(create(client, staff, inactive)) == (
        *INVALID,
        ["body", "student_id"],
        "student_inactive",
    )
    price = (422, "tuition_plan_price_invalid", ["body", "service_price_id"])
    assert code(service_price_id=str(uuid.uuid4()))[:3] == price
    assert code(service_price_id=str(session_item))[:3] == price
    starts = (422, "tuition_plan_starts_on_invalid", ["body", "starts_on"])
    assert code(starts_on="2031-02-06")[:3] == starts  # 32 days ago
    assert create(client, staff, w, starts_on="2031-02-07").status_code == 201
    assert code(starts_on="2032-03-11")[:3] == starts  # 367 days ahead
    ends = (422, "tuition_plan_ends_on_invalid", ["body", "ends_on"])
    assert code(starts_on="2031-05-15", ends_on="2031-06-30")[:3] == ends
    assert code(due_offset_days=61)[:2] == INVALID
    assert code(starts_on="1999-12-31")[3] == "date_out_of_range"
    response = create(client, staff, orphan)
    assert code_of(response) == (409, "student_has_no_family")
    # The 31-days-ago plan above is open-ended: any later plan overlaps it.
    response = create(client, staff, w, starts_on="2031-09-01")
    assert code_of(response) == (409, "tuition_plan_overlap")


def test_ending_at_the_current_period_stops_later_charges(
    client, staff, module_seeded_url
):
    url = module_seeded_url
    w = ts.world(url)
    ts.set_floor(url, date(2031, 1, 1))
    plan = created(client, staff, w, starts_on="2031-02-10")
    ts.run(url, ts.local(TODAY))
    before = ts.charges(url, plan["id"])
    assert [c.service_date for c in before] == [date(2031, 2, 10), date(2031, 3, 10)]

    too_early = patch(
        client, staff, path(plan["id"]), {"version": 1, "ends_on": "2031-03-09"}
    )
    assert field_code(too_early)[1] == "tuition_plan_ends_on_invalid"
    mid = patch(
        client, staff, path(plan["id"]), {"version": 1, "ends_on": "2031-04-01"}
    )
    assert field_code(mid)[1] == "tuition_plan_ends_on_invalid"
    response = patch(
        client, staff, path(plan["id"]), {"version": 1, "ends_on": "2031-04-09"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["ends_on"] == "2031-04-09"
    assert response.json()["posted_through"] == "2031-04-09"
    assert ts.run(url, ts.local(date(2031, 6, 20))).posted == 0
    assert ts.charges(url, plan["id"]) == before


def test_an_ended_plan_is_not_extended(client, staff, module_seeded_url):
    w = ts.world(module_seeded_url, frequency="weekly")
    plan = created(client, staff, w, starts_on="2031-02-24", ends_on="2031-03-09")
    assert plan["status"] == "ended"

    for ends_on in (None, "2031-03-16"):
        response = patch(
            client, staff, path(plan["id"]), {"version": 1, "ends_on": ends_on}
        )
        assert code_of(response) == (409, "tuition_plan_ended")
    shorter = patch(
        client, staff, path(plan["id"]), {"version": 1, "ends_on": "2031-03-02"}
    )
    assert shorter.status_code == 200, shorter.text


def test_extending_into_another_plan_overlaps(client, staff, module_seeded_url):
    w = ts.world(module_seeded_url)
    first = created(client, staff, w, starts_on="2031-03-10", ends_on="2031-05-09")
    created(client, staff, w, starts_on="2031-06-01")

    response = patch(client, staff, path(first["id"]), {"version": 1, "ends_on": None})

    assert code_of(response) == (409, "tuition_plan_overlap")


def test_due_offset_applies_to_later_periods_and_versions_are_checked(
    client, staff, module_seeded_url
):
    url = module_seeded_url
    w = ts.world(url)
    ts.set_floor(url, date(2031, 1, 1))
    plan = created(client, staff, w, starts_on="2031-03-10", due_offset_days=2)
    ts.run(url, ts.local(TODAY))

    stale = patch(client, staff, path(plan["id"]), {"version": 9, "due_offset_days": 7})
    assert code_of(stale) == (409, "version_conflict")
    response = patch(
        client, staff, path(plan["id"]), {"version": 1, "due_offset_days": 7}
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    ts.run(url, ts.local(date(2031, 4, 10)))

    march, april = ts.charges(url, plan["id"])
    assert march.due_date == date(2031, 3, 12)  # posted before: unchanged
    assert april.due_date == date(2031, 4, 17)


def test_a_paused_period_is_never_charged_not_even_after_a_resume(
    client, staff, module_seeded_url
):
    url = module_seeded_url
    w = ts.world(url)
    ts.set_floor(url, date(2031, 1, 1))
    plan = created(client, staff, w, starts_on="2031-03-10")
    ts.run(url, ts.local(TODAY))

    response = post(
        client,
        staff,
        path(plan["id"], "/pauses"),
        {"version": 1, "starts_on": "2031-04-10"},
    )
    assert response.status_code == 201, response.text
    paused = response.json()
    assert paused["version"] == 2
    [pause] = paused["pauses"]
    assert (pause["starts_on"], pause["ends_on"]) == ("2031-04-10", None)
    run = ts.run(url, ts.local(date(2031, 5, 20)))
    assert (run.posted, run.paused) == (0, 2)
    resumed = post(
        client,
        staff,
        path(plan["id"], "/resume"),
        {"version": 2, "resume_on": "2031-06-10"},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["pauses"][0]["ends_on"] == "2031-06-09"
    assert queued(url) >= 1
    ts.run(url, ts.local(date(2031, 6, 12)))

    assert ts.starts(url, plan["id"]) == [date(2031, 3, 10), date(2031, 6, 10)]


def test_an_open_pause_shows_paused_and_resuming_on_its_start_removes_it(
    client, staff, module_seeded_url
):
    w = ts.world(module_seeded_url)
    plan = created(client, staff, w, starts_on="2031-03-10")
    post(
        client,
        staff,
        path(plan["id"], "/pauses"),
        {"version": 1, "starts_on": "2031-03-10"},
    )

    assert detail_of(client, staff, plan["id"])["status"] == "paused"
    response = post(
        client,
        staff,
        path(plan["id"], "/resume"),
        {"version": 2, "resume_on": "2031-03-10"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["pauses"] == []
    assert response.json()["status"] == "active"
    assert response.json()["version"] == 3


def test_pause_and_resume_refusals(client, staff, module_seeded_url):
    url = module_seeded_url
    w = ts.world(url)
    ts.set_floor(url, date(2031, 1, 1))
    plan = created(client, staff, w, starts_on="2031-02-10", ends_on="2031-09-09")
    ts.run(url, ts.local(TODAY))  # posts 10 Feb and 10 Mar
    pauses = path(plan["id"], "/pauses")

    def pause(**values: Any) -> Response:
        return post(client, staff, pauses, {"version": 1, **values})

    invalid = (422, "tuition_pause_invalid", ["body", "starts_on"])
    assert field_code(pause(starts_on="2031-04-11"))[:3] == invalid  # not a start
    assert field_code(pause(starts_on="2031-03-10"))[:3] == invalid  # posted
    assert field_code(pause(starts_on="2031-10-10"))[:3] == invalid  # after the end
    assert field_code(pause(starts_on="2031-04-10", ends_on="2031-05-10")) == (
        422,
        "tuition_pause_invalid",
        ["body", "ends_on"],
        "tuition_pause_invalid",
    )
    resume = path(plan["id"], "/resume")
    no_pause = post(client, staff, resume, {"version": 1, "resume_on": "2031-05-10"})
    assert code_of(no_pause) == (409, "tuition_pause_not_open")
    assert pause(starts_on="2031-05-10", ends_on="2031-06-09").status_code == 201
    overlap = post(client, staff, pauses, {"version": 2, "starts_on": "2031-04-10"})
    assert code_of(overlap) == (409, "tuition_pause_overlap")
    assert (
        post(
            client, staff, pauses, {"version": 2, "starts_on": "2031-07-10"}
        ).status_code
        == 201
    )

    def resume_on(day: str) -> Response:
        return post(client, staff, resume, {"version": 3, "resume_on": day})

    resume_invalid = (422, "tuition_resume_invalid", ["body", "resume_on"])
    assert field_code(resume_on("2031-07-11"))[:3] == resume_invalid
    assert field_code(resume_on("2031-06-10"))[:3] == resume_invalid  # before it
    assert code_of(
        post(client, staff, resume, {"version": 1, "resume_on": "2031-08-10"})
    ) == (
        409,
        "version_conflict",
    )
    ended = created(
        client, staff, ts.world(url), starts_on="2031-02-10", ends_on="2031-03-09"
    )
    response = post(
        client,
        staff,
        path(ended["id"], "/pauses"),
        {"version": 1, "starts_on": "2031-03-10"},
    )
    assert code_of(response) == (409, "tuition_plan_ended")


def test_delete_only_a_plan_without_charges(client, staff, module_seeded_url):
    url = module_seeded_url
    ts.set_floor(url, date(2031, 1, 1))
    charged = created(client, staff, ts.world(url), starts_on="2031-03-10")
    ts.run(url, ts.local(TODAY))
    clean = created(client, staff, ts.world(url), starts_on="2031-04-01")
    post(
        client,
        staff,
        path(clean["id"], "/pauses"),
        {"version": 1, "starts_on": "2031-05-01"},
    )

    refused = delete(client, staff, path(charged["id"], "?version=1"))
    assert code_of(refused) == (409, "tuition_plan_has_charges")
    stale = delete(client, staff, path(clean["id"], "?version=1"))
    assert code_of(stale) == (409, "version_conflict")
    response = delete(client, staff, path(clean["id"], "?version=2"))

    assert response.status_code == 204, response.text
    assert get(client, staff, path(clean["id"])).status_code == 404
    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM tuition_plan_pauses WHERE tuition_plan_id = :p",
            {"p": clean["id"]},
        )[0].n
        == 0
    )
    assert any(
        e.action == "delete"
        for e in events(url, record_type="tuition_plans", record_id=clean["id"])
    )


def test_the_key_trigger_refuses_a_new_start(client, staff, module_seeded_url):
    plan = created(client, staff, ts.world(module_seeded_url), starts_on="2031-03-10")

    with pytest.raises(DBAPIError) as caught:
        run_sql(
            module_seeded_url,
            "UPDATE tuition_plans SET starts_on = '2031-03-11' WHERE id = :p",
            {"p": plan["id"]},
        )
    assert getattr(caught.value.orig, "sqlstate", None) == "42501"
    run_sql(
        module_seeded_url,
        "UPDATE tuition_plans SET ends_on = '2031-04-09' WHERE id = :p",
        {"p": plan["id"]},
    )


def test_unknown_plans_are_404(client, staff):
    missing = path(uuid.uuid4())
    assert code_of(get(client, staff, missing)) == (404, "not_found")
    assert code_of(patch(client, staff, missing, {"version": 1})) == (404, "not_found")
    assert code_of(delete(client, staff, f"{missing}?version=1")) == (404, "not_found")
    response = post(
        client, staff, f"{missing}/pauses", {"version": 1, "starts_on": "2031-04-01"}
    )
    assert code_of(response) == (404, "not_found")


def test_list_filters_order_status_and_statement_bound(
    client, staff, module_seeded_url
):
    url = module_seeded_url
    a = ts.world(url)
    b = ts.world(url, frequency="weekly")
    scheduled = created(client, staff, a, starts_on="2031-05-01")
    active = created(client, staff, b, starts_on="2031-03-10")
    ended = created(
        client, staff, ts.world(url), starts_on="2031-02-10", ends_on="2031-03-09"
    )

    page = get(client, staff, ts.PLANS, limit=50)
    assert page.status_code == 200, page.text
    ids = [item["id"] for item in page.json()["items"]]
    assert {scheduled["id"], active["id"], ended["id"]} <= set(ids)
    assert set(page.json()["items"][0]) == ITEM_KEYS
    only_a = get(client, staff, ts.PLANS, student_id=str(a.student)).json()
    assert [i["id"] for i in only_a["items"]] == [scheduled["id"]]
    by_family = get(client, staff, ts.PLANS, family_id=str(b.family)).json()
    assert [i["id"] for i in by_family["items"]] == [active["id"]]
    statuses = get(client, staff, ts.PLANS, status=["ended", "scheduled"]).json()
    assert {i["status"] for i in statuses["items"]} == {"ended", "scheduled"}
    assert code_of(get(client, staff, ts.PLANS, status="nope"))[0] == 422

    for _ in range(5):
        created(client, staff, ts.world(url), starts_on="2031-03-10")
    count, listed = count_statements(
        url,
        lambda s: tuition_plans.list_tuition_plans(
            s,
            student_id=None,
            family_id=None,
            group_id=None,
            statuses=(),
            page=OffsetParams(limit=50, offset=0),
        ),
    )
    assert listed.total >= 8
    assert count <= 10
    names = [item.student.name.casefold() for item in listed.items]
    assert names == sorted(names)
