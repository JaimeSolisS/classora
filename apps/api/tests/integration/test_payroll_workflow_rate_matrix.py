"""The ``course`` rate refusal as a matrix, through real routes (#110 E5, QA D1,
D2, D3): finalized line kind by new version model by window position.

``F`` is the cohort's first date and ``D`` the line's own date. Each scene
finalizes one real line by the route, then posts every model at every position
of its layout. A refused post writes nothing, and an accepted version is deleted
by its route, so all the cells of a scene share its line. ``CELLS`` is written by
hand from the rule "refused only if it would change a finalized line's price":

- a ``per_course`` line: refused when the window holds ``F``, any model;
- an ordinary line: refused when it holds ``D``, any model; when it holds ``F``
  only, refused only by a ``per_course`` version.

The same matrix runs against the pricing itself in
``tests/unit/test_payroll_rate_refusal.py``.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.guardian_support import delete
from tests.integration.payroll_calculation_support import (
    add_rate,
    add_terms,
    clear,
    lines,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    Team,
    code,
    finalize,
    lock_at,
    prepare,
    status_of,
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

REFUSED = (422, "effective_from_under_finalized_line")
LINE_MODELS = ("per_course", "per_hour", "per_session")
NEW_MODELS = ("per_course", "per_hour", "per_session", None)
BASE = {"per_course": "1000", "per_hour": "120", "per_session": "120"}


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


@dataclass(frozen=True)
class Cell:
    """A new version from day ``starts``, and what its window holds."""

    name: str
    starts: int
    holds_first: bool
    holds_own: bool


@dataclass(frozen=True)
class Layout:
    """The versions before the line: days from the first Monday, ``F`` = 0."""

    versions: tuple[int, ...]
    own_day: int
    cells: tuple[Cell, ...]


LAYOUTS = {
    # A version between F and D, so a window can hold F alone.
    "split": Layout(
        (-10, 1),
        2,
        (
            Cell("before both", -12, False, False),
            Cell("F only", 0, True, False),
            Cell("D only, on D", 2, False, True),
            Cell("after both", 3, False, False),
        ),
    ),
    # One version: a window from F on holds both.
    "open": Layout(
        (-10,),
        2,
        (
            Cell("both, from F", 0, True, True),
            Cell("both, from before F", -5, True, True),
            Cell("D only, from between", 1, False, True),
            Cell("D only, on D", 2, False, True),
            Cell("after both", 3, False, False),
        ),
    ),
    # The line is on the first session: F = D.
    "on_first": Layout(
        (-10,),
        0,
        (
            Cell("both, on F = D", 0, True, True),
            Cell("after both", 1, False, False),
        ),
    ),
    # The next version is on F: its date isn't in the window.
    "next_on_first": Layout(
        (0,),
        2,
        (
            Cell("before, ending on F", -3, False, False),
            Cell("D only", 1, False, True),
        ),
    ),
    # The next version is on D: its date isn't in the window.
    "next_on_own": Layout(
        (-10, 2),
        2,
        (
            Cell("F only, ending on D", 0, True, False),
            Cell("between, ending on D", 1, False, False),
        ),
    ),
}


def expected(line_model: str, new_model: str | None, cell: Cell) -> tuple[Any, Any]:
    """The hand-written rule: (refused, the date to go past, in days)."""
    if line_model == "per_course":
        return cell.holds_first, 0
    if cell.holds_own:
        return True, "own"
    return (cell.holds_first and new_model == "per_course"), 0


def base_rate(ctx: Ctx, model: str, on: str, rate: str | None = None) -> Any:
    return add_rate(
        ctx.url, ctx.tutor.id, on, rate or BASE[model], kind="course", model=model
    )


def scene(ctx: Ctx, who: Team, line_model: str, layout: Layout) -> Any:
    """One finalized line of ``line_model`` on the layout's own date."""
    add_terms(ctx.url, ctx.tutor.id)
    for index, offset in enumerate(layout.versions):
        rate = str(int(BASE[line_model]) + index)
        base_rate(ctx, line_model, day(ctx, offset), rate)
    cohort = make_cohort(ctx, 3)  # Monday, Wednesday, Friday
    period = period_of(ctx, ctx.tutor, layout.own_day, 2, first=True)
    teach(ctx, cohort, 0 if layout.own_day == 0 else 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), period)
    [line] = lines(ctx.url, period)
    assert (line.rate_model, line.service_date.isoformat()) == (
        line_model,
        day(ctx, layout.own_day),
    )
    return period


@pytest.mark.parametrize("name", list(LAYOUTS))
@pytest.mark.parametrize("line_model", LINE_MODELS)
def test_the_matrix(ctx, line_model, name):
    who = team(ctx)
    layout = LAYOUTS[name]
    period = scene(ctx, who, line_model, layout)
    before = [dict(line._mapping) for line in lines(ctx.url, period)]
    wrong: list[str] = []

    for cell in layout.cells:
        for model in NEW_MODELS:
            label = f"{line_model}/{name}/{cell.name}/{model}"
            refused, latest = expected(line_model, model, cell)
            response = add_version(
                ctx, who, day(ctx, cell.starts), "777" if model else None, model=model
            )
            if refused:
                date_shown = day(ctx, layout.own_day if latest == "own" else latest)
                if code(response) != REFUSED or response.json()["details"] != {
                    "latest_finalized_on": date_shown
                }:
                    wrong.append(f"{label}: {response.status_code} {response.text}")
            elif response.status_code != 201:
                wrong.append(f"{label}: {response.status_code} {response.text}")
            else:
                with frozen_clock(later(ctx)):
                    gone = delete(
                        ctx.client,
                        who.owner.cookie,
                        f"/api/v1/payroll/tutors/{ctx.tutor.id}/rates"
                        f"/{response.json()['id']}",
                    )
                assert gone.status_code == 204, gone.text

    assert wrong == []
    assert [dict(line._mapping) for line in lines(ctx.url, period)] == before


def mixed_cohort(ctx: Ctx, who: Team) -> tuple[Any, Any]:
    """Reachable only through a delete (QA O2): an ordinary line finalized, then a
    ``per_course`` version under an unused first version, which is deleted, so the
    next line of the cohort is per-course."""
    add_terms(ctx.url, ctx.tutor.id)
    cohort = make_cohort(ctx, 3)  # days 0, 2, 4
    unused = base_rate(ctx, "per_hour", day(ctx, -2))
    base_rate(ctx, "per_hour", day(ctx, 1))
    one = period_of(ctx, ctx.tutor, 2, 2, first=True)
    two = period_of(ctx, ctx.tutor, 4, 60, first=False)
    teach(ctx, cohort, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    created = add_version(ctx, who, day(ctx, -5), "1000")
    assert created.status_code == 201, created.text
    with frozen_clock(later(ctx)):
        gone = delete(
            ctx.client,
            who.owner.cookie,
            f"/api/v1/payroll/tutors/{ctx.tutor.id}/rates/{unused}",
        )
    assert gone.status_code == 204, gone.text
    teach(ctx, cohort, 2)
    lock_at(ctx.client, ctx.url, who, later(ctx), two)
    assert lines(ctx.url, one)[0].rate_model == "per_hour"
    assert lines(ctx.url, two)[0].rate_model == "per_course"
    return one, two


@pytest.mark.parametrize("model", NEW_MODELS)
def test_mixed_lines_in_one_cohort_each_line_answers_for_itself(ctx, model):
    who = team(ctx)
    mixed_cohort(ctx, who)
    rate = "777" if model else None

    # F: the per-course line refuses, whatever the model.
    first = add_version(ctx, who, day(ctx, -1), rate, model=model)
    assert code(first) == REFUSED, first.text
    assert first.json()["details"] == {"latest_finalized_on": day(ctx, 0)}

    # D of the ordinary line: it refuses, and the per-course line wouldn't.
    own = add_version(ctx, who, day(ctx, 2), rate, model=model)
    assert code(own) == REFUSED, own.text
    assert own.json()["details"] == {"latest_finalized_on": day(ctx, 2)}

    # Past the ordinary line, on the per-course line's own stretch: nothing changes.
    after = add_version(ctx, who, day(ctx, 3), rate, model=model)
    assert after.status_code == 201, after.text


@pytest.mark.parametrize(
    ("line_model", "new_model"),
    [
        ("per_hour", "per_hour"),
        ("per_hour", "per_session"),
        ("per_session", "per_hour"),
        ("per_session", "per_session"),
    ],
)
def test_d2_a_no_rate_first_session_gets_a_rate_past_an_ordinary_line(
    ctx, line_model, new_model
):
    """QA's ``trap``: the first session is approved late, in a period finalized
    empty, with an ordinary line later in the cohort. A ``per_course`` version
    stays refused, the ordinary one passes, and the tutor goes on."""
    who = team(ctx)
    set_floor(ctx.url, ctx.tl.monday.isoformat())
    add_terms(ctx.url, ctx.tutor.id)
    base_rate(ctx, line_model, day(ctx, 1))
    cohort = make_cohort(ctx, 3)
    one = period_of(ctx, ctx.tutor, 0, 2)
    two = period_of(ctx, ctx.tutor, 2, 2)
    three = period_of(ctx, ctx.tutor, 4, 60)
    lock_at(ctx.client, ctx.url, who, later(ctx), one)
    teach(ctx, cohort, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), two)
    paid = [dict(line._mapping) for line in lines(ctx.url, two)]
    teach(ctx, cohort, 0)
    assert [item["reason"] for item in unresolved(ctx, who)] == ["no_rate"]
    with frozen_clock(later(ctx)):
        refused = prepare(ctx.client, who.finance, three)
    assert code(refused) == (409, "payroll_unresolved_work")
    per_course = add_version(ctx, who, day(ctx, 0), "1000")
    assert code(per_course) == REFUSED, per_course.text

    created = add_version(ctx, who, day(ctx, 0), "90", model=new_model)

    assert created.status_code == 201, created.text
    assert unresolved(ctx, who) == []
    lock_at(ctx.client, ctx.url, who, later(ctx), three)
    assert status_of(ctx.url, three) == "finalized"
    assert [dict(line._mapping) for line in lines(ctx.url, two)] == paid


def test_d2_proof_the_same_version_under_a_prepared_line_is_no_drift(ctx):
    """QA's ``proof``: it changes no price, so ``finalize`` passes, not stale."""
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    base_rate(ctx, "per_hour", day(ctx, 1))
    cohort = make_cohort(ctx, 3)
    period = period_of(ctx, ctx.tutor, 2, 2, first=True)
    teach(ctx, cohort, 1)
    with frozen_clock(later(ctx)):
        assert code(prepare(ctx.client, who.finance, period)) == OK
    created = add_version(ctx, who, day(ctx, 0), "90", model="per_hour")
    assert created.status_code == 201, created.text

    with frozen_clock(later(ctx)):
        response = finalize(ctx.client, ctx.url, who.owner, period)

    assert code(response) == OK, response.text
    assert lines(ctx.url, period)[0].amount == Decimal("120.00")


def test_d3_the_date_shown_is_the_newest_and_a_day_after_it_clears(ctx):
    """QA's ``latest``, with two lines, so the newest of all the lines counts."""
    who = team(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    base_rate(ctx, "per_hour", "2000-01-01")
    cohort = make_cohort(ctx, 3)
    period = period_of(ctx, ctx.tutor, 0, 4)
    teach(ctx, cohort, 0, 1)
    lock_at(ctx.client, ctx.url, who, later(ctx), period)

    for starts in (0, 1, 2):
        response = add_version(ctx, who, day(ctx, starts), "90", model="per_hour")
        assert code(response) == REFUSED, response.text
        assert response.json()["details"] == {"latest_finalized_on": day(ctx, 2)}

    assert add_version(ctx, who, day(ctx, 3), "90", model="per_hour").status_code == 201
