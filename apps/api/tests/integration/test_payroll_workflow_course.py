"""Oldest first, and #442's finalized-state behaviours through the real routes
(#110 E2, H1-H8, J7, J8; ADR-0017 E3, the #442 notes).

Each period here is prepared by Finance Staff and finalized by an Owner, by route.
#442 could only simulate that with a SQL finalize. Every expected amount is worked
by hand from ``rate x k / N``; the clock of the routes is a year after the cohort.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity.permissions import OWNER
from app.modules.payroll import service as payroll
from app.workflows import payroll_calculation, payroll_workflow
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.change_request_support import Scene, make_scene
from tests.integration.conftest import short_lived_engine
from tests.integration.lifecycle_support import code_of
from tests.integration.payroll_calculation_support import (
    add_period_row,
    add_rate,
    add_rule,
    add_terms,
    clear,
    closed_out,
    lines,
)
from tests.integration.payroll_workflow_support import (
    OK,
    OUT_OF_ORDER,
    STALE,
    Team,
    code,
    finalize,
    lock_at,
    locked,
    prepare,
    prepared_at,
    recalculate,
    return_period,
    row_of,
    status_of,
    team_at,
)
from tests.integration.series_edit_support import Ctx, make_ctx
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_attendance import principal_of
from tests.integration.test_class_session_makeups import book, booked
from tests.integration.test_payroll_pay_events import (
    cancelled_by_family,
    cancelled_target,
    makeup_of,
)
from tests.integration.test_payroll_pay_events import (
    on_payroll as hourly_payroll,
)
from tests.integration.test_payroll_per_course import (
    amounts,
    cents,
    grow,
    make_cohort,
    period_of,
    rows_of,
    shares,
    teach,
    two_periods,
)
from tests.integration.test_session_cancellation import cancel

WAIT_LIMIT = 15.0


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    yield
    clear(module_seeded_url)


@pytest.fixture
def ctx(module_seeded_url: str, client: TestClient) -> Ctx:
    return make_ctx(module_seeded_url, client)


@pytest.fixture
def scene(module_seeded_url: str, client: TestClient) -> Scene:
    return make_scene(module_seeded_url, client)


def later(ctx: Ctx) -> datetime:
    """After every period of these tests."""
    return datetime.combine(
        ctx.tl.monday + timedelta(days=366), datetime.min.time(), UTC
    ) + timedelta(hours=18)


def team(ctx: Ctx) -> Team:
    return team_at(ctx.url, ctx.client, later(ctx))


def prepare_all(ctx: Ctx, who: Team, *periods: Any) -> None:
    with frozen_clock(later(ctx)):
        for period in periods:
            response = prepare(ctx.client, who.finance, period)
            assert code(response) == OK, response.text


def final(ctx: Ctx, who: Team, period: Any) -> Any:
    with frozen_clock(later(ctx)):
        return finalize(ctx.client, ctx.url, who.owner, period)


def total(ctx: Ctx, *periods: Any) -> Decimal:
    return sum(
        (row_of(ctx.url, period).total_amount for period in periods), Decimal("0.00")
    )


# --- H1: oldest first -----


def test_h1_a_later_period_waits_for_every_earlier_one(ctx):
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    one = period_of(ctx, ctx.tutor, 0, 7)
    two = period_of(ctx, ctx.tutor, 7, 7)
    prepare_all(ctx, who, one, two)

    refused = final(ctx, who, two)

    assert code(refused) == OUT_OF_ORDER
    assert refused.json()["details"] == {
        "earliest_period_id": str(one),
        "status": "prepared",
    }
    assert code(final(ctx, who, one)) == OK
    assert code(final(ctx, who, two)) == OK


def test_h1_an_earlier_open_period_refuses_and_the_oldest_is_named(ctx):
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    one = period_of(ctx, ctx.tutor, 0, 7)
    two = period_of(ctx, ctx.tutor, 7, 7)
    three = period_of(ctx, ctx.tutor, 14, 7)
    prepare_all(ctx, who, two, three)

    refused = final(ctx, who, three)

    assert code(refused) == OUT_OF_ORDER
    assert refused.json()["details"] == {
        "earliest_period_id": str(one),
        "status": "open",
    }


def test_h1_prepare_stays_free_of_any_order(ctx):
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    one = period_of(ctx, ctx.tutor, 0, 7)
    two = period_of(ctx, ctx.tutor, 7, 7)

    prepare_all(ctx, who, two, one)

    assert status_of(ctx.url, one) == status_of(ctx.url, two) == "prepared"


def test_h1_another_tutors_periods_dont_count(ctx):
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    other = new_tutor_user(ctx.url, qualified_for=[ctx.subject])
    add_terms(ctx.url, other.id)
    period_of(ctx, other, 0, 7)
    mine = period_of(ctx, ctx.tutor, 7, 7, first=True)
    prepare_all(ctx, who, mine)

    assert code(final(ctx, who, mine)) == OK


def test_e2_an_empty_earlier_period_counts_and_goes_through_at_zero(ctx):
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id)
    empty = period_of(ctx, ctx.tutor, 0, 7)
    two = period_of(ctx, ctx.tutor, 7, 7)
    prepare_all(ctx, who, two)

    assert code(final(ctx, who, two)) == OUT_OF_ORDER

    lock_at(ctx.client, ctx.url, who, later(ctx), empty)
    assert code(final(ctx, who, two)) == OK
    assert row_of(ctx.url, empty).total_amount == Decimal("0.00")


# --- H2, H3: both directions of N -----


def test_h2_n_growing_oldest_first_pays_1000_not_833_33(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 3)  # Monday, Wednesday, Friday: N = 3
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    one = period_of(ctx, ctx.tutor, 0, 2)
    two = period_of(ctx, ctx.tutor, 2, 2)
    three = period_of(ctx, ctx.tutor, 4, 7)
    rest = period_of(ctx, ctx.tutor, 11, 70)
    teach(ctx, cohort, 0, 1, 2)
    prepare_all(ctx, who, one, two, three, rest)

    assert code(final(ctx, who, three)) == OUT_OF_ORDER
    assert code(final(ctx, who, one)) == OK
    assert code(final(ctx, who, two)) == OK

    grow(ctx, cohort, 2)  # N = 4
    stale = final(ctx, who, three)
    assert code(stale) == STALE
    assert stale.json()["details"] == {"added": 0, "removed": 0, "changed": 1}
    with frozen_clock(later(ctx)):
        assert code(return_period(ctx.client, who.finance, three)) == OK
    closed_out(ctx, rows_of(ctx, cohort.id)[3], minutes=60)
    with frozen_clock(later(ctx) + timedelta(seconds=1)):
        assert code(return_period(ctx.client, who.finance, rest)) == OK
        locked(ctx.client, ctx.url, who.finance, who.owner, three)
        locked(ctx.client, ctx.url, who.finance, who.owner, rest)

    assert amounts(ctx.url, one) == cents("333.33")
    assert amounts(ctx.url, two) == cents("333.34")
    assert shares(ctx.url, three) == [(3, 4)]
    assert amounts(ctx.url, three) == cents("83.33")
    assert amounts(ctx.url, rest) == cents("250.00")
    assert total(ctx, one, two, three, rest) == Decimal("1000.00")


def test_h3_n_shrinking_oldest_first_pays_1000_not_1166_67(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 4)  # N = 4
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    one = period_of(ctx, ctx.tutor, 0, 2)
    two = period_of(ctx, ctx.tutor, 2, 2)
    rest = period_of(ctx, ctx.tutor, 4, 70)
    teach(ctx, cohort, 0, 1)
    prepare_all(ctx, who, one, two)

    assert code(final(ctx, who, two)) == OUT_OF_ORDER
    assert code(final(ctx, who, one)) == OK
    assert code(final(ctx, who, two)) == OK

    answered = cancel(ctx, cohort.id_of(2), cohort.session(2)["version"])
    assert code_of(answered) == OK, answered.text  # N = 3
    teach(ctx, cohort, 3)
    lock_at(ctx.client, ctx.url, who, later(ctx), rest)

    assert amounts(ctx.url, one) == cents("250.00")
    assert amounts(ctx.url, two) == cents("250.00")
    assert shares(ctx.url, rest) == [(3, 3)]
    assert amounts(ctx.url, rest) == cents("500.00")
    assert total(ctx, one, two, rest) == Decimal("1000.00")


# --- H4-H7: #442's finalized states -----


def test_h4_n1_a_floored_line_isnt_paid_again(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 2)  # Monday, Wednesday; N = 2
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    one = period_of(ctx, ctx.tutor, 0, 2)
    two = period_of(ctx, ctx.tutor, 2, 3)
    rest = period_of(ctx, ctx.tutor, 5, 60)
    teach(ctx, cohort, 0)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    assert amounts(ctx.url, one) == cents("500.00")

    grow(ctx, cohort, 2, 3, 4, 5)  # N = 6
    teach(ctx, cohort, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), two)
    assert amounts(ctx.url, two) == cents("0.00")
    assert shares(ctx.url, two) == [(2, 6)]

    for session in rows_of(ctx, cohort.id)[2:]:
        closed_out(ctx, session, minutes=60)
    lock_at(ctx.client, ctx.url, who, later(ctx), rest)

    assert amounts(ctx.url, rest) == cents("0.00", "166.67", "166.66", "166.67")
    assert shares(ctx.url, rest) == [(3, 6), (4, 6), (5, 6), (6, 6)]
    assert total(ctx, one, two, rest) == Decimal("1000.00")


def test_h5_d1_work_before_the_first_period_counts_once(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 3)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    middle = period_of(ctx, ctx.tutor, 1, 3, first=True)  # the first Wednesday
    last = period_of(ctx, ctx.tutor, 4, 60)
    teach(ctx, cohort, 0, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), middle)
    assert shares(ctx.url, middle) == [(2, 3)]
    teach(ctx, cohort, 2)

    lock_at(ctx.client, ctx.url, who, later(ctx), last)

    assert shares(ctx.url, last) == [(3, 3)]
    assert amounts(ctx.url, last) == cents("333.33")


def test_h6_d8_a_no_show_below_100_is_capped_at_the_sessions_line(ctx):
    who = team(ctx)
    add_rule(ctx.url, "no_show", "2025-01-01", "percent", percent="50")
    cohort = make_cohort(ctx, 2)  # N = 2
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    one = period_of(ctx, ctx.tutor, 0, 2)
    two = period_of(ctx, ctx.tutor, 2, 3)
    rest = period_of(ctx, ctx.tutor, 5, 60)
    teach(ctx, cohort, 0)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    grow(ctx, cohort, 2, 3)  # N = 4
    closed_out(ctx, cohort.session(1), status="absent", minutes=60)
    lock_at(ctx.client, ctx.url, who, later(ctx), two)
    [no_show] = lines(ctx.url, two)
    # 2 of 4 is 500.00, already paid. Uncapped: 50% of 250.00 = 125.00.
    assert (no_show.kind, no_show.amount) == ("no_show", Decimal("0.00"))

    for session in rows_of(ctx, cohort.id)[2:]:
        closed_out(ctx, session, minutes=60)
    lock_at(ctx.client, ctx.url, who, later(ctx), rest)

    assert amounts(ctx.url, rest) == cents("250.00", "250.00")
    assert total(ctx, one, two, rest) == Decimal("1000.00")


@pytest.mark.parametrize(
    "rule", [{"mode": "percent", "percent": "250"}, {"mode": "fixed", "amount": "625"}]
)
def test_h6_d8_above_100_and_fixed_stay_uncapped(ctx, rule):
    """N = 4 at 1000: three lines of 250.00, and a no-show paying 625.00 by the
    rule, so 1375.00 on 1000.00. That is what the rule says, not a double pay."""
    who = team(ctx)
    add_rule(ctx.url, "no_show", "2025-01-01", **rule)
    cohort = make_cohort(ctx, 4)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    period = period_of(ctx, ctx.tutor, 0, 70)
    teach(ctx, cohort, 0)
    closed_out(ctx, cohort.session(1), status="absent", minutes=60)
    teach(ctx, cohort, 2, 3)

    lock_at(ctx.client, ctx.url, who, later(ctx), period)

    assert amounts(ctx.url, period) == cents("250.00", "625.00", "250.00", "250.00")
    assert total(ctx, period) == Decimal("1375.00")


def test_h7_approved_inside_a_finalized_period_isnt_walked(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0)
    lock_at(ctx.client, ctx.url, who, later(ctx), first)
    teach(ctx, cohort, 1)  # same week, its period is finalized: for #111
    teach(ctx, cohort, 2)

    lock_at(ctx.client, ctx.url, who, later(ctx), second)

    assert amounts(ctx.url, first) == cents("333.33")
    # k skips the late session: 666.67 - 333.33.
    assert shares(ctx.url, second) == [(2, 3)]
    assert amounts(ctx.url, second) == cents("333.34")


# --- H8, J7: drift with a cohort -----


def test_h8_finalizing_the_first_period_raises_no_false_alarm_on_the_second(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1, 2)
    prepare_all(ctx, who, first, second)

    assert code(final(ctx, who, first)) == OK
    assert code(final(ctx, who, second)) == OK

    assert total(ctx, first, second) == Decimal("1000.00")


def test_h8_j7_with_n_changed_the_second_period_is_stale(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1, 2)
    prepare_all(ctx, who, first, second)
    assert code(final(ctx, who, first)) == OK

    grow(ctx, cohort, 3)

    assert code(final(ctx, who, second)) == STALE
    assert status_of(ctx.url, second) == "prepared"
    with frozen_clock(later(ctx)):
        assert code(return_period(ctx.client, who.finance, second)) == OK


def test_j7_n_changing_before_the_first_finalize_is_stale_on_it_too(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 3)
    first, _ = two_periods(ctx)
    teach(ctx, cohort, 0, 1)
    prepare_all(ctx, who, first)

    grow(ctx, cohort, 3)

    assert code(final(ctx, who, first)) == STALE


# --- H9: a run overlapping a real finalize -----


def test_h9_a_run_overlapping_a_real_finalize_counts_each_session_once(
    ctx, monkeypatch
):
    """P2's run has read the finalized periods, and P1 is still ``prepared`` then.
    P1's finalize commits on its own connection before the run reads the finalized
    lines. This is the real thing the #442 "between the reads" test simulated."""
    who = team(ctx)
    url = ctx.url
    cohort = make_cohort(ctx, 3)  # Monday, Wednesday, Friday: N = 3
    add_terms(url, ctx.tutor.id)
    add_rate(url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    one = period_of(ctx, ctx.tutor, 0, 3)
    two = period_of(ctx, ctx.tutor, 3, 60)
    teach(ctx, cohort, 0, 1, 2)
    prepare_all(ctx, who, one)
    review = datetime.fromisoformat(prepared_at(url, one) or "")
    principal = principal_of(who.owner.user.id, OWNER)
    real = payroll.finalized_course_lines

    async def _overlap() -> None:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        reached, resume = asyncio.Event(), asyncio.Event()

        async def paused(*args: Any, **kwargs: Any) -> Any:
            if not reached.is_set():
                reached.set()
                await asyncio.wait_for(resume.wait(), WAIT_LIMIT)
            return await real(*args, **kwargs)

        monkeypatch.setattr(payroll, "finalized_course_lines", paused)

        async def run_second() -> None:
            async with UnitOfWork(sessions) as uow:
                await payroll_calculation.recalculate(uow.session, two)

        try:
            running = asyncio.create_task(run_second())
            await asyncio.wait_for(reached.wait(), WAIT_LIMIT)
            async with UnitOfWork(sessions) as uow:
                await payroll_workflow.finalize(uow.session, principal, one, review)
            resume.set()
            await asyncio.wait_for(running, WAIT_LIMIT)
        finally:
            resume.set()
            await engine.dispose()

    with frozen_clock(later(ctx)):
        asyncio.run(_overlap())
    monkeypatch.setattr(payroll, "finalized_course_lines", real)

    assert status_of(url, one) == "finalized"
    assert shares(url, two) == [(3, 3)]
    assert amounts(url, two) == cents("333.33")
    lock_at(ctx.client, url, who, later(ctx), two)
    assert total(ctx, one, two) == Decimal("1000.00")


# --- J8: no false alarm, the event kinds -----


def test_j8_a_no_show_and_a_per_course_line(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 2)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    period = period_of(ctx, ctx.tutor, 0, 70)
    teach(ctx, cohort, 0)
    closed_out(ctx, cohort.session(1), status="absent", minutes=60)

    lock_at(ctx.client, ctx.url, who, later(ctx), period)

    assert [line.kind for line in lines(ctx.url, period)] == ["session", "no_show"]


def test_j8_a_late_cancellation(scene):
    add_rule(
        scene.url,
        "late_cancellation",
        scene.ctx.tl.monday.isoformat(),
        "percent",
        percent="50",
    )
    who = team(scene.ctx)
    period = hourly_payroll(scene)
    cancelled_by_family(scene)

    lock_at(scene.ctx.client, scene.url, who, later(scene.ctx), period)

    assert [line.kind for line in lines(scene.url, period)] == ["late_cancellation"]


def test_j8_a_makeup(scene):
    who = team(scene.ctx)
    period = hourly_payroll(scene)
    target = cancelled_target(scene)
    made = booked(book(scene, target["id"]))
    answered = cancel(scene.ctx, made["id"], made["version"])
    assert code_of(answered) == OK, answered.text
    makeup = makeup_of(scene, made["id"], 2, 3)
    closed_out(scene.ctx, makeup, minutes=60)

    lock_at(scene.ctx.client, scene.url, who, later(scene.ctx), period)

    assert [line.kind for line in lines(scene.url, period)] == ["makeup"]


def test_j8_a_recalculation_after_prepare_is_refused_so_drift_is_all_there_is(ctx):
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    period = add_period_row(
        ctx.url,
        ctx.tutor.id,
        ctx.tl.monday.isoformat(),
        (ctx.tl.monday + timedelta(days=6)).isoformat(),
    )
    prepare_all(ctx, who, period)

    with frozen_clock(later(ctx)):
        response = recalculate(ctx.client, who.finance, period)

    assert code(response) == (409, "payroll_period_not_open")
