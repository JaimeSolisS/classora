"""Per-course work after lock through real cohorts and routes (M7-08d A1-A9;
ADR-0017 E3, E11; #442 C).

Each test takes its own decade. Terms, the first rate and periods are set by SQL,
as in every payroll test. The cohorts, closeouts, revisions, cancellations, the
job (``run_post_adjustments``), ``prepare``, ``return`` and ``finalize`` are real.
Every expected amount is worked by hand from ``rate x k / N``, half away from zero
to the centavo. A cohort of four at 1000.00 runs Mondays and Wednesdays: days 0, 2,
7 and 9.

**Not reachable through routes, and covered in
``tests/unit/test_payroll_course_anchors.py``:** a late per-course makeup (a course
session is never a makeup target, #442 D3) and an anchor whose cohort later
loses sessions below its ``k`` (an approved session stays in ``N``, so ``k`` never
passes it).
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

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.identity.permissions import OWNER
from app.modules.payroll import service as payroll
from app.workflows import payroll_adjustments, payroll_calculation, payroll_workflow
from app.workflows.payroll_adjustments import Candidate, PostAdjustmentsRunResult
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.package_support import closeout_version
from tests.integration.payroll_calculation_support import (
    add_rate,
    add_rule,
    add_terms,
    clear,
    closed_out,
    lines,
)
from tests.integration.payroll_manual_support import CREATED, manual
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    STALE,
    Team,
    code,
    finalize,
    lock_at,
    locked,
    prepare,
    prepared_at,
    return_period,
    row_of,
    status_of,
)
from tests.integration.series_edit_support import Ctx, make_ctx
from tests.integration.test_attendance import correct, principal_of
from tests.integration.test_attendance import version_of as session_version
from tests.integration.test_closeout_review import correct_closeout
from tests.integration.test_payroll_calculation_concurrency import in_turns
from tests.integration.test_payroll_per_course import (
    Cohort,
    day,
    grow,
    make_cohort,
    period_of,
    rows_of,
    teach,
)
from tests.integration.test_payroll_workflow_course import later, team, unresolved
from tests.integration.test_session_cancellation import cancel

WAIT_LIMIT = 15.0
RATE = Decimal("1000.00")
NEVER = {Decimal("833.33"), Decimal("1166.67")}
"""#442's figures for a per-course cohort counted at the wrong ``k``."""


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


# --- Helpers -----


def run_job(url: str) -> PostAdjustmentsRunResult:
    return asyncio.run(payroll_adjustments.run_post_adjustments(settings_for(url)))


def adjustments(ctx: Ctx) -> list[Any]:
    return run_sql(
        ctx.url,
        "SELECT * FROM payroll_adjustments WHERE tutor_user_id = :t"
        " ORDER BY created_at, id",
        {"t": ctx.tutor.id},
    )


def anchor_of(ctx: Ctx, session_id: str) -> Any:
    [row] = [a for a in adjustments(ctx) if str(a.class_session_id) == session_id]
    return row


def on_course_payroll(ctx: Ctx, rate: str = "1000") -> None:
    set_floor(ctx.url, ctx.tl.monday.isoformat())
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate=rate, kind="course", model="per_course")


def four_periods(ctx: Ctx) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Days 0-1, 2-3, 4-8, and 9 on: one session of the cohort in each."""
    return (
        period_of(ctx, ctx.tutor, 0, 2),
        period_of(ctx, ctx.tutor, 2, 2),
        period_of(ctx, ctx.tutor, 4, 5),
        period_of(ctx, ctx.tutor, 9, 70),
    )


def cohort_amounts(ctx: Ctx, cohort: Cohort) -> list[Decimal]:
    """Every line and adjustment paid on the cohort's sessions."""
    rows = run_sql(
        ctx.url,
        "SELECT amount FROM payroll_lines AS l JOIN class_sessions AS s"
        " ON s.id = l.class_session_id WHERE l.tutor_user_id = :t"
        " AND s.cohort_id = :c"
        " UNION ALL SELECT amount FROM payroll_adjustments AS a JOIN class_sessions"
        " AS s ON s.id = a.class_session_id WHERE a.tutor_user_id = :t"
        " AND s.cohort_id = :c",
        {"t": ctx.tutor.id, "c": cohort.id},
    )
    return [row.amount for row in rows]


def periods_total(ctx: Ctx, *periods: uuid.UUID) -> Decimal:
    return sum(
        (row_of(ctx.url, period).total_amount for period in periods), Decimal("0.00")
    )


def k_n(row: Any) -> tuple[int, int]:
    return row.cumulative_sessions, row.cohort_sessions


def owner_cookie(ctx: Ctx) -> Any:
    return sign_in(ctx.client, ctx.owner.username)


def revise(ctx: Ctx, session_id: str, status: str, **closeout: Any) -> None:
    """A staff correction of the attendance (when ``status`` is given), then a
    new approval revision, as #111 C7 does for a lesson."""
    with frozen_clock(later(ctx)):
        cookie = owner_cookie(ctx)
        if status:
            changed = correct(
                ctx.client,
                cookie,
                session_id,
                session_version(ctx.url, session_id),
                ctx.student,
                status,
            )
            assert code_of(changed) == (201, None), changed.text
        response = correct_closeout(
            ctx.client,
            cookie,
            session_id,
            closeout_version(ctx.url, uuid.UUID(session_id)),
            reason="Asistencia corregida",
            **closeout,
        )
        assert code_of(response) == OK, response.text


def shrink(ctx: Ctx, cohort: Cohort, index: int) -> None:
    session = rows_of(ctx, cohort.id)[index]
    answered = cancel(ctx, session["id"], session["version"])
    assert code_of(answered) == OK, answered.text


# --- A1, A4: a late session of a cohort of four, in every order -----

ORDERS = ("before", "after_one", "prepared")
CHANGES = ("same", "grow", "shrink")


def change_n(ctx: Ctx, cohort: Cohort, change: str) -> None:
    if change == "grow":
        grow(ctx, cohort, 3)  # day 18: N = 5
    elif change == "shrink":
        shrink(ctx, cohort, 2)  # day 7, not taught: N = 3


def teach_the_rest(ctx: Ctx, cohort: Cohort, *indexes: int) -> None:
    """Close out these sessions, by date, the grown one included, unless
    cancelled."""
    for index, row in enumerate(rows_of(ctx, cohort.id)):
        if index in indexes and row["state"] != "cancelled":
            closed_out(ctx, row, minutes=60)


N_AFTER = {"same": 4, "grow": 5, "shrink": 3}


@pytest.mark.parametrize("change", CHANGES)
@pytest.mark.parametrize("order", ORDERS)
def test_a4_a_late_first_session_and_the_cohort_still_sums_to_its_rate(
    ctx, order, change
):
    """Session 1's period is finalized empty, session 1 is approved late, and
    sessions 2-4 fall in later periods. The anchor is posted before any later
    period is finalized, after one is, or while one is ``prepared``; then ``N``
    stays, grows to 5 or shrinks to 3."""
    who = team(ctx)
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 4)
    one, two, three, rest = four_periods(ctx)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    expected_k = 1
    if order == "after_one":
        teach(ctx, cohort, 1)
        lock_at(ctx.client, ctx.url, who, later(ctx), two)
        expected_k = 2
    if order == "prepared":
        teach(ctx, cohort, 1)
        with frozen_clock(later(ctx)):
            assert code(prepare(ctx.client, who.finance, two)) == OK

    teach(ctx, cohort, 0)  # late: its period is finalized
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)

    anchor = anchor_of(ctx, cohort.id_of(0))
    assert (anchor.kind, anchor.line_kind) == ("late_approval", "session")
    assert k_n(anchor) == (expected_k, 4)
    assert (anchor.rate_model, anchor.rate) == ("per_course", Decimal("1000.0000"))
    assert anchor.amount == Decimal("250.00")
    assert anchor.target_period_id == (three if order != "before" else two)
    change_n(ctx, cohort, change)
    if order == "prepared":
        stale = locked_response(ctx, who, two)
        assert code(stale) == STALE, stale.text
        with frozen_clock(later(ctx) + timedelta(seconds=1)):
            assert code(return_period(ctx.client, who.finance, two)) == OK
            locked(ctx.client, ctx.url, who.finance, who.owner, two)
        # Re-prepared, session 2 walks after the anchor.
        assert [k_n(line) for line in lines(ctx.url, two)] == [(2, N_AFTER[change])]
    teach_the_rest(ctx, cohort, *(range(1, 5) if order == "before" else range(2, 5)))
    for period in (two, three, rest):
        if status_of(ctx.url, period) != "finalized":
            lock_at(ctx.client, ctx.url, who, later(ctx) + timedelta(seconds=2), period)

    paid = cohort_amounts(ctx, cohort)
    assert sum(paid, Decimal("0.00")) == RATE
    assert periods_total(ctx, one, two, three, rest) == RATE
    assert not NEVER & set(paid)
    assert len(adjustments(ctx)) == 1


def locked_response(ctx: Ctx, who: Team, period: uuid.UUID) -> Any:
    """``finalize`` by route, for its refusal."""
    with frozen_clock(later(ctx)):
        return finalize(ctx.client, ctx.url, who.owner, period)


def test_a4_the_figures_by_hand_when_n_grows_after_the_anchor(ctx):
    """Anchor 1 of 4 is 250.00; then N = 5: 400.00 - 250.00, then 200.00 each."""
    who = team(ctx)
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 4)
    one, two, three, rest = four_periods(ctx)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 0)
    run_job(ctx.url)
    grow(ctx, cohort, 3)
    for row in rows_of(ctx, cohort.id)[1:]:
        closed_out(ctx, row, minutes=60)
    lock_at(ctx.client, ctx.url, who, later(ctx), two, three, rest)

    assert [(k_n(line), line.amount) for line in lines(ctx.url, two)] == [
        ((2, 5), Decimal("150.00")),
        ((None, None), Decimal("250.00")),
    ]
    assert [line.amount for line in lines(ctx.url, three)] == [Decimal("200.00")]
    assert [line.amount for line in lines(ctx.url, rest)] == [
        Decimal("200.00"),
        Decimal("200.00"),
    ]


def test_a4_the_figures_by_hand_when_n_shrinks_after_the_anchor(ctx):
    """Anchor 1 of 4 is 250.00; then N = 3: 666.67 - 250.00, then 333.33."""
    who = team(ctx)
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 4)
    one, two, three, rest = four_periods(ctx)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 0)
    run_job(ctx.url)
    shrink(ctx, cohort, 2)
    teach(ctx, cohort, 1, 3)
    lock_at(ctx.client, ctx.url, who, later(ctx), two, three, rest)

    session_lines = [line for line in lines(ctx.url, two) if line.kind != "adjustment"]
    assert [(k_n(line), line.amount) for line in session_lines] == [
        ((2, 3), Decimal("416.67"))
    ]
    assert [(k_n(line), line.amount) for line in lines(ctx.url, rest)] == [
        ((3, 3), Decimal("333.33"))
    ]


# --- A2, A5: the anchor's kind and amount -----


@pytest.mark.parametrize(("percent", "amount"), [("50", "125.00"), ("100", "250.00")])
def test_a5_a_late_no_show_is_anchored_at_its_rule(ctx, percent, amount):
    who = team(ctx)
    on_course_payroll(ctx)
    add_rule(ctx.url, "no_show", ctx.tl.monday.isoformat(), "percent", percent=percent)
    cohort = make_cohort(ctx, 4)
    one, two, three, rest = four_periods(ctx)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    closed_out(ctx, cohort.session(0), status="absent", minutes=60)

    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)

    anchor = anchor_of(ctx, cohort.id_of(0))
    assert (anchor.line_kind, anchor.amount, k_n(anchor)) == (
        "no_show",
        Decimal(amount),
        (1, 4),
    )
    assert anchor.session_pay_rule_id is not None
    teach(ctx, cohort, 1, 2, 3)
    lock_at(ctx.client, ctx.url, who, later(ctx), two, three, rest)
    # The walk goes on from 1 of 4 whatever the no-show paid.
    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == RATE - Decimal(
        "250.00"
    ) + Decimal(amount)


def test_a2_a_floored_anchor_writes_nothing_and_is_read_again(ctx):
    """1 of 2 finalized (500.00), then N = 4: the late session 2 alone is 2 of 4,
    500.00, already paid. Nothing is written until the cohort moves on."""
    who = team(ctx)
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 2)  # days 0, 2
    one = period_of(ctx, ctx.tutor, 0, 4)
    rest = period_of(ctx, ctx.tutor, 4, 70)
    teach(ctx, cohort, 0)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    grow(ctx, cohort, 2, 3)  # N = 4
    teach(ctx, cohort, 1)  # late, inside the finalized period

    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1)
    assert adjustments(ctx) == []
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1)

    for row in rows_of(ctx, cohort.id)[2:]:
        closed_out(ctx, row, minutes=60)
    lock_at(ctx.client, ctx.url, who, later(ctx), rest)
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    assert k_n(anchor_of(ctx, cohort.id_of(1))) == (4, 4)
    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == RATE


def test_d1_every_remaining_session_late_still_reaches_the_rate(ctx):
    """1 of 2 finalized (500.00), then N = 4 and the three other sessions all
    approved late, inside the finalized period. Each alone would be 2 of 4,
    500.00, already paid, and never written (D1): the late sessions dated before
    one count in its ``k``, so the cohort reaches 1000.00. What is left floors at
    0.00 for good and stays a candidate."""
    who = team(ctx)
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 2)  # days 0, 2
    one = period_of(ctx, ctx.tutor, 0, 21)
    period_of(ctx, ctx.tutor, 21, 70)
    teach(ctx, cohort, 0)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    grow(ctx, cohort, 2, 3)  # days 11 and 18, inside the finalized period
    for row in rows_of(ctx, cohort.id)[1:]:
        closed_out(ctx, row, minutes=60)

    run_job(ctx.url)
    run_job(ctx.url)

    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == RATE
    assert all(row.cumulative_sessions <= 4 for row in adjustments(ctx))
    again = run_job(ctx.url)  # one or two are left, by the run's order
    assert (again.posted, again.failed) == (0, 0) and again.candidates in (1, 2)


# --- A6: revisions of a finalized per-course line -----


def test_a6_a_session_revised_to_all_absent_and_back(ctx):
    who = team(ctx)
    on_course_payroll(ctx)
    add_rule(ctx.url, "no_show", ctx.tl.monday.isoformat(), "percent", percent="50")
    cohort = make_cohort(ctx, 4)
    one, two, three, rest = four_periods(ctx)
    teach(ctx, cohort, 0)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    [line] = lines(ctx.url, one)
    assert (k_n(line), line.amount) == ((1, 4), Decimal("250.00"))
    paid = [dict(row._mapping) for row in lines(ctx.url, one)]

    revise(ctx, cohort.id_of(0), "absent")
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    [no_show] = adjustments(ctx)
    assert (no_show.kind, no_show.line_kind) == ("hours_correction", "no_show")
    # 50 % of 1 of 4: 125.00, against 250.00 paid.
    assert (no_show.amount, k_n(no_show)) == (Decimal("-125.00"), (1, 4))
    assert no_show.origin_line_id == line.id

    revise(ctx, cohort.id_of(0), "present")
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    back = adjustments(ctx)[-1]
    assert (back.line_kind, back.amount, k_n(back)) == (
        "session",
        Decimal("125.00"),
        (1, 4),
    )

    revise(ctx, cohort.id_of(0), "", actual_minutes=30)  # minutes only
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1)
    assert len(adjustments(ctx)) == 2

    teach(ctx, cohort, 1, 2, 3)
    lock_at(ctx.client, ctx.url, who, later(ctx), two, three, rest)
    assert [dict(row._mapping) for row in lines(ctx.url, one)] == paid
    # k and N never moved: 250.00 - 125.00 + 125.00, then 250.00 each.
    assert [k_n(row) for row in lines(ctx.url, rest)] == [(4, 4)]
    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == RATE


def test_a6_an_anchor_revised_to_all_absent(ctx):
    """The origin is the anchor (its ``adjustment`` line), at its own ``k``."""
    who = team(ctx)
    on_course_payroll(ctx)
    add_rule(ctx.url, "no_show", ctx.tl.monday.isoformat(), "percent", percent="50")
    cohort = make_cohort(ctx, 4)
    one = period_of(ctx, ctx.tutor, 0, 4)  # sessions 1 and 2
    period_of(ctx, ctx.tutor, 4, 70)
    teach(ctx, cohort, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 0)
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    anchor = anchor_of(ctx, cohort.id_of(0))
    assert k_n(anchor) == (2, 4)

    revise(ctx, cohort.id_of(0), "absent")
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    correction = adjustments(ctx)[-1]
    # 50 % of 2 of 4 minus 50 % of 1 of 4: 125.00, against 250.00.
    assert (correction.kind, correction.amount, k_n(correction)) == (
        "hours_correction",
        Decimal("-125.00"),
        (2, 4),
    )


# --- A7: before the first period stays out -----


def test_a7_work_before_the_first_period_is_walked_and_never_anchored(ctx):
    who = team(ctx)
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 4)
    first = period_of(ctx, ctx.tutor, 2, 2, first=True)  # the first Wednesday
    rest = period_of(ctx, ctx.tutor, 4, 70)
    teach(ctx, cohort, 0)  # before the first period

    assert run_job(ctx.url) == PostAdjustmentsRunResult()
    teach(ctx, cohort, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), first)
    assert run_job(ctx.url) == PostAdjustmentsRunResult()

    assert adjustments(ctx) == []
    [line] = lines(ctx.url, first)
    assert (k_n(line), line.amount) == ((2, 4), Decimal("250.00"))
    teach(ctx, cohort, 2, 3)
    lock_at(ctx.client, ctx.url, who, later(ctx), rest)
    # 750.00 by lines; the first 250.00 is a one-time item's (#109 E4).
    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == Decimal("750.00")


# --- A8: two late sessions in one run or in two -----


def selection_order(monkeypatch: Any, session_ids: list[str]) -> None:
    """Make the job's selection read its candidates in this order (the query has
    none), so a test sees each order the heap can return (QA round 1 D1)."""
    real = payroll_adjustments._found

    async def found(*args: Any, **kwargs: Any) -> Any:
        items = await real(*args, **kwargs)
        rank = {session_id: index for index, session_id in enumerate(session_ids)}
        return sorted(
            items,
            key=lambda item: rank.get(str(item.candidate.class_session_id), len(rank)),
        )

    monkeypatch.setattr(payroll_adjustments, "_found", found)


ORDERS_READ = ("earlier_first", "later_first")


@pytest.mark.parametrize("read", ORDERS_READ)
@pytest.mark.parametrize("runs", [1, 2])
def test_a8_two_late_sessions_give_the_same_anchors_in_one_run_or_two(
    ctx, monkeypatch, runs, read
):
    """Whatever order the selection reads them in, the job anchors a cohort oldest
    first: session 1 at 1 of 4, session 2 at 2 of 4 (QA round 1 D1, O2)."""
    who = team(ctx)
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 4)
    one = period_of(ctx, ctx.tutor, 0, 4)  # sessions 1 and 2
    rest = period_of(ctx, ctx.tutor, 4, 70)
    ids = [cohort.id_of(0), cohort.id_of(1)]
    selection_order(monkeypatch, ids if read == "earlier_first" else ids[::-1])
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 0)
    if runs == 2:
        assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    teach(ctx, cohort, 1)
    result = run_job(ctx.url)

    assert result.posted == (2 if runs == 1 else 1)
    assert [(k_n(anchor_of(ctx, i)), anchor_of(ctx, i).amount) for i in ids] == [
        ((1, 4), Decimal("250.00")),
        ((2, 4), Decimal("250.00")),
    ]
    assert run_job(ctx.url) == PostAdjustmentsRunResult()
    teach(ctx, cohort, 2, 3)
    lock_at(ctx.client, ctx.url, who, later(ctx), rest)
    assert [k_n(row) for row in lines(ctx.url, rest) if row.kind != "adjustment"] == [
        (3, 4),
        (4, 4),
    ]
    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == RATE
    assert len(adjustments(ctx)) == 2


# --- QA round 1: two late sessions and a shrink (D1); a settled session (D2) -----


@pytest.mark.parametrize("read", ORDERS_READ)
def test_qa_d1_two_late_sessions_then_n_shrinks_still_reach_the_rate(
    ctx, monkeypatch, read
):
    """QA's ``q1``: a cohort of 4 (days 0, 2, 7, 9); period one (days 0-3)
    finalized empty; sessions 1 and 2 approved late, one run; session 4 (not
    taught) cancelled, so N = 3; session 3 taught. 250.00 + 250.00 + 500.00 in
    either read order, never 750.00."""
    who = team(ctx)
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 4)
    one = period_of(ctx, ctx.tutor, 0, 4)
    rest = period_of(ctx, ctx.tutor, 4, 70)
    ids = [cohort.id_of(0), cohort.id_of(1)]
    selection_order(monkeypatch, ids if read == "earlier_first" else ids[::-1])
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, *((1, 0) if read == "later_first" else (0, 1)))
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=2, posted=2)
    shrink(ctx, cohort, 3)
    teach(ctx, cohort, 2)
    lock_at(ctx.client, ctx.url, who, later(ctx), rest)
    run_job(ctx.url)

    assert [k_n(anchor_of(ctx, i)) for i in ids] == [(1, 4), (2, 4)]
    session_lines = [row for row in lines(ctx.url, rest) if row.kind != "adjustment"]
    assert [(k_n(row), row.amount) for row in session_lines] == [
        ((3, 3), Decimal("500.00"))
    ]
    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == RATE


@pytest.mark.parametrize("terms_back", [False, True])
def test_qa_d2_a_session_paid_by_hand_isnt_paid_again_by_an_anchor(ctx, terms_back):
    """QA's ``q3`` (D7): 900 over 3 (days 0, 7, 14). Session 1 has no paying
    terms, is approved late and paid 300.00 by hand (settled). Session 2 is
    approved late and anchored: it doesn't count session 1, so 1 of 3, 300.00, and
    session 3 is 2 of 3, 300.00: 900.00 with the manual, not 1200.00. With
    ``terms_back`` the stop goes after the hand payment (by SQL, as M7-08c B2), so
    only the settlement keeps session 1 out."""
    who = team(ctx)
    set_floor(ctx.url, ctx.tl.monday.isoformat())
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url, ctx.tutor.id, "2000-01-01", "900", model="per_course", kind="course"
    )
    cohort = make_cohort(ctx, 3, weekdays=[1])  # days 0, 7, 14
    stop = add_terms(ctx.url, ctx.tutor.id, day(ctx, 0), frequency=None)
    add_terms(ctx.url, ctx.tutor.id, day(ctx, 1))
    one = period_of(ctx, ctx.tutor, 0, 10)
    rest = period_of(ctx, ctx.tutor, 10, 70)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 0)
    assert [(i["class_session_id"], i["reason"]) for i in unresolved(ctx, who)] == [
        (cohort.id_of(0), "no_pay_terms")
    ]
    with frozen_clock(later(ctx)):
        paid = manual(
            ctx.client,
            who.owner,
            ctx.tutor.id,
            "300.00",
            class_session_id=cohort.id_of(0),
        )
    assert code(paid) == CREATED, paid.text
    if terms_back:
        run_sql(ctx.url, "DELETE FROM tutor_pay_terms WHERE id = :id", {"id": stop})
    teach(ctx, cohort, 1)
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    anchor = anchor_of(ctx, cohort.id_of(1))
    assert (anchor.kind, k_n(anchor), anchor.amount) == (
        "late_approval",
        (1, 3),
        Decimal("300.00"),
    )

    teach(ctx, cohort, 2)
    lock_at(ctx.client, ctx.url, who, later(ctx), rest)

    [last] = [row for row in lines(ctx.url, rest) if row.kind != "adjustment"]
    assert (k_n(last), last.amount) == ((2, 3), Decimal("300.00"))
    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == Decimal("900.00")


def test_qa_d2_a_session_paid_by_hand_before_two_late_ones(ctx):
    """D7 with two late sessions after the settled one, oldest first: 1 of 3 and
    2 of 3; the cohort's ``k`` stays one short, so lines and anchors pay 600.00 and
    the manual the third share."""
    who = team(ctx)
    set_floor(ctx.url, ctx.tl.monday.isoformat())
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url, ctx.tutor.id, "2000-01-01", "900", model="per_course", kind="course"
    )
    cohort = make_cohort(ctx, 3, weekdays=[1])  # days 0, 7, 14
    add_terms(ctx.url, ctx.tutor.id, day(ctx, 0), frequency=None)
    add_terms(ctx.url, ctx.tutor.id, day(ctx, 1))
    one = period_of(ctx, ctx.tutor, 0, 21)
    period_of(ctx, ctx.tutor, 21, 70)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 0)
    with frozen_clock(later(ctx)):
        paid = manual(
            ctx.client,
            who.owner,
            ctx.tutor.id,
            "300.00",
            class_session_id=cohort.id_of(0),
        )
    assert code(paid) == CREATED, paid.text
    teach(ctx, cohort, 2, 1)

    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=2, posted=2)

    assert [k_n(anchor_of(ctx, cohort.id_of(i))) for i in (1, 2)] == [(1, 3), (2, 3)]
    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == Decimal("900.00")


# --- A9: concurrency -----


def selected(ctx: Ctx) -> tuple[Any, str, Any, Candidate]:
    async def read(session: AsyncSession) -> Any:
        zone = await administration.get_center_time_zone(session)
        floor = await payroll.payroll_starts_on(session)
        today = await administration.center_today(session)
        selection = await payroll_adjustments.select_candidates(
            session, floor=floor, zone=zone
        )
        [candidate] = [c for c in selection.to_post if c.tutor_user_id == ctx.tutor.id]
        return floor, zone, today, candidate

    found: tuple[Any, str, Any, Candidate] = in_uow(ctx.url, read)
    return found


def posting(ctx: Ctx) -> Any:
    floor, zone, today, candidate = selected(ctx)
    assert candidate.course

    def call(session: AsyncSession) -> Any:
        return payroll_adjustments.post_one(
            session, candidate, floor=floor, zone=zone, today=today
        )

    return call


def recalculating(period: uuid.UUID) -> Any:
    def call(session: AsyncSession) -> Any:
        return payroll_calculation.recalculate(session, period)

    return call


def anchored_scene(ctx: Ctx, who: Team) -> tuple[Cohort, uuid.UUID, uuid.UUID]:
    """Session 2 finalized at 1 of 4 in period two; session 1 late; session 3 in
    the open period three."""
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 4)
    one, two, three, _ = four_periods(ctx)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), two)
    teach(ctx, cohort, 0, 2)
    return cohort, two, three


def test_a9_a_recalculation_waiting_for_the_anchor_sees_it(ctx):
    who = team(ctx)
    cohort, _, three = anchored_scene(ctx, who)

    posted, result = in_turns(ctx.url, posting(ctx), recalculating(three))

    assert posted == "posted"
    assert k_n(anchor_of(ctx, cohort.id_of(0))) == (2, 4)
    assert result.line_count == 2  # session 3 and the anchor's line
    [line] = [row for row in lines(ctx.url, three) if row.kind != "adjustment"]
    assert (k_n(line), line.amount) == ((3, 4), Decimal("250.00"))


def test_a9_an_anchor_waiting_for_a_recalculation_is_caught_by_drift(ctx):
    who = team(ctx)
    cohort, _, three = anchored_scene(ctx, who)
    with frozen_clock(later(ctx)):
        assert code(prepare(ctx.client, who.finance, three)) == OK
    [line] = lines(ctx.url, three)
    assert k_n(line) == (2, 4)  # before the anchor

    result, posted = in_turns(ctx.url, holding(three), posting(ctx))

    assert result is None and posted == "posted"
    stale = locked_response(ctx, who, three)
    assert code(stale) == STALE, stale.text
    assert stale.json()["details"] == {"added": 0, "removed": 0, "changed": 1}


def holding(period: uuid.UUID) -> Any:
    """A writer holding the prepared period's row, as ``finalize`` does."""

    async def call(session: AsyncSession) -> None:
        await payroll.lock_period(session, period)

    return call


@pytest.mark.parametrize("first", ["finalize", "anchor"])
def test_a9_an_anchor_and_a_finalize_of_an_earlier_period_take_turns(ctx, first):
    """Session 2's period is ``prepared`` at 1 of 4 when session 1 is approved
    late. Either the finalize commits first and the anchor reads its line (2 of
    4), or the anchor commits first and the finalize finds its line stale."""
    who = team(ctx)
    on_course_payroll(ctx)
    cohort = make_cohort(ctx, 4)
    one, two, three, rest = four_periods(ctx)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 1)
    with frozen_clock(later(ctx)):
        assert code(prepare(ctx.client, who.finance, two)) == OK
    review = datetime.fromisoformat(prepared_at(ctx.url, two) or "")
    principal = principal_of(who.owner.user.id, OWNER)
    teach(ctx, cohort, 0)
    post = posting(ctx)

    def finalizing(session: AsyncSession) -> Any:
        return payroll_workflow.finalize(session, principal, two, review)

    with frozen_clock(later(ctx)):
        if first == "finalize":
            done, posted = in_turns(ctx.url, finalizing, post)
            assert posted == "posted" and not isinstance(done, Exception)
            assert k_n(anchor_of(ctx, cohort.id_of(0))) == (2, 4)
        else:
            posted, refused = in_turns(ctx.url, post, finalizing)
            assert posted == "posted"
            assert isinstance(refused, payroll.PayrollPeriodStaleError)
            assert k_n(anchor_of(ctx, cohort.id_of(0))) == (1, 4)
            assert code(return_period(ctx.client, who.finance, two)) == OK
    if status_of(ctx.url, two) != "finalized":
        lock_at(ctx.client, ctx.url, who, later(ctx) + timedelta(seconds=1), two)
    teach(ctx, cohort, 2, 3)
    lock_at(ctx.client, ctx.url, who, later(ctx) + timedelta(seconds=2), three, rest)
    assert sum(cohort_amounts(ctx, cohort), Decimal("0.00")) == RATE


def test_a9_post_one_takes_the_tutors_periods_before_the_target(ctx, monkeypatch):
    """The lock order of an anchor: advisory lock, the not-finalized periods,
    then the target (module docstring)."""
    who = team(ctx)
    anchored_scene(ctx, who)
    calls: list[str] = []
    for name in ("lock_payroll_periods", "lock_unfinalized_periods_of_tutor"):
        real = getattr(payroll, name)

        def spy(*args: Any, _real: Any = real, _name: str = name, **kwargs: Any) -> Any:
            calls.append(_name)
            return _real(*args, **kwargs)

        monkeypatch.setattr(payroll, name, spy)
    real_target = payroll_adjustments.lock_target

    async def target(*args: Any, **kwargs: Any) -> Any:
        calls.append("lock_target")
        return await real_target(*args, **kwargs)

    monkeypatch.setattr(payroll_adjustments, "lock_target", target)
    step = posting(ctx)

    async def go() -> Any:
        engine = short_lived_engine(ctx.url)
        try:
            async with UnitOfWork(create_sessionmaker(engine)) as uow:
                return await step(uow.session)
        finally:
            await engine.dispose()

    assert asyncio.run(asyncio.wait_for(go(), WAIT_LIMIT)) == "posted"
    assert calls[:3] == [
        "lock_payroll_periods",
        "lock_unfinalized_periods_of_tutor",
        "lock_target",
    ]
