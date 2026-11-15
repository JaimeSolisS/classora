"""The tutor of a cohort through the #233 series offer (#65 I2, C5, C6, E5).

"Now" is the Friday before the cohort's first Monday, 12:00 in Mexico City,
so the first occurrence (Monday 16:00) is more than 48 hours ahead (the
default offer deadline) and the 12-week horizon holds every session of a
10-session Monday/Wednesday cohort.
"""

from datetime import timedelta
from typing import Any

import pytest

from app.modules.identity.permissions import ACADEMIC_COORDINATOR, OWNER
from app.modules.scheduling import service as scheduling
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.catalog_support import count_statements, get
from tests.integration.cohort_support import (
    cohort_body,
    cohort_rows,
    cohort_series,
    create_cohort,
    new_template,
)
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of
from tests.integration.series_support import create_series, series_body, timeline
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_class_session_create import (
    new_room,
    new_student,
    new_subject,
)
from tests.integration.test_series_offers import (
    REASSIGNMENT,
    answer,
    as_user,
    offer_series,
    run_job,
    series_offers,
    series_state,
)

CREATED = (201, None)
OK = (200, None)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Any:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


class Ctx:
    def __init__(self, url: str) -> None:
        self.tl = timeline()
        self.now = self.tl.now - timedelta(days=2)
        self.subject = new_subject(url)
        self.owner = create_user(url, roles=(OWNER,))
        self.coordinator = create_user(url, roles=(ACADEMIC_COORDINATOR,))
        self.tutor = new_tutor_user(url, qualified_for=[self.subject])


@pytest.fixture
def ctx(module_seeded_url: str) -> Ctx:
    return Ctx(module_seeded_url)


def make_cohort(
    client, ctx: Ctx, *, session_count: int = 12, **changes: Any
) -> dict[str, Any]:
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        template = new_template(
            client, cookie, [ctx.subject], session_count=session_count
        )
        response = create_cohort(
            client,
            cookie,
            cohort_body(
                template=template["id"],
                subject=ctx.subject,
                starts_on=ctx.tl.monday,
                **changes,
            ),
        )
    assert code_of(response) == CREATED, response.text
    created: dict[str, Any] = response.json()
    return created


def test_a_coordinator_offers_the_series_and_the_tutor_accepts(
    module_seeded_url, client, ctx
):
    url = module_seeded_url
    # 30 Monday/Wednesday sessions: 24 in the horizon, 6 created later.
    cohort = make_cohort(client, ctx, session_count=30)
    [series] = cohort_series(url, cohort["id"])
    assert {r.state for r in cohort_rows(url, cohort["id"])} == {"unassigned"}

    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.coordinator, ctx.now)
        offered = offer_series(
            client,
            cookie,
            series.id,
            version=series.version,
            tutor_user_id=str(ctx.tutor.id),
        )
    assert code_of(offered) == CREATED, offered.text
    offer = offered.json()["offer"]
    assert offer["series"]["kind"] == "course"
    assert offer["series"]["cohort"] == {"id": cohort["id"], "name": cohort["name"]}
    rows = cohort_rows(url, cohort["id"])
    assert {r.state for r in rows} == {"offered"}

    # The tutor's list names the cohort.
    with frozen_clock(ctx.now):
        tutor_cookie = as_user(client, ctx.tutor, ctx.now)
        mine = get(client, tutor_cookie, "/api/v1/assignment-offers").json()
    [item] = [i for i in mine["items"] if i["id"] == offer["id"]]
    assert item["series"]["cohort"] == {"id": cohort["id"], "name": cohort["name"]}

    accepted = answer(client, ctx, offer["id"], "accept", ctx.now + timedelta(hours=1))
    assert code_of(accepted) == OK, accepted.text
    assert {r.state for r in cohort_rows(url, cohort["id"])} == {"confirmed"}

    # Rows the next run creates are confirmed too.
    assert len(rows) == 24
    run_job(url, ctx.now + timedelta(weeks=4))
    rows = cohort_rows(url, cohort["id"])
    assert len(rows) == 30
    assert {r.state for r in rows} == {"confirmed"}
    assert {str(r.tutor_user_id) for r in rows} == {str(ctx.tutor.id)}

    # The details carry the cohort.
    with frozen_clock(ctx.now):
        owner = as_user(client, ctx.owner, ctx.now)
        session = get(client, owner, f"/api/v1/class-sessions/{rows[0].id}").json()
        detail = get(client, owner, f"/api/v1/class-series/{series.id}").json()
        read = get(client, owner, f"/api/v1/cohorts/{cohort['id']}").json()
    assert session["cohort_id"] == cohort["id"]
    assert detail["cohort_id"] == cohort["id"]
    assert read["series"]["assignment"]["status"] == "accepted"
    assert read["series"]["tutor"]["id"] == str(ctx.tutor.id)


def test_a_declined_cohort_offer_returns_the_rows_and_raises_one_item(
    module_seeded_url, client, ctx
):
    url = module_seeded_url
    cohort = make_cohort(client, ctx, tutor=ctx.tutor.id)
    [series] = cohort_series(url, cohort["id"])
    [offer] = series_offers(url, series.id)

    declined = answer(
        client,
        ctx,
        offer.id,
        "decline",
        ctx.now + timedelta(hours=1),
        {"note": "No puedo"},
    )

    assert code_of(declined) == OK, declined.text
    rows = cohort_rows(url, cohort["id"])
    assert {(r.state, r.tutor_user_id) for r in rows} == {("unassigned", None)}
    state = series_state(url, series.id)
    assert (state.tutor_user_id, state.assignment_status) == (None, None)
    items = run_sql(
        url,
        "SELECT id FROM approval_requests WHERE request_type = :t AND subject_id = :s",
        {"t": REASSIGNMENT, "s": str(offer.id)},
    )
    assert len(items) == 1


def test_a_page_of_offers_with_a_cohort_keeps_the_statement_budget(
    module_seeded_url, client, ctx
):
    """#52 G2 / #233 F1: at most seven statements, also for a page mixing a
    cohort's series offer with an individual series offer (students and
    rooms to resolve)."""
    url = module_seeded_url
    make_cohort(client, ctx, tutor=ctx.tutor.id, room=new_room(url))
    with frozen_clock(ctx.now):
        cookie = as_user(client, ctx.owner, ctx.now)
        individual = create_series(
            client,
            cookie,
            series_body(
                student=new_student(url),
                subject=ctx.subject,
                tutor=ctx.tutor.id,
                room=new_room(url),
                starts_on=ctx.tl.monday,
                start_time="09:00",
                count=4,
            ),
        )
    assert code_of(individual) == CREATED, individual.text

    count, page = count_statements(
        url,
        lambda session: scheduling.list_my_offers(
            session, caller_id=ctx.tutor.id, status="pending", limit=20, offset=0
        ),
    )

    assert len(page.items) == 2
    cohorts = [i.series.cohort for i in page.items if i.series is not None]
    assert sum(cohort is not None for cohort in cohorts) == 1
    assert count <= 7
