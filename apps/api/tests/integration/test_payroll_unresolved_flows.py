"""Unresolved work through real cancellations, substitutes and cohorts (#443 F7,
F8, F9; #107 E13; #442 B3, D4).

Each test takes its own decade. Rates, terms and periods are set by SQL, as in
every payroll test, and the ``finalized`` status too (#110 owns the route). The
requests, offers, cohorts and closeouts are real.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.payroll import service as payroll
from app.workflows import payroll_calculation
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.change_request_support import Scene, make_scene
from tests.integration.conftest import run_sql
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.payroll_calculation_support import (
    add_period_row,
    add_rate,
    add_rule,
    add_terms,
    clear,
    closed_out,
    lines,
    run,
)
from tests.integration.payroll_period_support import finalize, reset, set_floor
from tests.integration.series_edit_support import Ctx, as_user, make_ctx
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_payroll_pay_events import (
    cancelled_by_family,
    from_scene_start,
)
from tests.integration.test_payroll_pay_events import on_payroll as long_period
from tests.integration.test_payroll_per_course import (
    day,
    make_cohort,
    on_payroll,
    period_of,
    teach,
)
from tests.integration.test_session_cancellation import cancel, read
from tests.integration.test_session_substitutions import substituted

OK = (200, None)
PAY_LATE = {"percent": "50"}
ROUTE = "/api/v1/payroll/unresolved-work"


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    set_floor(module_seeded_url, "2000-01-01")
    yield
    clear(module_seeded_url)
    reset(module_seeded_url)


@pytest.fixture
def scene(module_seeded_url: str, client: TestClient) -> Scene:
    return make_scene(module_seeded_url, client)


@pytest.fixture
def ctx(module_seeded_url: str, client: TestClient) -> Ctx:
    return make_ctx(module_seeded_url, client)


def tuesday(ctx: Ctx) -> date:
    return ctx.tl.monday + timedelta(days=1)


def items(url: str, tutor: CreatedUser) -> list[tuple[str, str, str]]:
    """``(session, source, reason)``."""

    async def read_items(session: AsyncSession) -> Any:
        return await payroll_calculation.unresolved_work(session, tutor.id, None)

    return [
        (str(item.class_session_id), item.source, item.reason)
        for item in in_uow(url, read_items)
    ]


def blocking(url: str, period: uuid.UUID) -> list[str]:
    async def read_items(session: AsyncSession) -> Any:
        summary = (await payroll.period_lines(session, period)).period
        return await payroll_calculation.blocking_work(session, summary)

    return [str(item.class_session_id) for item in in_uow(url, read_items)]


def paying_late(scene: Scene) -> None:
    add_rule(
        scene.url,
        "late_cancellation",
        from_scene_start(scene),
        "percent",
        **PAY_LATE,
    )


# --- F8: cancellations (#107 E13) -----


def test_f8_a_late_cancellation_under_percent_with_no_rate_is_listed(scene):
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)
    add_terms(url, tutor.id)
    period = add_period_row(
        url, tutor.id, day(scene.ctx, 0), day(scene.ctx, 60), "monthly"
    )

    cancelled_by_family(scene)

    assert items(url, tutor) == [(scene.session["id"], "cancellation", "no_rate")]
    assert blocking(url, period) == [scene.session["id"]]


def test_f8_a_late_cancellation_with_no_terms_is_no_pay_terms(scene):
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)
    add_rate(url, tutor.id)

    cancelled_by_family(scene)

    assert items(url, tutor) == [(scene.session["id"], "cancellation", "no_pay_terms")]


def test_f8_a_paying_cancellation_before_the_first_period_doesnt_block(scene):
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)
    first = add_period_row(
        url, tutor.id, day(scene.ctx, 70), day(scene.ctx, 76), "weekly"
    )

    cancelled_by_family(scene)

    assert items(url, tutor) == [
        (scene.session["id"], "cancellation", "before_first_period")
    ]
    assert blocking(url, first) == []


def test_f8_a_cancellation_under_none_isnt_listed(scene):
    url, tutor = scene.url, scene.ctx.tutor

    cancelled_by_family(scene)

    assert items(url, tutor) == []


def test_f8_a_salaried_tutor_with_no_rate_isnt_listed(scene):
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)
    add_terms(url, tutor.id, salary="9000")

    cancelled_by_family(scene)

    assert items(url, tutor) == []


def test_f8_a_cancellation_with_a_rate_and_with_a_line_isnt_listed(scene):
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)
    period = long_period(scene)

    cancelled_by_family(scene)
    assert items(url, tutor) == []

    run(url, period)
    [line] = lines(url, period)
    assert line.kind == "late_cancellation"
    # The basis goes, and the line stays: the work is paid.
    add_terms(url, tutor.id, day(scene.ctx, -1), None)
    assert items(url, tutor) == []


def test_f8_an_on_time_cancellation_isnt_listed(scene):
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)

    cancelled_by_family(scene, ahead=48)
    assert items(url, tutor) == []


def test_f8_a_center_cancellation_isnt_listed(scene):
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)

    answered = cancel(scene.ctx, scene.session["id"], scene.session["version"])

    assert code_of(answered) == OK, answered.text
    assert items(url, tutor) == []


def test_one_tutors_cancellation_isnt_anothers(scene, module_seeded_url, client):
    url = scene.url
    paying_late(scene)
    other = make_scene(url, client)
    period = add_period_row(
        url, other.ctx.tutor.id, day(other.ctx, 0), day(other.ctx, 60), "monthly"
    )

    cancelled_by_family(scene)

    assert items(url, other.ctx.tutor) == []
    assert blocking(url, period) == []
    assert items(url, scene.ctx.tutor) == [
        (scene.session["id"], "cancellation", "no_pay_terms")
    ]


def test_a_cancellation_with_no_tutor_is_nobodys_work(scene):
    # Set by SQL: no real path clears the tutor of a cancelled session.
    url = scene.url
    paying_late(scene)
    cancelled_by_family(scene)
    run_sql(
        url,
        "UPDATE class_sessions SET tutor_user_id = NULL WHERE id = :id",
        {"id": scene.session["id"]},
    )
    cookie = as_user(scene.ctx, scene.ctx.owner, scene.ctx.tl.now)

    response = get(scene.ctx.client, cookie, ROUTE, limit=100)

    assert code_of(response) == OK, response.text
    assert scene.session["id"] not in {
        item["class_session_id"] for item in response.json()["items"]
    }


def near_midnight(scene: Scene) -> tuple[str, str]:
    """The cancelled session moved by SQL to 23:00 in the center, 05:00 the next
    day in UTC: no real path moves a cancelled session. Returns its center date
    and the UTC date."""
    day_after = scene.ctx.tl.monday + timedelta(days=8)
    starts = datetime.combine(day_after, time(5), tzinfo=UTC)
    run_sql(
        scene.url,
        "UPDATE class_sessions SET starts_at = CAST(:s AS timestamptz),"
        " ends_at = CAST(:s AS timestamptz)"
        " + (scheduled_minutes * interval '1 minute') WHERE id = :id",
        {"s": starts, "id": scene.session["id"]},
    )
    return (day_after - timedelta(days=1)).isoformat(), day_after.isoformat()


def test_a_cancellations_service_date_is_the_center_date(scene):
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)
    cancelled_by_family(scene)
    center, utc = near_midnight(scene)

    async def read_items(session: AsyncSession) -> Any:
        return await payroll_calculation.unresolved_work(session, tutor.id, None)

    [item] = in_uow(url, read_items)

    assert item.service_date.isoformat() == center
    assert center != utc


def test_a5_a_cancellation_before_the_floor_by_the_center_date(scene):
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)
    cancelled_by_family(scene)
    center, utc = near_midnight(scene)
    expected = [(scene.session["id"], "cancellation", "no_pay_terms")]

    set_floor(url, center)
    assert items(url, tutor) == expected

    # The UTC date is the floor, and the center date is before it.
    set_floor(url, utc)
    assert items(url, tutor) == []


def test_a_cancellation_of_another_initiator_isnt_listed(scene):
    # Set by SQL: a real tutor request is always excused, so it never gets here.
    url, tutor = scene.url, scene.ctx.tutor
    paying_late(scene)
    cancelled_by_family(scene)
    assert len(items(url, tutor)) == 1
    run_sql(
        url,
        "UPDATE session_change_requests SET initiated_by = 'tutor',"
        " student_id = NULL WHERE class_session_id = :id",
        {"id": scene.session["id"]},
    )

    assert items(url, tutor) == []


# --- F7: a substitute with no terms (C3) -----


def test_f7_a_substitute_with_no_terms_is_listed_not_the_original(scene):
    url = scene.url
    ctx = scene.ctx
    substitute = new_tutor_user(url, qualified_for=(ctx.subject,))
    period = long_period(scene)
    cookie = as_user(ctx, ctx.owner, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        item = substituted(ctx.client, cookie, scene.session, substitute.id)
    answer = as_user(ctx, substitute, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        accepted = post(
            ctx.client,
            answer,
            f"/api/v1/assignment-offers/{item['offer_id']}/accept",
            None,
        )
    assert code_of(accepted) == OK, accepted.text
    closed_out(ctx, read(ctx, scene.session["id"]), tutor=substitute, minutes=60)

    assert items(url, substitute) == [(scene.session["id"], "closeout", "no_pay_terms")]
    assert items(url, ctx.tutor) == []
    assert blocking(url, period) == []


# --- F9: per-course -----


def test_f9_per_course_only_after_the_first_date_is_no_rate(ctx):
    cohort = make_cohort(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 1), "1000", kind="course", model="per_course"
    )
    period = period_of(ctx, ctx.tutor, 0, 70)
    teach(ctx, cohort, 0, 1)
    run(ctx.url, period)

    assert lines(ctx.url, period) == []
    assert {item[0]: item[2] for item in items(ctx.url, ctx.tutor)} == {
        cohort.id_of(0): "no_rate",
        cohort.id_of(1): "no_rate",
    }
    assert set(blocking(ctx.url, period)) == {cohort.id_of(0), cohort.id_of(1)}

    # A back-dated version on the first date gives every session its basis.
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 0), "900", kind="course", model="per_course"
    )

    assert items(ctx.url, ctx.tutor) == []
    run(ctx.url, period)
    assert len(lines(ctx.url, period)) == 2


def test_a_per_course_cohort_with_a_rate_is_no_item(ctx):
    cohort = make_cohort(ctx)
    period = on_payroll(ctx)
    teach(ctx, cohort, 0)

    assert items(ctx.url, ctx.tutor) == []
    run(ctx.url, period)
    assert items(ctx.url, ctx.tutor) == []


def test_a_cohorts_first_date_is_its_own_with_interleaved_cohorts(ctx):
    early = make_cohort(ctx, 2, weekdays=[1, 3], start_time="16:00")
    late = make_cohort(
        ctx,
        2,
        weekdays=[3, 5],
        start_time="18:00",
        starts_on=ctx.tl.monday + timedelta(days=2),
    )
    add_terms(ctx.url, ctx.tutor.id)
    # From the first date of ``late``, after the first date of ``early``.
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 2), "1000", kind="course", model="per_course"
    )
    period = period_of(ctx, ctx.tutor, 0, 70)
    teach(ctx, early, 0)
    teach(ctx, late, 0)
    teach(ctx, early, 1)
    teach(ctx, late, 1)
    unpaid = {early.id_of(0), early.id_of(1)}

    # Before a run both cohorts are candidates, so each needs its own date.
    assert {item[0] for item in items(ctx.url, ctx.tutor)} == unpaid
    run(ctx.url, period)
    assert {item[0] for item in items(ctx.url, ctx.tutor)} == unpaid
    assert {str(line.class_session_id) for line in lines(ctx.url, period)} == {
        late.id_of(0),
        late.id_of(1),
    }


def test_a_cohorts_first_date_is_the_center_date(ctx):
    # 19:00 on Monday in the center is Tuesday in UTC.
    cohort = make_cohort(ctx, 2, start_time="19:00")
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 1), "1000", kind="course", model="per_course"
    )
    period = period_of(ctx, ctx.tutor, 0, 70)
    teach(ctx, cohort, 0)
    run(ctx.url, period)

    assert lines(ctx.url, period) == []
    assert items(ctx.url, ctx.tutor) == [(cohort.id_of(0), "closeout", "no_rate")]


def test_a_cancelled_first_session_keeps_the_cohorts_first_date(ctx):
    cohort = make_cohort(ctx)
    answered = cancel(ctx, cohort.id_of(0), cohort.session(0)["version"])
    assert code_of(answered) == OK, answered.text
    add_terms(ctx.url, ctx.tutor.id)
    # On the session that runs first, but after the cancelled one.
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 1), "1000", kind="course", model="per_course"
    )
    period = period_of(ctx, ctx.tutor, 0, 70)
    teach(ctx, cohort, 1)
    run(ctx.url, period)

    assert lines(ctx.url, period) == []
    assert items(ctx.url, ctx.tutor) == [(cohort.id_of(1), "closeout", "no_rate")]


def test_442_d4_a_walked_session_in_a_terms_gap_is_no_pay_terms(ctx):
    cohort = make_cohort(ctx)
    period = on_payroll(ctx)
    add_terms(ctx.url, ctx.tutor.id, day(ctx, 1), None)
    add_terms(ctx.url, ctx.tutor.id, day(ctx, 3))
    teach(ctx, cohort, 0, 1, 2)

    run(ctx.url, period)

    assert [str(line.class_session_id) for line in lines(ctx.url, period)] == [
        cohort.id_of(0),
        cohort.id_of(2),
    ]
    assert items(ctx.url, ctx.tutor) == [
        (cohort.id_of(1), "closeout", "no_pay_terms"),
    ]
    assert blocking(ctx.url, period) == [cohort.id_of(1)]


def test_f10_a_course_candidate_costs_two_statements_whatever_the_cohorts(ctx):
    first = make_cohort(ctx, 2, start_time="16:00")
    add_terms(ctx.url, ctx.tutor.id, "2000-01-01", None)
    teach(ctx, first, 0, 1)

    async def reader(session: AsyncSession) -> Any:
        return await payroll_calculation.unresolved_work(session, ctx.tutor.id, None)

    async def route(session: AsyncSession) -> Any:
        return await payroll_calculation.list_unresolved_work(
            session,
            page=payroll_calculation.CursorParams(25, None),
            tutor_user_id=ctx.tutor.id,
        )

    small, found = count_statements(ctx.url, reader)
    assert (small, len(found)) == (10, 2)

    second = make_cohort(ctx, 2, start_time="18:00")
    third = make_cohort(
        ctx, 2, start_time="12:00", weekdays=[2, 4], starts_on=tuesday(ctx)
    )
    teach(ctx, second, 0, 1)
    teach(ctx, third, 0, 1)

    large, found = count_statements(ctx.url, reader)
    assert (large, len(found)) == (10, 6)
    assert count_statements(ctx.url, route)[0] == 11


def test_a_finalized_course_line_anchors_and_isnt_an_item(ctx):
    cohort = make_cohort(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url,
        ctx.tutor.id,
        "2000-01-01",
        "1000",
        kind="course",
        model="per_course",
    )
    closed = period_of(ctx, ctx.tutor, 0, 2)
    following = period_of(ctx, ctx.tutor, 2, 70)
    teach(ctx, cohort, 0)
    run(ctx.url, closed)
    finalize(ctx.url, closed, ctx.owner.id)
    add_terms(ctx.url, ctx.tutor.id, day(ctx, 1), None)
    add_terms(ctx.url, ctx.tutor.id, day(ctx, 3))
    teach(ctx, cohort, 1, 2)
    run(ctx.url, following)

    # k = 1 stays with the finalized line, the gap counts, so the third is k = 3.
    assert [
        (str(line.class_session_id), line.cumulative_sessions)
        for line in lines(ctx.url, following)
    ] == [(cohort.id_of(2), 3)]
    assert items(ctx.url, ctx.tutor) == [
        (cohort.id_of(1), "closeout", "no_pay_terms"),
    ]


def test_a_session_of_a_finalized_period_with_no_line_but_a_basis_isnt_an_item(ctx):
    # #111 pays it: there is nothing for Finance to fix.
    cohort = make_cohort(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url,
        ctx.tutor.id,
        "2000-01-01",
        "1000",
        kind="course",
        model="per_course",
    )
    closed = period_of(ctx, ctx.tutor, 0, 2)
    finalize(ctx.url, closed, ctx.owner.id)
    teach(ctx, cohort, 0)

    assert lines(ctx.url, closed) == []
    assert items(ctx.url, ctx.tutor) == []


def test_work_before_the_first_period_in_a_cohort_doesnt_block(ctx):
    cohort = make_cohort(ctx)
    add_rate(
        ctx.url,
        ctx.tutor.id,
        "2000-01-01",
        "1000",
        kind="course",
        model="per_course",
    )
    add_terms(ctx.url, ctx.tutor.id, day(ctx, 2))
    first = period_of(ctx, ctx.tutor, 2, 70, first=True)
    teach(ctx, cohort, 0, 1)
    run(ctx.url, first)

    assert items(ctx.url, ctx.tutor) == [
        (cohort.id_of(0), "closeout", "before_first_period"),
    ]
    assert blocking(ctx.url, first) == []
    # The session counts in the walk, as #442 D1: this line is the second share.
    [line] = lines(ctx.url, first)
    assert (line.cumulative_sessions, line.cohort_sessions) == (2, 3)
