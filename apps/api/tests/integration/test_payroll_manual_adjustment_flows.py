"""Settled course work and the ``no_rate`` trap no rate can fix, through real
cohorts and routes (M7-08c B2, D3; #110 QA O4 and round 3; ADR-0017 E3).

Each test takes its own decade. Terms, the first rates and periods are set by
SQL, as in every payroll test; the cohorts, closeouts, rate versions, the
``manual`` adjustment, ``recalculate``, ``prepare`` and ``finalize`` are real.
"""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.workflows import payroll_adjustments
from app.workflows.payroll_adjustments import PostAdjustmentsRunResult
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql, settings_for
from tests.integration.payroll_calculation_support import (
    add_rate,
    add_terms,
    clear,
    lines,
)
from tests.integration.payroll_manual_support import CREATED, manual
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    UNRESOLVED,
    code,
    lock_at,
    prepare,
    recalculate,
    row_of,
)
from tests.integration.series_edit_support import Ctx, make_ctx
from tests.integration.test_payroll_per_course import (
    day,
    make_cohort,
    period_of,
    teach,
)
from tests.integration.test_payroll_workflow_course import (
    add_version,
    later,
    team,
    unresolved,
)


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    yield
    clear(module_seeded_url)
    set_floor(module_seeded_url, None)


@pytest.fixture
def ctx(module_seeded_url: str, client: TestClient) -> Ctx:
    return make_ctx(module_seeded_url, client)


def run_job(url: str) -> PostAdjustmentsRunResult:
    return asyncio.run(payroll_adjustments.run_post_adjustments(settings_for(url)))


def course_lines(url: str, period: Any) -> list[tuple[str, int, int, Decimal]]:
    return [
        (
            str(line.class_session_id),
            line.cumulative_sessions,
            line.cohort_sessions,
            line.amount,
        )
        for line in lines(url, period)
        if line.kind != "adjustment"
    ]


# --- B2: a settled course session counts in k and gets no line -----


def test_b2_a_settled_course_session_counts_and_earns_no_line(ctx):
    who = team(ctx)
    url, tutor = ctx.url, ctx.tutor
    set_floor(url, ctx.tl.monday.isoformat())
    add_terms(url, tutor.id)
    add_rate(url, tutor.id, "2000-01-01", "900", model="per_course", kind="course")
    cohort = make_cohort(ctx, 3, weekdays=[1])  # days 0, 7, 14
    stop = add_terms(url, tutor.id, day(ctx, 7), frequency=None)
    add_terms(url, tutor.id, day(ctx, 8))
    period = period_of(ctx, tutor, 0, 21)
    teach(ctx, cohort, 0, 1, 2)
    middle = cohort.id_of(1)

    with frozen_clock(later(ctx)):
        assert code(recalculate(ctx.client, who.finance, period)) == OK
        assert [(i["class_session_id"], i["reason"]) for i in unresolved(ctx, who)] == [
            (middle, "no_pay_terms")
        ]
        assert code(prepare(ctx.client, who.finance, period)) == UNRESOLVED
        response = manual(
            ctx.client, who.owner, tutor.id, "300.00", class_session_id=middle
        )
        assert code(response) == CREATED, response.text
    # The stop goes: the middle session now has terms, and is settled.
    run_sql(url, "DELETE FROM tutor_pay_terms WHERE id = :id", {"id": stop})
    with frozen_clock(later(ctx)):
        assert code(recalculate(ctx.client, who.finance, period)) == OK

    assert course_lines(url, period) == [
        (cohort.id_of(0), 1, 3, Decimal("300.00")),
        (cohort.id_of(2), 3, 3, Decimal("300.00")),
    ]
    lock_at(ctx.client, url, who, later(ctx), period)
    assert row_of(url, period).total_amount == Decimal("900.00")


# --- D3: no_rate that no rate version can fix (#110 QA O4) -----


def test_d3_work_no_version_can_price_is_paid_by_hand(ctx):
    """Cohort A is hourly from day 2; cohort B per-course from its first
    session, day 16, the day of A's third session. One course version per day
    can't be both, so A's day-16 session is ``no_rate`` whatever is tried."""
    who = team(ctx)
    url, tutor = ctx.url, ctx.tutor
    set_floor(url, ctx.tl.monday.isoformat())
    add_terms(url, tutor.id)
    for on, rate, model in ((-30, "100", "per_hour"), (16, "1000", "per_course")):
        made = add_version(ctx, who, day(ctx, on), rate, model=model)
        assert made.status_code == 201, made.text
    cohort_a = make_cohort(ctx, 3, weekdays=[3])  # days 2, 9, 16
    cohort_b = make_cohort(
        ctx,
        2,
        weekdays=[3],
        start_time="18:00",
        starts_on=ctx.tl.monday + timedelta(days=16),
    )  # days 16, 23
    one = period_of(ctx, ctx.tutor, 0, 7)
    two = period_of(ctx, ctx.tutor, 7, 14)
    three = period_of(ctx, ctx.tutor, 21, 60)
    teach(ctx, cohort_a, 0)
    lock_at(ctx.client, url, who, later(ctx), one)
    teach(ctx, cohort_a, 1)
    teach(ctx, cohort_b, 0)
    lock_at(ctx.client, url, who, later(ctx), two)
    paid = [dict(line._mapping) for line in lines(url, two)]
    teach(ctx, cohort_a, 2)
    trapped = cohort_a.id_of(2)
    assert [i["reason"] for i in unresolved(ctx, who)] == ["no_rate"]
    with frozen_clock(later(ctx)):
        assert code(prepare(ctx.client, who.finance, three)) == UNRESOLVED

    # Every version tried is refused or leaves it no_rate.
    for on, rate, model in (
        (14, "100", "per_hour"),
        (16, "100", "per_hour"),
        (16, "999", "per_course"),
        (12, "100", "per_hour"),
    ):
        tried = add_version(ctx, who, day(ctx, on), rate, model=model)
        assert [i["reason"] for i in unresolved(ctx, who)] == ["no_rate"], (
            on,
            model,
            tried.text,
        )

    with frozen_clock(later(ctx)):
        response = manual(
            ctx.client, who.owner, tutor.id, "100.00", class_session_id=trapped
        )
    assert code(response) == CREATED, response.text
    assert response.json()["target_period_id"] == str(three)
    assert unresolved(ctx, who) == []
    assert run_job(url) == PostAdjustmentsRunResult()

    teach(ctx, cohort_b, 1)
    lock_at(ctx.client, url, who, later(ctx), three)
    # B's second half and the hand payment; two never moved.
    assert row_of(url, three).total_amount == Decimal("600.00")
    assert [dict(line._mapping) for line in lines(url, two)] == paid
