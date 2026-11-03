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
from tests.integration.catalog_support import get, post
from tests.integration.change_request_support import Scene, make_scene
from tests.integration.conftest import run_sql, short_lived_engine
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
from tests.integration.payroll_period_support import set_floor
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
    day,
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


def unresolved(ctx: Ctx, who: Team) -> list[Any]:
    with frozen_clock(later(ctx)):
        found: list[Any] = get(
            ctx.client,
            who.finance.cookie,
            "/api/v1/payroll/unresolved-work",
            tutor_user_id=str(ctx.tutor.id),
        ).json()["items"]
    return found


def stable_rates(ctx: Ctx) -> list[Any]:
    return [
        (row.effective_from, row.rate)
        for row in run_sql(
            ctx.url,
            "SELECT effective_from, rate FROM compensation_rates"
            " WHERE tutor_user_id = :u ORDER BY effective_from",
            {"u": ctx.tutor.id},
        )
    ]


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


# --- E5, QA D1: a course version is refused only if it re-prices a line -----


def add_version(
    ctx: Ctx,
    who: Team,
    on: str,
    rate: str | None = "700",
    *,
    model: str | None = "per_course",
) -> Any:
    with frozen_clock(later(ctx)):
        return post(
            ctx.client,
            who.owner.cookie,
            f"/api/v1/payroll/tutors/{ctx.tutor.id}/rates",
            {
                "class_kind": "course",
                "effective_from": on,
                "rate_model": None if rate is None else model,
                "rate": rate,
            },
        )


def cohort_at(ctx: Ctx, offset: int, sessions: int = 2) -> Any:
    """Mondays and Wednesdays from ``offset`` days after the first Monday."""
    return make_cohort(
        ctx, sessions, starts_on=ctx.tl.monday + timedelta(days=offset), weekdays=[1, 3]
    )


def finalized_cohort_b(ctx: Ctx, who: Team) -> None:
    """QA D1's scene. The only version starts on day 14, cohort B on day 21. Period
    one, days 0 to 13, is finalized empty, and period two pays B."""
    set_floor(ctx.url, ctx.tl.monday.isoformat())
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 14), "1000", kind="course", model="per_course"
    )
    one = period_of(ctx, ctx.tutor, 0, 14)
    two = period_of(ctx, ctx.tutor, 14, 14)
    cohort = cohort_at(ctx, 21)
    teach(ctx, cohort, 0, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), one, two)
    assert amounts(ctx.url, two) == cents("500.00", "500.00")


@pytest.fixture
def no_floor_after(module_seeded_url: str) -> Iterator[None]:
    yield
    set_floor(module_seeded_url, None)


def test_d1_a_no_rate_course_item_gets_its_rate_and_the_tutor_goes_on(
    ctx, no_floor_after
):
    """The exit, through real routes: work approved late in a finalized period, no
    version on its first date, a back-dated version, and the next period
    finalizes."""
    who = team(ctx)
    finalized_cohort_b(ctx, who)
    three = period_of(ctx, ctx.tutor, 28, 30)
    late = cohort_at(ctx, 0)
    teach(ctx, late, 0, 1)
    assert len(unresolved(ctx, who)) == 2

    with frozen_clock(later(ctx)):
        refused = prepare(ctx.client, who.finance, three)
    assert code(refused) == (409, "payroll_unresolved_work")
    assert refused.json()["details"]["count"] == 2

    created = add_version(ctx, who, day(ctx, 0))

    assert created.status_code == 201, created.text
    assert unresolved(ctx, who) == []
    lock_at(ctx.client, ctx.url, who, later(ctx), three)
    assert status_of(ctx.url, three) == "finalized"


@pytest.mark.parametrize("rate", ["300", "9000", None])
def test_d1_a_version_that_would_reprice_a_finalized_cohort_is_refused(
    ctx, no_floor_after, rate
):
    """Between the day-14 version and B's first date, lower, higher or a stop."""
    who = team(ctx)
    finalized_cohort_b(ctx, who)
    before = stable_rates(ctx)

    response = add_version(ctx, who, day(ctx, 16), rate)

    assert code(response) == (422, "effective_from_under_finalized_line")
    assert response.json()["details"] == {"latest_finalized_on": day(ctx, 21)}
    assert stable_rates(ctx) == before


def test_d1_the_first_date_itself_is_taken_and_the_day_after_isnt(ctx, no_floor_after):
    """QA ``s7``: B's lines run to day 23, and from day 22 a version can't re-price
    B, so a new cohort can get its rate from a true date."""
    who = team(ctx)
    finalized_cohort_b(ctx, who)

    assert code(add_version(ctx, who, day(ctx, 21))) == (
        422,
        "effective_from_under_finalized_line",
    )
    created = add_version(ctx, who, day(ctx, 22))
    assert created.status_code == 201, created.text


def test_d1_a_first_date_on_the_next_versions_date_isnt_taken_over(ctx, no_floor_after):
    """B's first date resolves to the version of that very date."""
    who = team(ctx)
    set_floor(ctx.url, ctx.tl.monday.isoformat())
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 21), "1000", kind="course", model="per_course"
    )
    period = period_of(ctx, ctx.tutor, 0, 28)
    cohort = cohort_at(ctx, 21)
    teach(ctx, cohort, 0, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), period)

    created = add_version(ctx, who, day(ctx, 14))

    assert created.status_code == 201, created.text


def test_d1_the_window_is_on_the_first_date_not_on_the_lines_own_date(
    ctx, no_floor_after
):
    """Kills "E5 course bounded". The cohort starts Monday and the next version
    is on Tuesday, so the only finalized line, Wednesday's, is past the window by
    its own date, and still priced on Monday, by the version being taken over."""
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 1), "5000", kind="course", model="per_course"
    )
    cohort = make_cohort(ctx, 2)  # Monday and Wednesday; N = 2
    period = period_of(ctx, ctx.tutor, 2, 7, first=True)
    teach(ctx, cohort, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), period)
    [line] = lines(ctx.url, period)
    assert line.service_date.isoformat() == day(ctx, 2)
    assert line.rate == Decimal("1000.0000")

    response = add_version(ctx, who, day(ctx, 0))

    assert code(response) == (422, "effective_from_under_finalized_line")
    assert response.json()["details"] == {"latest_finalized_on": day(ctx, 0)}


def test_l3_an_hourly_course_line_refuses_a_per_course_version(ctx):
    """#442 D6: a per-course version on the first date would re-price every line
    of the cohort, though a later version covers the line's own date."""
    who = team(ctx)
    cohort = make_cohort(ctx, 2)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="100", kind="course", model="per_hour")
    period = period_of(ctx, ctx.tutor, 0, 7)
    teach(ctx, cohort, 0, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), period)

    assert code(add_version(ctx, who, day(ctx, 0))) == (
        422,
        "effective_from_under_finalized_line",
    )


def test_l3_an_hourly_course_line_is_taken_over_on_its_own_date_too(ctx):
    who = team(ctx)
    cohort = make_cohort(ctx, 2)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="100", kind="course", model="per_hour")
    period = period_of(ctx, ctx.tutor, 0, 7)
    teach(ctx, cohort, 0, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), period)

    response = add_version(ctx, who, day(ctx, 1), "150", model="per_hour")

    assert code(response) == (422, "effective_from_under_finalized_line")
    assert response.json()["details"] == {"latest_finalized_on": day(ctx, 2)}


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
