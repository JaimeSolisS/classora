"""A rate delete runs the add's refusal (M7-08d B1-B5), and posted adjustments
count as fixed prices in both refusals (C1-C3; #110 E5 and its QA D1-D4).

Deleting a version gives its window, from its date to the next version of the
kind, to the previous version, or to nothing. It is refused, ``409
compensation_rate_delete_under_finalized_line`` with ``details.latest_finalized_on``,
exactly when that would change a finalized line's price or a posted adjustment's,
by the add's rule: a per-course price on the cohort's first date ``F``, an
ordinary one on its own date ``D`` (and on ``F`` only against a ``per_course``
previous version), never against a previous version of the line's own model and
rate. A version a line or an adjustment names is ``409 compensation_rate_in_use``
first.

Each test takes its own decade; terms, the first versions and periods are set by
SQL, the rest by route. **Cells that can't occur through routes:** a finalized
price inside the window of the *first* version (B3) always names that version, so
it is ``409 compensation_rate_in_use``: a price is fixed by the version in force
then, and a version added before it later can't reach its date. The window going
to nothing is covered here by a stop previous version, and by
``tests/unit/test_payroll_rate_refusal.py``, whose oracle covers every small
layout of a delete against the pricing lookups.
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

from app.modules.identity.permissions import OWNER, TUTOR
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import CompensationRateDeleteUnderFinalizedLineError
from app.workflows import payroll_adjustments, payroll_compensation, payroll_workflow
from app.workflows.payroll_adjustments import PostAdjustmentsRunResult
from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client, create_user
from tests.integration.conftest import run_sql, settings_for
from tests.integration.guardian_support import delete
from tests.integration.payroll_calculation_support import (
    add_rate,
    add_terms,
    clear,
    lines,
    no_cohorts,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    Team,
    code,
    lock_at,
    prepare,
    prepared_at,
    status_of,
)
from tests.integration.series_edit_support import Ctx, make_ctx
from tests.integration.test_attendance import principal_of
from tests.integration.test_payroll_calculation_concurrency import in_turns
from tests.integration.test_payroll_per_course import (
    day,
    make_cohort,
    period_of,
    teach,
)
from tests.integration.test_payroll_workflow_course import add_version, later, team
from tests.integration.test_payroll_workflow_rate_matrix import (
    BASE,
    LAYOUTS,
    LINE_MODELS,
    NEW_MODELS,
    REFUSED,
    Cell,
    Layout,
    expected,
    scene,
)

DELETE_REFUSED = (409, "compensation_rate_delete_under_finalized_line")
IN_USE = (409, "compensation_rate_in_use")
NOT_FOUND = (404, "not_found")
GONE = (204, None)
WAIT_LIMIT = 15.0


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


def remove(ctx: Ctx, who: Team, rate_id: Any, *, tutor: Any = None) -> Any:
    with frozen_clock(later(ctx)):
        return delete(
            ctx.client,
            who.owner.cookie,
            f"/api/v1/payroll/tutors/{tutor or ctx.tutor.id}/rates/{rate_id}",
        )


def created(response: Any) -> str:
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def refused_on(
    response: Any, on: str, refusal: tuple[int, str] = DELETE_REFUSED
) -> None:
    assert code(response) == refusal, response.text
    assert response.json()["details"] == {"latest_finalized_on": on}


def run_job(url: str) -> PostAdjustmentsRunResult:
    return asyncio.run(payroll_adjustments.run_post_adjustments(settings_for(url)))


def rate_ids(ctx: Ctx) -> list[str]:
    return [
        str(row.id)
        for row in run_sql(
            ctx.url,
            "SELECT id FROM compensation_rates WHERE tutor_user_id = :u"
            " ORDER BY class_kind, effective_from",
            {"u": ctx.tutor.id},
        )
    ]


def course_version(ctx: Ctx, offset: int, rate: str | None, model: str) -> str:
    return str(
        add_rate(
            ctx.url, ctx.tutor.id, day(ctx, offset), rate, kind="course", model=model
        )
    )


# --- B1: the order -----


def test_b1_not_found_then_in_use_then_the_refusal_then_the_delete(ctx):
    """QA round 2's ``delete`` scene: an hourly cohort, a ``per_course`` version
    under an unused first-date version, which is then deleted."""
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    cohort = make_cohort(ctx, 4, weekdays=[1, 3])  # days 0, 2, 7, 9: F = 0
    on_first = course_version(ctx, -1, "120", "per_hour")  # prices F only
    named = course_version(ctx, 1, "110", "per_hour")  # prices D = 2
    period = period_of(ctx, ctx.tutor, 0, 4, first=True)
    period_of(ctx, ctx.tutor, 4, 60, first=False)
    teach(ctx, cohort, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), period)
    [line] = lines(ctx.url, period)
    assert (line.rate_model, line.amount) == ("per_hour", Decimal("110.00"))
    per_course = created(add_version(ctx, who, day(ctx, -3), "1000"))  # [-3, -1)
    stranger = create_user(ctx.url, roles=(TUTOR,))

    assert code(remove(ctx, who, per_course, tutor=stranger.id)) == NOT_FOUND
    assert code(remove(ctx, who, uuid.uuid4())) == NOT_FOUND
    assert code(remove(ctx, who, named)) == IN_USE
    # F would go to per-course, so the hourly line would be per-course.
    refused_on(remove(ctx, who, on_first), day(ctx, 0))
    assert sorted(rate_ids(ctx)) == sorted([on_first, named, per_course])
    assert code(remove(ctx, who, per_course)) == GONE  # its window holds nothing
    assert code(remove(ctx, who, on_first)) == GONE  # F goes to nothing: no change
    assert [dict(row._mapping) for row in lines(ctx.url, period)] == [
        dict(line._mapping)
    ]


def test_b1_the_in_use_wording_stays(ctx):
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    cohort = make_cohort(ctx, 3)
    only = course_version(ctx, -10, "1000", "per_course")
    period = period_of(ctx, ctx.tutor, 0, 70, first=True)
    teach(ctx, cohort, 0)
    lock_at(ctx.client, ctx.url, who, later(ctx), period)

    response = remove(ctx, who, only)

    assert code(response) == IN_USE
    assert response.json()["detail"] == "A payroll period uses this rate."


def test_b1_the_delete_locks_a_prepared_period_too(ctx):
    """The not-finalized periods ``FOR UPDATE`` (#110 L5's lock for adds): a
    writer holding a ``prepared`` period makes the delete wait."""
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    unused = course_version(ctx, -10, "1000", "per_course")
    period = period_of(ctx, ctx.tutor, 0, 70, first=True)
    with frozen_clock(later(ctx)):
        assert code(prepare(ctx.client, who.finance, period)) == OK

    async def holding(session: AsyncSession) -> None:
        await payroll.lock_period(session, period)

    async def deleting(session: AsyncSession) -> None:
        await payroll.delete_compensation_rate(
            session, ctx.tutor.id, uuid.UUID(unused), first_session_dates=no_cohorts
        )

    held, deleted = in_turns(ctx.url, holding, deleting)

    assert held is None and deleted is None
    assert rate_ids(ctx) == []


# --- B2: QA's scenes, through routes -----


def residual(ctx: Ctx, who: Team) -> tuple[Any, Any]:
    """#110 QA round 4's D4 scene: hourly 100 from day -30, per-course 1000 from
    day 10. Cohort A Mondays (0, 7, 14), cohort B Wednesdays from day 16. B's first
    line is finalized at 500.00, and A's day 14 is late, ``no_rate``."""
    set_floor(ctx.url, ctx.tl.monday.isoformat())
    add_terms(ctx.url, ctx.tutor.id)
    for on, rate, model in ((-30, "100", "per_hour"), (10, "1000", "per_course")):
        created(add_version(ctx, who, day(ctx, on), rate, model=model))
    cohort_a = make_cohort(ctx, 3, weekdays=[1])
    cohort_b = make_cohort(
        ctx, 2, weekdays=[3], starts_on=ctx.tl.monday + timedelta(days=16)
    )
    one = period_of(ctx, ctx.tutor, 0, 7)
    two = period_of(ctx, ctx.tutor, 7, 14)
    three = period_of(ctx, ctx.tutor, 21, 60)
    teach(ctx, cohort_a, 0)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort_a, 1)
    teach(ctx, cohort_b, 0)
    lock_at(ctx.client, ctx.url, who, later(ctx), two)
    teach(ctx, cohort_a, 2)
    return cohort_b, three


def test_b2_round_4_deleting_the_same_rate_cut_is_refused(ctx):
    """``d4_delete_v``: the same-rate cut on B's first date lets the hourly
    version on day 14 in; deleting the cut would pay B's next session hourly
    (600.00 on 1000.00). Refused now, naming B's first date."""
    who = team(ctx)
    cohort_b, three = residual(ctx, who)
    cut = created(add_version(ctx, who, day(ctx, 16), "1000.0000", model="per_course"))
    created(add_version(ctx, who, day(ctx, 14), "100", model="per_hour"))

    refused_on(remove(ctx, who, cut), day(ctx, 16))

    teach(ctx, cohort_b, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), three)
    b_lines = run_sql(
        ctx.url,
        "SELECT l.amount, l.rate_model FROM payroll_lines AS l"
        " JOIN class_sessions AS s ON s.id = l.class_session_id"
        " WHERE l.tutor_user_id = :u AND s.cohort_id = :c",
        {"u": ctx.tutor.id, "c": cohort_b.id},
    )
    assert {row.rate_model for row in b_lines} == {"per_course"}
    assert sum(row.amount for row in b_lines) == Decimal("1000.00")


def test_b2_round_4_deleting_the_first_dates_cut_into_a_stop_is_refused(ctx):
    """``pc_delete_original``: per-course 1000 from day -10, lines on days 0 and
    2 finalized. A same-rate V on -5, a same-rate V2 on F, V deleted, a stop on
    -5 (window [-5, F)): deleting V2 would leave F with no rate."""
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    created(add_version(ctx, who, day(ctx, -10), "1000"))
    cohort = make_cohort(ctx, 6)
    period = period_of(ctx, ctx.tutor, 0, 4, first=True)
    rest = period_of(ctx, ctx.tutor, 4, 70)
    teach(ctx, cohort, 0, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), period)
    assert [line.amount for line in lines(ctx.url, period)] == [
        Decimal("166.67"),
        Decimal("166.66"),
    ]
    v = created(add_version(ctx, who, day(ctx, -5), "1000"))
    v2 = created(add_version(ctx, who, day(ctx, 0), "1000"))
    assert code(remove(ctx, who, v)) == GONE
    created(add_version(ctx, who, day(ctx, -5), None, model=None))

    refused_on(remove(ctx, who, v2), day(ctx, 0))

    teach(ctx, cohort, 2)
    lock_at(ctx.client, ctx.url, who, later(ctx), rest)
    assert [(line.rate_model, line.amount) for line in lines(ctx.url, rest)] == [
        ("per_course", Decimal("166.67"))
    ]


def test_b2_controls_an_unused_window_and_a_same_rate_previous_version(ctx):
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    cohort = make_cohort(ctx, 3)  # days 0, 2, 4
    course_version(ctx, -10, "120", "per_hour")
    period = period_of(ctx, ctx.tutor, 0, 4, first=True)
    teach(ctx, cohort, 1)  # D = 2, 120.00
    lock_at(ctx.client, ctx.url, who, later(ctx), period)
    same = created(add_version(ctx, who, day(ctx, 1), "120.0000", model="per_hour"))
    unused = created(add_version(ctx, who, day(ctx, 3), "150", model="per_hour"))

    assert code(remove(ctx, who, unused)) == GONE  # [3, ...) holds no price
    assert code(remove(ctx, who, same)) == GONE  # back to 120: same price (D4)

    other = created(add_version(ctx, who, day(ctx, 1), "120.0000", model="per_hour"))
    created(add_version(ctx, who, day(ctx, -5), "150", model="per_hour"))  # [-5, 1)
    refused_on(remove(ctx, who, other), day(ctx, 2))  # D would go to 150


# --- B4: the delete matrix, through routes -----


def previous_day(layout: Layout, cell: Cell) -> int | None:
    """A day for a previous version just before the cell's start whose window
    holds no price, or ``None`` when no free day fits (the cell then runs with
    the layout's own previous version)."""
    for back in (1, 2, 3):
        start = cell.starts - back
        span = range(start, cell.starts)
        if any(v in range(start, cell.starts + 1) for v in layout.versions):
            return None
        if 0 in span or layout.own_day in span:
            continue
        return start
    return None


def layout_previous(
    layout: Layout, line_model: str, cell: Cell
) -> tuple[str | None, str | None]:
    """The model and rate of the layout's version before the cell's start."""
    earlier = [
        (offset, index)
        for index, offset in enumerate(layout.versions)
        if offset < cell.starts
    ]
    if not earlier:
        return None, None
    _, index = max(earlier)
    return line_model, str(int(BASE[line_model]) + index)


def delete_expected(
    line_model: str, line_rate: str, model: str | None, rate: str | None, cell: Cell
) -> tuple[bool, Any]:
    if model == line_model and rate is not None and Decimal(rate) == Decimal(line_rate):
        return False, 0
    return expected(line_model, model, cell)


@pytest.mark.parametrize("name", list(LAYOUTS))
@pytest.mark.parametrize("line_model", LINE_MODELS)
def test_the_delete_matrix(ctx, line_model, name):
    """Each cell: a version V of the line's own price at the cell's start (201 by
    D4), a previous version P of every model just before it, then V deleted, so
    its window goes to P. Then P and V are deleted again, back to the layout."""
    who = team(ctx)
    layout = LAYOUTS[name]
    period = scene(ctx, who, line_model, layout)
    before = [dict(line._mapping) for line in lines(ctx.url, period)]
    [line] = lines(ctx.url, period)
    line_rate = str(line.rate)
    wrong: list[str] = []
    cells = 0

    for cell in layout.cells:
        p_day = previous_day(layout, cell)
        models: tuple[Any, ...] = (
            (*NEW_MODELS, "same") if p_day is not None else ("layout",)
        )
        for model in models:
            label = f"{line_model}/{name}/{cell.name}/{model}"
            v = add_version(
                ctx, who, day(ctx, cell.starts), line_rate, model=line_model
            )
            if v.status_code != 201:
                wrong.append(f"{label}: V {v.status_code} {v.text}")
                continue
            p_id = None
            if model == "layout":
                p_model, p_rate = layout_previous(layout, line_model, cell)
            else:
                p_model = line_model if model == "same" else model
                p_rate = line_rate if model == "same" else ("777" if model else None)
                assert p_day is not None
                p = add_version(ctx, who, day(ctx, p_day), p_rate, model=p_model)
                if p.status_code != 201:
                    wrong.append(f"{label}: P {p.status_code} {p.text}")
                    remove(ctx, who, v.json()["id"])
                    continue
                p_id = p.json()["id"]
            refused, latest = delete_expected(
                line_model, line_rate, p_model, p_rate, cell
            )
            response = remove(ctx, who, v.json()["id"])
            cells += 1
            if refused:
                shown = day(ctx, layout.own_day if latest == "own" else latest)
                if code(response) != DELETE_REFUSED or response.json()["details"] != {
                    "latest_finalized_on": shown
                }:
                    wrong.append(f"{label}: {response.status_code} {response.text}")
            elif code(response) != GONE:
                wrong.append(f"{label}: {response.status_code} {response.text}")
            # Back to the layout: P's window holds no price, then V goes to the
            # layout's own previous version, the line's price on its date.
            if p_id is not None:
                assert code(remove(ctx, who, p_id)) == GONE, label
            if code(response) != GONE:
                assert code(remove(ctx, who, v.json()["id"])) == GONE, label

    assert wrong == []
    assert cells >= len(layout.cells)
    assert [dict(row._mapping) for row in lines(ctx.url, period)] == before


# --- B5: a delete racing a finalize -----


@pytest.mark.parametrize("first", ["finalize", "delete"])
def test_b5_a_delete_and_a_finalize_take_turns(ctx, first):
    """Round 2's O2 with the line ``prepared``: deleting the first date's hourly
    version would make the cohort per-course. Finalize first: the delete reads a
    finalized line and refuses. Delete first: the finalize finds the line stale."""
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    cohort = make_cohort(ctx, 4, weekdays=[1, 3])  # days 0, 2, 7, 9
    on_first = course_version(ctx, -1, "120", "per_hour")
    course_version(ctx, 1, "110", "per_hour")
    course_version(ctx, -3, "1000", "per_course")
    period = period_of(ctx, ctx.tutor, 0, 4, first=True)
    period_of(ctx, ctx.tutor, 4, 60, first=False)
    teach(ctx, cohort, 1)
    with frozen_clock(later(ctx)):
        assert code(prepare(ctx.client, who.finance, period)) == OK
    review = datetime.fromisoformat(prepared_at(ctx.url, period) or "")
    principal = principal_of(who.owner.user.id, OWNER)

    def finalizing(session: AsyncSession) -> Any:
        return payroll_workflow.finalize(session, principal, period, review)

    async def deleting(session: AsyncSession) -> None:
        await payroll_compensation.delete_compensation_rate(
            session, principal, ctx.tutor.id, uuid.UUID(on_first)
        )

    with frozen_clock(later(ctx)):
        if first == "finalize":
            done, refused = in_turns(ctx.url, finalizing, deleting)
            assert not isinstance(done, Exception), done
            assert isinstance(refused, CompensationRateDeleteUnderFinalizedLineError)
            assert refused.details == {"latest_finalized_on": day(ctx, 0)}
            assert status_of(ctx.url, period) == "finalized"
            assert on_first in rate_ids(ctx)
        else:
            done, stale = in_turns(ctx.url, deleting, finalizing)
            assert done is None
            assert isinstance(stale, payroll.PayrollPeriodStaleError)
            assert status_of(ctx.url, period) == "prepared"
            assert on_first not in rate_ids(ctx)


# --- C: posted adjustments in both refusals -----


def late_course_session(ctx: Ctx, who: Team, model: str, rate: str) -> Any:
    """Cohort of 4 from day 0; period one (days 0-1) finalized empty, session 1
    approved late and posted by the job into period two."""
    set_floor(ctx.url, ctx.tl.monday.isoformat())
    add_terms(ctx.url, ctx.tutor.id)
    version = course_version(ctx, -10, rate, model)
    cohort = make_cohort(ctx, 4)
    one = period_of(ctx, ctx.tutor, 0, 2)
    period_of(ctx, ctx.tutor, 2, 70)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 0)
    assert run_job(ctx.url) == PostAdjustmentsRunResult(candidates=1, posted=1)
    assert lines(ctx.url, one) == []
    return version


def test_c1_an_ordinary_late_approval_is_a_fixed_price_on_its_date(ctx):
    who = team(ctx)
    late_course_session(ctx, who, "per_hour", "120")

    refused = add_version(ctx, who, day(ctx, -2), "130", model="per_hour")
    refused_on(refused, day(ctx, 0), REFUSED)
    same = add_version(ctx, who, day(ctx, -2), "120.0000", model="per_hour")
    assert same.status_code == 201, same.text
    after = add_version(ctx, who, day(ctx, 1), "130", model="per_hour")
    assert after.status_code == 201, after.text  # the date is behind it


def test_c2_an_anchor_is_a_fixed_price_on_the_cohorts_first_date(ctx):
    """No finalized line exists at all: only the anchor."""
    who = team(ctx)
    late_course_session(ctx, who, "per_course", "1000")

    for rate, model in (("2000", "per_course"), ("1000", "per_hour"), (None, None)):
        response = add_version(ctx, who, day(ctx, 0), rate, model=model)
        refused_on(response, day(ctx, 0), REFUSED)
    same = add_version(ctx, who, day(ctx, 0), "1000.0000", model="per_course")
    assert same.status_code == 201, same.text


@pytest.mark.parametrize(
    ("model", "rate"), [("per_course", "1000"), ("per_hour", "120")]
)
def test_c3_the_delete_check_sees_the_adjustments(ctx, model, rate):
    """A same-rate V over the adjustment's pricing date (201, D4), a previous P of
    another rate just before it, then V deleted: refused. With P gone, V goes."""
    who = team(ctx)
    version = late_course_session(ctx, who, model, rate)
    v = created(add_version(ctx, who, day(ctx, 0), f"{rate}.0000", model=model))
    p = created(add_version(ctx, who, day(ctx, -1), "777", model=model))

    refused_on(remove(ctx, who, v), day(ctx, 0))
    assert code(remove(ctx, who, version)) == IN_USE  # the adjustment names it
    assert code(remove(ctx, who, p)) == GONE
    assert code(remove(ctx, who, v)) == GONE
