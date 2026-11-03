"""Work after lock through real cancellations, salaried dates and cohorts (#111
C2, C3, C8, C9; #443 D8, A5; ADR-0017 E11 and its #111 notes).

Each test takes its own decade. Terms, rates, rules and periods are set by SQL,
as in every payroll test. **The ``finalized`` status is set by SQL here, on
purpose** (#443 QA D1): no route sequence leaves a paying cancellation without a
line in a finalized period, because it exists before its period ends (#111 C2).
The requests, closeouts, cohorts, rate and terms writes, and the next period's
``prepare`` and ``finalize``, are real.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.payroll import service as payroll
from app.workflows import payroll_adjustments, payroll_calculation
from app.workflows.payroll_adjustments import PostAdjustmentsRunResult
from tests.integration.auth_support import CreatedUser, auth_client
from tests.integration.catalog_support import post
from tests.integration.change_request_support import Scene, make_scene
from tests.integration.conftest import run_sql, settings_for
from tests.integration.guardian_support import delete
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
from tests.integration.payroll_workflow_support import (
    OK,
    UNRESOLVED,
    code,
    lock_at,
    prepare,
    row_of,
    team_at,
)
from tests.integration.series_edit_support import Ctx, make_ctx, owner_call
from tests.integration.test_payroll_pay_events import (
    cancelled_by_family,
    from_scene_start,
)
from tests.integration.test_payroll_per_course import (
    day,
    make_cohort,
    period_of,
    teach,
)
from tests.integration.test_session_cancellation import cancel, one_off

CREATED = (201, None)


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


# --- Helpers -----


def run_job(url: str) -> PostAdjustmentsRunResult:
    return asyncio.run(payroll_adjustments.run_post_adjustments(settings_for(url)))


def adjustments(url: str, tutor: CreatedUser) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM payroll_adjustments WHERE tutor_user_id = :t"
        " ORDER BY created_at, id",
        {"t": tutor.id},
    )


def items(url: str, tutor: CreatedUser) -> list[tuple[str, str, str]]:
    async def read(session: AsyncSession) -> Any:
        return await payroll_calculation.unresolved_work(session, tutor.id, None)

    return [
        (str(item.class_session_id), item.source, item.reason)
        for item in in_uow(url, read)
    ]


def blocking(url: str, period: uuid.UUID) -> list[str]:
    async def read(session: AsyncSession) -> Any:
        summary = (await payroll.period_lines(session, period)).period
        return await payroll_calculation.blocking_work(session, summary)

    return [str(item.class_session_id) for item in in_uow(url, read)]


def frozen_state(url: str, period: uuid.UUID) -> tuple[Any, list[Any]]:
    return row_of(url, period), run_sql(
        url,
        "SELECT * FROM payroll_lines WHERE payroll_period_id = :p ORDER BY id",
        {"p": period},
    )


def weeks(scene: Scene, first: int, count: int, *, first_period: bool) -> uuid.UUID:
    """A weekly-looking period of ``count`` weeks from week ``first``."""
    start = scene.ctx.tl.week(first)
    end = start + timedelta(weeks=count, days=-1)
    return add_period_row(
        scene.url,
        scene.ctx.tutor.id,
        start.isoformat(),
        end.isoformat(),
        "weekly",
        first=first_period,
    )


def rate_route(scene: Scene, on: str, rate: str = "120") -> Any:
    tutor = scene.ctx.tutor
    return owner_call(
        scene.ctx,
        scene.ctx.tl.now,
        lambda cookie: post(
            scene.ctx.client,
            cookie,
            f"/api/v1/payroll/tutors/{tutor.id}/rates",
            {
                "class_kind": "individual",
                "effective_from": on,
                "rate_model": "per_hour",
                "rate": rate,
            },
        ),
    )


def terms_route(scene: Scene, on: str, frequency: str | None) -> Any:
    tutor = scene.ctx.tutor
    return owner_call(
        scene.ctx,
        scene.ctx.tl.now,
        lambda cookie: post(
            scene.ctx.client,
            cookie,
            f"/api/v1/payroll/tutors/{tutor.id}/pay-terms",
            {"effective_from": on, "pay_frequency": frequency, "salary_amount": None},
        ),
    )


def after(scene: Scene, weeks_on: int) -> datetime:
    """An instant after week ``weeks_on`` ended."""
    return scene.ctx.tl.at(scene.ctx.tl.week(weeks_on + 1, 2), "12:00")


# --- C2: a finalized paying cancellation (#443 D8, both halves; MUST) -----


def finalized_cancellation(scene: Scene) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Hourly terms with no rate for the kind, a paying ``late`` rule, the
    session's weeks finalized by SQL around a real family cancellation."""
    url, tutor = scene.url, scene.ctx.tutor
    add_rule(url, "late_cancellation", from_scene_start(scene), "percent", percent="50")
    terms = add_terms(url, tutor.id)
    first = weeks(scene, 1, 2, first_period=True)
    second = weeks(scene, 3, 2, first_period=False)
    cancelled_by_family(scene)
    finalize(url, first, scene.ctx.owner.id)
    return first, second, terms


def test_c2_a_finalized_paying_cancellation_is_listed_then_paid_once(
    scene, module_seeded_url, client
):
    url, tutor = scene.url, scene.ctx.tutor
    first, second, _ = finalized_cancellation(scene)
    session_id = scene.session["id"]
    before = frozen_state(url, first)

    # The listing: no rate, and it blocks the next period.
    assert items(url, tutor) == [(session_id, "cancellation", "no_rate")]
    assert blocking(url, second) == [session_id]
    team = team_at(url, client, after(scene, 4))
    from tests.conftest import frozen_clock

    with frozen_clock(after(scene, 4)):
        assert code(prepare(client, team.finance, second)) == UNRESOLVED
    assert run_job(url) == PostAdjustmentsRunResult()

    # A back-dated rate: no finalized line re-priced, so 201; the item goes.
    assert code_of(rate_route(scene, from_scene_start(scene))) == CREATED
    assert items(url, tutor) == []
    assert blocking(url, second) == []

    # The anti-join: one late approval keyed by the session.
    assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    [adjustment] = adjustments(url, tutor)
    assert adjustment.kind == "late_approval"
    assert adjustment.source_key == f"cancellation:{session_id}"
    assert adjustment.approval_id is None
    assert str(adjustment.class_session_id) == session_id
    assert (adjustment.origin_period_id, adjustment.target_period_id) == (
        first,
        second,
    )
    assert adjustment.line_kind == "late_cancellation"
    # 50 % of 60 minutes at 120.00 an hour.
    assert adjustment.amount == Decimal("60.00")
    assert adjustment.session_pay_rule_id is not None
    assert run_job(url) == PostAdjustmentsRunResult()

    # The next period prepares and finalizes, with the adjustment in it.
    lock_at(client, url, team, after(scene, 4), second)
    assert row_of(url, second).total_amount == Decimal("60.00")
    assert frozen_state(url, first) == before


def test_c9_settled_work_stays_off_the_list_when_its_basis_goes(scene):
    url, tutor = scene.url, scene.ctx.tutor
    first, second, terms = finalized_cancellation(scene)
    add_rate(url, tutor.id)
    assert run_job(url).posted == 1

    removed = owner_call(
        scene.ctx,
        scene.ctx.tl.now,
        lambda cookie: delete(
            scene.ctx.client,
            cookie,
            f"/api/v1/payroll/tutors/{tutor.id}/pay-terms/{terms}",
        ),
    )
    assert removed.status_code == 204, removed.text

    assert items(url, tutor) == []
    assert blocking(url, second) == []
    assert run_job(url) == PostAdjustmentsRunResult()
    assert len(adjustments(url, tutor)) == 1


def test_c2_a_cancellation_under_none_is_never_a_candidate(scene):
    url, tutor = scene.url, scene.ctx.tutor
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    first = weeks(scene, 1, 2, first_period=True)
    weeks(scene, 3, 2, first_period=False)
    cancelled_by_family(scene)
    finalize(url, first, scene.ctx.owner.id)

    assert run_job(url) == PostAdjustmentsRunResult()
    assert adjustments(url, tutor) == []


def test_c2_a_center_cancellation_is_never_a_candidate(scene):
    url, tutor = scene.url, scene.ctx.tutor
    add_rule(url, "late_cancellation", from_scene_start(scene), "percent", percent="50")
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    first = weeks(scene, 1, 2, first_period=True)
    weeks(scene, 3, 2, first_period=False)
    answered = cancel(scene.ctx, scene.session["id"], scene.session["version"])
    assert code_of(answered) == OK, answered.text
    finalize(url, first, scene.ctx.owner.id)

    assert run_job(url) == PostAdjustmentsRunResult()


# --- C3: covered owes nothing (#111 E4, #443 A5) -----


def salaried_week(
    scene: Scene, *, rate_before: bool
) -> tuple[uuid.UUID, uuid.UUID, Any]:
    """A salaried tutor with no rate for the kind: a family late cancellation
    and a closeout in the same week, the week recalculated (the salary line) and
    finalized, then the closeout approved."""
    url, ctx = scene.url, scene.ctx
    add_rule(url, "late_cancellation", from_scene_start(scene), "percent", percent="50")
    add_terms(url, ctx.tutor.id, salary="3000.00")
    if rate_before:
        add_rate(url, ctx.tutor.id)
    first = weeks(scene, 2, 1, first_period=True)
    second = weeks(scene, 3, 1, first_period=False)
    set_floor(url, ctx.tl.week(2).isoformat())
    late = one_off(ctx, accept=True, starts_at=ctx.tl.at(ctx.tl.week(2, 3), "10:00"))
    cancelled_by_family(scene)
    run(url, first)
    finalize(url, first, ctx.owner.id)
    closed_out(ctx, late, minutes=60)
    return first, second, late


def test_c3_covered_work_is_neither_paid_nor_listed(scene):
    url, ctx = scene.url, scene.ctx
    first, second, _ = salaried_week(scene, rate_before=False)
    assert [line.kind for line in lines(url, first)] == ["salary"]
    before = frozen_state(url, first)

    # A rate back-dated under the salary: 201 (a covered line has no price).
    assert code_of(rate_route(scene, from_scene_start(scene))) == CREATED

    assert run_job(url) == PostAdjustmentsRunResult()
    assert items(url, ctx.tutor) == []

    # A stop dated inside that week (pm-108): still covered, nothing blocks.
    assert code_of(terms_route(scene, ctx.tl.week(2, 4).isoformat(), None)) == CREATED
    assert items(url, ctx.tutor) == []
    assert blocking(url, second) == []
    assert run_job(url) == PostAdjustmentsRunResult()
    assert adjustments(url, ctx.tutor) == []
    assert frozen_state(url, first) == before


def test_c3_a_rate_that_existed_at_finalize_still_pays(scene):
    url, ctx = scene.url, scene.ctx
    first, second, late = salaried_week(scene, rate_before=True)
    # The cancellation was paid on time, on the rate.
    assert sorted(line.kind for line in lines(url, first)) == [
        "late_cancellation",
        "salary",
    ]

    assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)

    [adjustment] = adjustments(url, ctx.tutor)
    assert str(adjustment.class_session_id) == late["id"]
    assert adjustment.amount == Decimal("120.00")
    assert adjustment.target_period_id == second


# --- C8: per-course is skipped (M7-08d) -----


def course_week(ctx: Ctx, model: str, rate: str) -> tuple[Any, uuid.UUID]:
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, "2000-01-01", rate, model=model, kind="course")
    cohort = make_cohort(ctx)
    first = period_of(ctx, ctx.tutor, 0, 7)
    second = period_of(ctx, ctx.tutor, 7, 63, first=False)
    finalize(ctx.url, first, ctx.owner.id)
    teach(ctx, cohort, 0)
    return cohort, second


def test_c8_a_late_session_of_a_per_course_cohort_is_skipped(ctx):
    cohort, _ = course_week(ctx, "per_course", "1000")

    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, skipped_course=1)
    assert adjustments(ctx.url, ctx.tutor) == []


def test_c8_an_ordinary_course_session_is_paid(ctx):
    cohort, second = course_week(ctx, "per_hour", "120")

    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    [adjustment] = adjustments(ctx.url, ctx.tutor)
    assert str(adjustment.class_session_id) == cohort.id_of(0)
    assert (adjustment.class_kind, adjustment.amount) == ("course", Decimal("120.00"))
    assert adjustment.target_period_id == second
    assert day(ctx, 0) == str(adjustment.service_date)
