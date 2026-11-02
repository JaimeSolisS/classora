"""Per-course pay through real cohorts (#442; ADR-0017 E3, #107 E2, E11).

Each test takes its own decade (:func:`~tests.integration.series_edit_support.
make_ctx`). The cohort is created through the API on Sunday ``tl.now``, offered to
the ctx tutor, who accepts, and runs Mondays and Wednesdays at 16:00 from
``tl.monday``. Closeouts go through the real routes, two hours after each start.

Every expected amount is worked by hand from ``rate x k / N``, half away from zero
to the centavo. A rate of 1000 over three sessions is 333.33, 666.67, 1000.00
cumulative.
"""

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.academics import service as academics
from app.modules.identity.permissions import FINANCE_STAFF, TUTOR
from app.modules.payroll import service as payroll
from app.modules.scheduling import service as scheduling
from app.workflows import payroll_calculation
from tests.conftest import frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.catalog_support import count_statements, get, post
from tests.integration.change_request_support import Scene, create, make_scene
from tests.integration.cohort_support import (
    cohort_body,
    cohort_rows,
    cohort_series,
    create_cohort,
    enroll,
    new_template,
)
from tests.integration.conftest import run_sql, short_lived_engine
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
    stable,
)
from tests.integration.payroll_period_support import finalize
from tests.integration.schedule_support import stored
from tests.integration.series_edit_support import OFFERS, Ctx, as_user, make_ctx
from tests.integration.test_assignment_offers import new_tutor_user
from tests.integration.test_attendance import Lesson
from tests.integration.test_class_session_makeups import book, refused
from tests.integration.test_payroll_calculation import _store_closeout
from tests.integration.test_session_cancellation import cancel, read
from tests.integration.test_session_substitutions import substituted

OK = (200, None)
CREATED = (201, None)
WAIT_LIMIT = 30.0


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


@dataclass
class Cohort:
    id: str
    sessions: list[dict[str, Any]]

    def session(self, index: int) -> dict[str, Any]:
        return self.sessions[index]

    def id_of(self, index: int) -> str:
        return str(self.sessions[index]["id"])


def rows_of(ctx: Ctx, cohort_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": str(row.id),
            "version": row.version,
            "starts_at": row.starts_at.isoformat(),
            "state": row.state,
        }
        for row in cohort_rows(ctx.url, cohort_id)
    ]


def make_cohort(
    ctx: Ctx,
    sessions: int = 3,
    *,
    tutor: CreatedUser | None = None,
    start_time: str = "16:00",
    student: uuid.UUID | None = None,
    enrolled: bool = True,
    weekdays: list[int] | None = None,
) -> Cohort:
    """A real cohort of ``sessions`` 60-minute sessions, ``confirmed``."""
    who = tutor or ctx.tutor
    cookie = as_user(ctx, ctx.owner, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        template = new_template(
            ctx.client,
            cookie,
            [ctx.subject],
            session_count=sessions,
            session_minutes=60,
        )
        made = create_cohort(
            ctx.client,
            cookie,
            cohort_body(
                template=template["id"],
                subject=ctx.subject,
                starts_on=ctx.tl.monday,
                tutor=who.id,
                start_time=start_time,
                weekdays=weekdays,
            ),
        )
        assert code_of(made) == CREATED, made.text
        cohort_id = made.json()["id"]
        if enrolled:
            joined = enroll(ctx.client, cookie, cohort_id, student or ctx.student)
            assert code_of(joined) == CREATED, joined.text
    [series] = cohort_series(ctx.url, cohort_id)
    [offer] = run_sql(
        ctx.url,
        "SELECT id FROM assignment_offers WHERE class_series_id = :s"
        " AND status = 'pending'",
        {"s": series.id},
    )
    answer = as_user(ctx, who, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        accepted = post(ctx.client, answer, f"{OFFERS}/{offer.id}/accept", None)
    assert code_of(accepted) == OK, accepted.text
    found = rows_of(ctx, cohort_id)
    assert len(found) == sessions
    assert {row["state"] for row in found} == {"confirmed"}
    return Cohort(id=cohort_id, sessions=found)


def on_payroll(
    ctx: Ctx,
    tutor: CreatedUser | None = None,
    rate: str = "1000",
    *,
    on: str = "2000-01-01",
    model: str = "per_course",
) -> uuid.UUID:
    """Terms, a rate, and one open period over the cohort's first ten weeks."""
    who = tutor or ctx.tutor
    add_terms(ctx.url, who.id)
    add_rate(ctx.url, who.id, on, rate, model=model, kind="course")
    return period_of(ctx, who, 0, 70)


def period_of(
    ctx: Ctx, tutor: CreatedUser, offset: int, days: int, *, first: bool | None = None
) -> uuid.UUID:
    """A period from ``offset`` days after the first Monday, for ``days``. A
    later period must follow the one before it, so only the first is free."""
    start = ctx.tl.monday + timedelta(days=offset)
    end = start + timedelta(days=days - 1)
    return add_period_row(
        ctx.url,
        tutor.id,
        start.isoformat(),
        end.isoformat(),
        "monthly",
        first=offset == 0 if first is None else first,
    )


def day(ctx: Ctx, offset: int) -> str:
    return (ctx.tl.monday + timedelta(days=offset)).isoformat()


def amounts(url: str, period: uuid.UUID) -> list[Decimal]:
    return [line.amount for line in lines(url, period)]


def shares(url: str, period: uuid.UUID) -> list[tuple[int, int]]:
    return [
        (line.cumulative_sessions, line.cohort_sessions) for line in lines(url, period)
    ]


def cents(*values: str) -> list[Decimal]:
    return [Decimal(value) for value in values]


def teach(ctx: Ctx, cohort: Cohort, *indexes: int, **kwargs: Any) -> None:
    for index in indexes:
        closed_out(ctx, cohort.session(index), minutes=60, **kwargs)


def by_order(url: str, period: uuid.UUID) -> list[str]:
    return [str(line.class_session_id) for line in lines(url, period)]


# --- G2: the rate -----


def test_g2_the_first_sessions_version_applies_to_all(ctx):
    cohort = make_cohort(ctx)
    period = on_payroll(ctx, on="2000-01-01")
    teach(ctx, cohort, 0)
    second = add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 1), "6000", kind="course", model="per_course"
    )
    teach(ctx, cohort, 1, 2)

    run(ctx.url, period)

    assert amounts(ctx.url, period) == cents("333.33", "333.34", "333.33")
    assert shares(ctx.url, period) == [(1, 3), (2, 3), (3, 3)]
    found = lines(ctx.url, period)
    assert {line.rate_model for line in found} == {"per_course"}
    assert second not in {line.compensation_rate_id for line in found}
    assert {line.rate for line in found} == {Decimal("1000.0000")}
    assert sum(amounts(ctx.url, period)) == Decimal("1000.00")


def test_g2_a_per_hour_change_mid_course_uses_each_dates_version(ctx):
    cohort = make_cohort(ctx)
    period = on_payroll(ctx, rate="120", model="per_hour")
    add_rate(ctx.url, ctx.tutor.id, day(ctx, 1), "150", kind="course")
    teach(ctx, cohort, 0, 1)

    run(ctx.url, period)

    # 60 minutes at each date's hourly rate.
    assert amounts(ctx.url, period) == cents("120.00", "150.00")
    assert shares(ctx.url, period) == [(None, None), (None, None)]


def test_g10_per_course_only_after_the_first_date_is_no_line(ctx):
    cohort = make_cohort(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 1), "1000", kind="course", model="per_course"
    )
    period = period_of(ctx, ctx.tutor, 0, 70)
    teach(ctx, cohort, 0, 1)

    run(ctx.url, period)
    assert lines(ctx.url, period) == []

    # A back-dated version fixes it, for every session.
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 0), "900", kind="course", model="per_course"
    )
    run(ctx.url, period)
    assert amounts(ctx.url, period) == cents("300.00", "300.00")


def test_b4_a_salaried_tutor_with_no_rate_is_covered(ctx):
    cohort = make_cohort(ctx)
    add_terms(ctx.url, ctx.tutor.id, salary="9000")
    period = period_of(ctx, ctx.tutor, 0, 70)
    teach(ctx, cohort, 0)

    run(ctx.url, period)

    [line] = lines(ctx.url, period)
    assert (line.kind, line.amount, line.rate_model) == ("session", 0, None)


# --- G3: cancellations -----


def test_g3_a_cancelled_first_session_keeps_the_lookup_date(ctx):
    cohort = make_cohort(ctx)
    answered = cancel(ctx, cohort.id_of(0), cohort.session(0)["version"])
    assert code_of(answered) == OK, answered.text
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, -1), "1000", kind="course", model="per_course"
    )
    # A version between the cancelled first session and the next one that runs:
    # read from the next session instead, it would be 8000.
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 1), "8000", kind="course", model="per_course"
    )
    period = period_of(ctx, ctx.tutor, 0, 70)
    teach(ctx, cohort, 1, 2)

    run(ctx.url, period)

    assert shares(ctx.url, period) == [(1, 2), (2, 2)]
    assert amounts(ctx.url, period) == cents("500.00", "500.00")


def test_g3_a_cancelled_mid_session_shrinks_n_and_the_next_line_catches_up(ctx):
    cohort = make_cohort(ctx)
    period = on_payroll(ctx)
    teach(ctx, cohort, 0)
    run(ctx.url, period)
    assert amounts(ctx.url, period) == cents("333.33")

    answered = cancel(ctx, cohort.id_of(1), cohort.session(1)["version"])
    assert code_of(answered) == OK, answered.text
    teach(ctx, cohort, 2)
    run(ctx.url, period)

    # N = 2: 500.00, 1000.00. The first open line follows the new N too.
    assert amounts(ctx.url, period) == cents("500.00", "500.00")
    assert sum(amounts(ctx.url, period)) == Decimal("1000.00")


def test_g3_after_a_finalized_line_the_catch_up_falls_on_the_next_line(ctx):
    cohort = make_cohort(ctx)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    first = period_of(ctx, ctx.tutor, 0, 2)
    later = period_of(ctx, ctx.tutor, 2, 60)
    teach(ctx, cohort, 0)
    run(ctx.url, first)
    finalize(ctx.url, first, ctx.owner.id)
    answered = cancel(ctx, cohort.id_of(1), cohort.session(1)["version"])
    assert code_of(answered) == OK, answered.text
    teach(ctx, cohort, 2)

    run(ctx.url, later)

    # Anchor 1000 x 1/3 = 333.33 with N = 2: 1000.00 - 333.33.
    assert amounts(ctx.url, first) == cents("333.33")
    assert amounts(ctx.url, later) == cents("666.67")
    assert shares(ctx.url, later) == [(2, 2)]


# --- G4, G5: the anchor -----


def two_periods(ctx: Ctx) -> tuple[uuid.UUID, uuid.UUID]:
    """First week, then the weeks after it, for a tutor on payroll."""
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    return (
        period_of(ctx, ctx.tutor, 0, 7),
        period_of(ctx, ctx.tutor, 7, 63),
    )


def grow(ctx: Ctx, cohort: Cohort, *weeks: int) -> None:
    """Sessions added to the cohort's end: ``N`` grows."""
    [row] = run_sql(
        ctx.url,
        "SELECT subject_id, tutor_user_id, series_id FROM class_sessions"
        " WHERE id = :id",
        {"id": cohort.id_of(0)},
    )
    for week in weeks:
        stored(
            ctx.url,
            ctx.tl.at(ctx.tl.week(week, 5), "16:00"),
            kind="course",
            cohort_id=uuid.UUID(cohort.id),
            subject_id=row.subject_id,
            tutor_user_id=row.tutor_user_id,
            students=(ctx.student,),
        )


def test_g4_when_n_grows_the_next_line_is_zero_until_cum_passes_the_anchor(ctx):
    cohort = make_cohort(ctx, 4)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1)  # the first week: k = 2 of 4, 500.00
    run(ctx.url, first)
    finalize(ctx.url, first, ctx.owner.id)
    assert amounts(ctx.url, first) == cents("250.00", "250.00")

    grow(ctx, cohort, 3, 4, 5, 6)  # N = 8
    teach(ctx, cohort, 2, 3)  # 1000 x 3/8 = 375.00, 1000 x 4/8 = 500.00
    run(ctx.url, second)

    assert amounts(ctx.url, second) == cents("0.00", "0.00")
    assert shares(ctx.url, second) == [(3, 8), (4, 8)]
    assert all(amount >= 0 for amount in amounts(ctx.url, second))
    grown = rows_of(ctx, cohort.id)
    closed_out(ctx, grown[4], minutes=60)  # 1000 x 5/8 = 625.00, past 500.00
    run(ctx.url, second)
    assert amounts(ctx.url, second) == cents("0.00", "0.00", "125.00")


def test_g5_an_anchor_with_another_n(ctx):
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1)  # k = 2 of 3: 666.67
    run(ctx.url, first)
    finalize(ctx.url, first, ctx.owner.id)
    grow(ctx, cohort, 3)  # N = 4
    teach(ctx, cohort, 2)

    run(ctx.url, second)

    # max(666.67, 1000 x 3/4 = 750.00) - 666.67, with k = 3 from the stored k.
    assert amounts(ctx.url, second) == cents("83.33")
    assert shares(ctx.url, second) == [(3, 4)]
    assert shares(ctx.url, first) == [(1, 3), (2, 3)]


def test_a_finalized_lines_rate_snapshot_sets_cum(ctx):
    """The stored rate, not today's: a version deleted and re-added at another
    rate can't change what was already earned."""
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1)  # 1000 x 2/3 = 666.67
    run(ctx.url, first)
    finalize(ctx.url, first, ctx.owner.id)
    add_rate(
        ctx.url,
        ctx.tutor.id,
        day(ctx, -1),
        "1400",
        kind="course",
        model="per_course",
    )
    teach(ctx, cohort, 2)

    run(ctx.url, second)

    # 1400 x 3/3 = 1400.00 - the anchor's 666.67. At 1400 the anchor would be 933.33.
    assert amounts(ctx.url, second) == cents("733.33")


def test_work_before_the_first_period_counts_once_after_a_finalized_line(ctx):
    """A cohort that began before the tutor's first period: the first session is
    in no finalized period and before the anchor. It is in the anchor's ``k``."""
    cohort = make_cohort(ctx, 3)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    middle = period_of(ctx, ctx.tutor, 1, 3, first=True)  # the first Wednesday
    last = period_of(ctx, ctx.tutor, 4, 60)
    teach(ctx, cohort, 0, 1)
    run(ctx.url, middle)
    finalize(ctx.url, middle, ctx.owner.id)
    assert shares(ctx.url, middle) == [(2, 3)]
    teach(ctx, cohort, 2)

    run(ctx.url, last)

    assert shares(ctx.url, last) == [(3, 3)]
    assert amounts(ctx.url, last) == cents("333.33")


def test_an_open_period_keeps_its_share_when_a_later_one_is_finalized_first(ctx):
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1, 2)
    run(ctx.url, second)
    finalize(ctx.url, second, ctx.owner.id)

    run(ctx.url, first)

    assert shares(ctx.url, first) == [(1, 3), (2, 3)]
    assert amounts(ctx.url, first) == cents("333.33", "333.34")
    assert amounts(ctx.url, second) == cents("333.33")


# --- QA #442 N1, state (c): cum at a finalized line -----


def test_n1_a_floored_finalized_line_isnt_paid_again(ctx):
    cohort = make_cohort(ctx, 2)  # Monday, Wednesday; N = 2
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    one = period_of(ctx, ctx.tutor, 0, 2)
    two = period_of(ctx, ctx.tutor, 2, 3)
    rest = period_of(ctx, ctx.tutor, 5, 60)
    teach(ctx, cohort, 0)
    run(ctx.url, one)
    finalize(ctx.url, one, ctx.owner.id)
    assert amounts(ctx.url, one) == cents("500.00")

    grow(ctx, cohort, 2, 3, 4, 5)  # N = 6
    teach(ctx, cohort, 1)
    run(ctx.url, two)
    finalize(ctx.url, two, ctx.owner.id)
    assert amounts(ctx.url, two) == cents("0.00")
    assert shares(ctx.url, two) == [(2, 6)]

    for session in rows_of(ctx, cohort.id)[2:]:
        closed_out(ctx, session, minutes=60)
    run(ctx.url, rest)

    # k = 3..6: 500.00, 666.67, 833.33, 1000.00, from the 500.00 already paid.
    assert amounts(ctx.url, rest) == cents("0.00", "166.67", "166.66", "166.67")
    assert shares(ctx.url, rest) == [(3, 6), (4, 6), (5, 6), (6, 6)]
    paid = [*amounts(ctx.url, one), *amounts(ctx.url, two), *amounts(ctx.url, rest)]
    assert sum(paid) == Decimal("1000.00")


def test_d8_a_no_show_at_50_percent_on_a_floored_share_pays_nothing(ctx):
    add_rule(ctx.url, "no_show", "2025-01-01", "percent", percent="50")
    cohort = make_cohort(ctx, 2)  # Monday, Wednesday; N = 2
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    one = period_of(ctx, ctx.tutor, 0, 2)
    two = period_of(ctx, ctx.tutor, 2, 3)
    rest = period_of(ctx, ctx.tutor, 5, 60)
    teach(ctx, cohort, 0)
    run(ctx.url, one)
    finalize(ctx.url, one, ctx.owner.id)
    assert amounts(ctx.url, one) == cents("500.00")

    grow(ctx, cohort, 2, 3)  # N = 4
    closed_out(ctx, cohort.session(1), status="absent", minutes=60)
    run(ctx.url, two)
    finalize(ctx.url, two, ctx.owner.id)
    [no_show] = lines(ctx.url, two)
    # 2 of 4 is 500.00, already paid. Uncapped: 50% of 250.00 = 125.00.
    assert (no_show.kind, no_show.amount) == ("no_show", Decimal("0.00"))

    for session in rows_of(ctx, cohort.id)[2:]:
        closed_out(ctx, session, minutes=60)
    run(ctx.url, rest)

    assert amounts(ctx.url, rest) == cents("250.00", "250.00")
    paid = [*amounts(ctx.url, one), no_show.amount, *amounts(ctx.url, rest)]
    assert paid == cents("500.00", "0.00", "250.00", "250.00")
    assert sum(paid) == Decimal("1000.00")


def test_state_c_late_approvals_before_a_finalized_line_pay_200_each(ctx):
    """S3 finalized at 1 of 5 while S1 and S2 had no approval. ``max(cum, line)``
    would keep S1 and S2's 400.00 at S3, and S4 would pay 0.00."""
    cohort = make_cohort(ctx, 5)  # Monday, Wednesday, Monday, Wednesday, Monday
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    early = period_of(ctx, ctx.tutor, 0, 7)
    middle = period_of(ctx, ctx.tutor, 7, 2)
    late = period_of(ctx, ctx.tutor, 9, 60)
    teach(ctx, cohort, 2)
    run(ctx.url, middle)
    finalize(ctx.url, middle, ctx.owner.id)
    assert shares(ctx.url, middle) == [(1, 5)]
    teach(ctx, cohort, 0, 1, 3, 4)

    run(ctx.url, early)
    run(ctx.url, late)

    assert amounts(ctx.url, early) == cents("200.00", "200.00")
    assert amounts(ctx.url, late) == cents("200.00", "200.00")
    assert shares(ctx.url, early) == [(1, 5), (2, 5)]
    assert shares(ctx.url, late) == [(2, 5), (3, 5)]
    assert amounts(ctx.url, middle) == cents("200.00")


# --- QA #442 test gaps: m3-m8 -----


def test_m3_another_tutors_finalized_period_doesnt_hide_these_dates(ctx):
    cohort = make_cohort(ctx, 3)
    period = on_payroll(ctx)
    teach(ctx, cohort, 0)
    stranger = create_user(ctx.url, roles=(TUTOR,))
    add_terms(ctx.url, stranger.id)
    theirs = period_of(ctx, stranger, 0, 70)
    finalize(ctx.url, theirs, ctx.owner.id)

    run(ctx.url, period)

    assert amounts(ctx.url, period) == cents("333.33")
    assert shares(ctx.url, period) == [(1, 3)]


def test_m4_a_substitutes_finalized_line_doesnt_anchor_the_original_tutor(ctx):
    cohort = make_cohort(ctx, 3)
    substitute = new_tutor_user(ctx.url, qualified_for=(ctx.subject,))
    period = on_payroll(ctx)
    their_period = on_payroll(ctx, substitute, "600")
    updated = sub_in(ctx, cohort.id_of(1), substitute)
    closed_out(ctx, updated, tutor=substitute, minutes=60)
    run(ctx.url, their_period)
    finalize(ctx.url, their_period, ctx.owner.id)
    assert shares(ctx.url, their_period) == [(1, 3)]
    teach(ctx, cohort, 0, 2)

    run(ctx.url, period)

    assert shares(ctx.url, period) == [(1, 3), (2, 3)]
    assert amounts(ctx.url, period) == cents("333.33", "333.34")


def test_m5_d6_an_hourly_finalized_line_doesnt_anchor(ctx):
    cohort = make_cohort(ctx, 3)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="120", kind="course")
    first = period_of(ctx, ctx.tutor, 0, 2)
    later = period_of(ctx, ctx.tutor, 2, 60)
    teach(ctx, cohort, 0)
    run(ctx.url, first)
    finalize(ctx.url, first, ctx.owner.id)
    assert shares(ctx.url, first) == [(None, None)]
    add_rate(
        ctx.url,
        ctx.tutor.id,
        day(ctx, -1),
        "1000",
        kind="course",
        model="per_course",
    )
    teach(ctx, cohort, 1)

    run(ctx.url, later)

    assert shares(ctx.url, later) == [(1, 3)]
    assert amounts(ctx.url, later) == cents("333.33")


def test_m6_two_interleaved_cohorts_of_one_tutor_walk_apart(ctx):
    first = make_cohort(ctx, 3)
    second = make_cohort(ctx, 2, start_time="18:00", enrolled=False)
    run_sql(
        ctx.url,
        "INSERT INTO class_session_participants (class_session_id, student_id)"
        " SELECT id, :student FROM class_sessions WHERE cohort_id = :cohort",
        {"student": ctx.student, "cohort": second.id},
    )
    period = on_payroll(ctx)
    teach(ctx, first, 0, 1, 2)
    teach(ctx, second, 0, 1)

    run(ctx.url, period)

    found = {str(line.class_session_id): line for line in lines(ctx.url, period)}
    assert [
        (found[first.id_of(i)].amount, found[first.id_of(i)].cumulative_sessions)
        for i in range(3)
    ] == [
        (Decimal("333.33"), 1),
        (Decimal("333.34"), 2),
        (Decimal("333.33"), 3),
    ]
    assert [
        (found[second.id_of(i)].amount, found[second.id_of(i)].cumulative_sessions)
        for i in range(2)
    ] == [(Decimal("500.00"), 1), (Decimal("500.00"), 2)]
    assert {found[first.id_of(i)].cohort_sessions for i in range(3)} == {3}
    assert {found[second.id_of(i)].cohort_sessions for i in range(2)} == {2}


def test_m7_the_first_session_date_is_the_center_date(ctx):
    """18:30 in Mexico City is 00:30 UTC the next day."""
    cohort = make_cohort(ctx, 2, start_time="18:30")
    period = on_payroll(ctx)
    add_rate(
        ctx.url, ctx.tutor.id, day(ctx, 1), "3000", kind="course", model="per_course"
    )
    teach(ctx, cohort, 0, 1)

    run(ctx.url, period)

    assert amounts(ctx.url, period) == cents("500.00", "500.00")


def test_m8_a_session_on_the_periods_last_day_is_in_it(ctx):
    cohort = make_cohort(ctx, 3)
    add_terms(ctx.url, ctx.tutor.id)
    add_rate(ctx.url, ctx.tutor.id, rate="1000", kind="course", model="per_course")
    first = period_of(ctx, ctx.tutor, 0, 3)  # ends on the first Wednesday
    later = period_of(ctx, ctx.tutor, 3, 60)
    teach(ctx, cohort, 0, 1, 2)

    run(ctx.url, first)
    run(ctx.url, later)

    assert by_order(ctx.url, first) == [cohort.id_of(0), cohort.id_of(1)]
    assert amounts(ctx.url, first) == cents("333.33", "333.34")
    assert by_order(ctx.url, later) == [cohort.id_of(2)]


# --- G6: any order -----


def test_g6_two_open_periods_give_the_same_lines_in_either_order(ctx):
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1, 2)

    run(ctx.url, second)
    run(ctx.url, first)
    b_then_a = stable(lines(ctx.url, first)), stable(lines(ctx.url, second))
    run_sql(
        ctx.url,
        "DELETE FROM payroll_lines WHERE tutor_user_id = :t",
        {"t": ctx.tutor.id},
    )
    run(ctx.url, first)
    run(ctx.url, second)
    a_then_b = stable(lines(ctx.url, first)), stable(lines(ctx.url, second))

    assert b_then_a == a_then_b
    assert amounts(ctx.url, first) == cents("333.33", "333.34")
    assert amounts(ctx.url, second) == cents("333.33")
    run(ctx.url, first)
    assert (stable(lines(ctx.url, first)), stable(lines(ctx.url, second))) == a_then_b


def test_a_prepared_earlier_period_is_walked_like_an_open_one(ctx):
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1, 2)
    run(ctx.url, first)
    run_sql(
        ctx.url,
        "UPDATE payroll_periods SET status = 'prepared', prepared_at = now(),"
        " prepared_by_user_id = :by WHERE id = :id",
        {"id": first, "by": ctx.owner.id},
    )

    run(ctx.url, second)

    assert shares(ctx.url, second) == [(3, 3)]


# --- G7: substitutes -----


def sub_in(ctx: Ctx, session_id: str, substitute: CreatedUser) -> dict[str, Any]:
    cookie = as_user(ctx, ctx.owner, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        item = substituted(ctx.client, cookie, read(ctx, session_id), substitute.id)
    answer = as_user(ctx, substitute, ctx.tl.now)
    with frozen_clock(ctx.tl.now):
        answered = post(ctx.client, answer, f"{OFFERS}/{item['offer_id']}/accept", None)
    assert code_of(answered) == OK, answered.text
    return read(ctx, session_id)


def test_g7_a_per_course_substitute_earns_by_their_own_k(ctx):
    cohort = make_cohort(ctx, 3)
    substitute = new_tutor_user(ctx.url, qualified_for=(ctx.subject,))
    period = on_payroll(ctx)
    their_period = on_payroll(ctx, substitute, "600")
    updated = sub_in(ctx, cohort.id_of(1), substitute)
    teach(ctx, cohort, 0, 2)
    closed_out(ctx, updated, tutor=substitute, minutes=60)

    run(ctx.url, period)
    run(ctx.url, their_period)

    # Own k: 600 x 1/3 = 200.00. The original tutor's k skips session 2.
    assert amounts(ctx.url, their_period) == cents("200.00")
    assert shares(ctx.url, their_period) == [(1, 3)]
    assert amounts(ctx.url, period) == cents("333.33", "333.34")
    assert shares(ctx.url, period) == [(1, 3), (2, 3)]
    assert by_order(ctx.url, period) == [cohort.id_of(0), cohort.id_of(2)]


def test_g7_an_hourly_substitute_is_paid_hourly(ctx):
    cohort = make_cohort(ctx, 3)
    substitute = new_tutor_user(ctx.url, qualified_for=(ctx.subject,))
    their_period = on_payroll(ctx, substitute, "120", model="per_hour")
    updated = sub_in(ctx, cohort.id_of(1), substitute)
    closed_out(ctx, updated, tutor=substitute, minutes=45)

    run(ctx.url, their_period)

    # 120 x 45 / 60
    assert amounts(ctx.url, their_period) == cents("90.00")
    assert shares(ctx.url, their_period) == [(None, None)]


# --- G8, D2, D3: pay events -----


def test_g8_a_no_show_at_50_percent_then_an_ordinary_session(ctx):
    add_rule(ctx.url, "no_show", "2025-01-01", "percent", percent="50")
    cohort = make_cohort(ctx, 3)
    period = on_payroll(ctx)
    closed_out(ctx, cohort.session(0), status="absent", minutes=60)
    teach(ctx, cohort, 1)

    run(ctx.url, period)

    no_show, ordinary = lines(ctx.url, period)
    # 1000 x 1/3 x 50% = 166.67; then 666.67 - 333.33 = 333.34, no catch-up.
    assert (no_show.kind, no_show.amount) == ("no_show", Decimal("166.67"))
    assert no_show.session_pay_rule_id is not None
    assert (ordinary.kind, ordinary.amount) == ("session", Decimal("333.34"))
    assert (ordinary.session_pay_rule_id, ordinary.cumulative_sessions) == (None, 2)


def test_d3_a_course_session_has_no_family_request_and_no_makeup(
    module_seeded_url, client
):
    scene: Scene = make_scene(module_seeded_url, client)
    ctx = scene.ctx
    cohort = make_cohort(ctx, 2)
    session_id = cohort.id_of(0)

    response = create(scene, scene.guardian, session_id=session_id)
    assert code_of(response) == (409, "change_request_kind_unsupported")

    answered = cancel(ctx, session_id, cohort.session(0)["version"])
    assert code_of(answered) == OK, answered.text
    assert refused(book(scene, session_id)) == ["makeup_target_not_individual"]

    period = on_payroll(ctx)
    run(ctx.url, period)
    assert lines(ctx.url, period) == []


# --- G9: late approvals -----


def test_g9_approved_after_its_period_finalized_is_not_walked(ctx):
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0)
    run(ctx.url, first)
    finalize(ctx.url, first, ctx.owner.id)
    teach(ctx, cohort, 1)  # same week, its period is finalized: for #111
    teach(ctx, cohort, 2)

    run(ctx.url, second)

    assert amounts(ctx.url, first) == cents("333.33")
    assert by_order(ctx.url, second) == [cohort.id_of(2)]
    # k skips the late session: 666.67 - 333.33.
    assert shares(ctx.url, second) == [(2, 3)]
    assert amounts(ctx.url, second) == cents("333.34")


def test_a_period_finalized_between_the_reads_counts_once(ctx, monkeypatch):
    """The dates were read before the finalize, the lines after: the sessions have
    both an approval and a finalized line. The line wins, at its stored ``N``."""
    cohort = make_cohort(ctx, 4)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1)  # 1000 x 2/4 = 500.00
    run(ctx.url, first)
    finalize(ctx.url, first, ctx.owner.id)
    answered = cancel(ctx, cohort.id_of(2), cohort.session(2)["version"])
    assert code_of(answered) == OK, answered.text
    teach(ctx, cohort, 3)

    async def before_the_finalize(_session: Any, _tutor: Any) -> list[Any]:
        return []

    monkeypatch.setattr(payroll, "finalized_period_dates", before_the_finalize)

    run(ctx.url, second)

    # 1000 x 3/3 - the stored 500.00. Rewalked at N = 3 it would be 333.33.
    assert shares(ctx.url, second) == [(3, 3)]
    assert amounts(ctx.url, second) == cents("500.00")


# --- Races: two periods, and no lock on scheduling (#107 F5) -----


def test_two_periods_of_a_tutor_run_at_once_with_the_lines_of_one_order(ctx):
    cohort = make_cohort(ctx, 3)
    first, second = two_periods(ctx)
    teach(ctx, cohort, 0, 1, 2)
    run(ctx.url, first)
    run(ctx.url, second)
    expected = stable(lines(ctx.url, first)), stable(lines(ctx.url, second))

    async def both() -> list[Any]:
        engine = short_lived_engine(ctx.url)
        sessions = create_sessionmaker(engine)
        barrier = asyncio.Barrier(2)

        async def one(period: uuid.UUID) -> Any:
            await barrier.wait()
            async with UnitOfWork(sessions) as uow:
                return await payroll_calculation.recalculate(uow.session, period)

        try:
            return list(
                await asyncio.wait_for(
                    asyncio.gather(one(first), one(second), return_exceptions=True),
                    WAIT_LIMIT,
                )
            )
        finally:
            await engine.dispose()

    results = asyncio.run(both())

    assert [result.line_count for result in results] == [2, 1]
    assert (stable(lines(ctx.url, first)), stable(lines(ctx.url, second))) == expected


def test_a_run_holding_its_locks_doesnt_block_a_cohort_cancellation(ctx):
    cohort = make_cohort(ctx, 3)
    period = on_payroll(ctx)
    teach(ctx, cohort, 0)

    async def during_a_run(session: AsyncSession) -> Any:
        await payroll_calculation.recalculate(session, period)
        return await asyncio.wait_for(
            asyncio.to_thread(
                cancel, ctx, cohort.id_of(2), cohort.session(2)["version"]
            ),
            WAIT_LIMIT,
        )

    answered = in_uow(ctx.url, during_a_run)

    assert code_of(answered) == OK, answered.text
    assert shares(ctx.url, period) == [(1, 3)]  # read before the cancellation
    run(ctx.url, period)
    assert shares(ctx.url, period) == [(1, 2)]


# --- A: the readers -----


def test_a1_every_state_oldest_first_and_only_these_cohorts(ctx):
    cohort = make_cohort(ctx, 3)
    other = make_cohort(ctx, 2, start_time="18:00", enrolled=False)
    answered = cancel(ctx, cohort.id_of(1), cohort.session(1)["version"])
    assert code_of(answered) == OK, answered.text

    async def read_them(session: AsyncSession) -> Any:
        return await scheduling.cohort_sessions(session, [uuid.UUID(cohort.id)])

    found = in_uow(ctx.url, read_them)

    assert [str(row.class_session_id) for row in found] == [
        cohort.id_of(0),
        cohort.id_of(1),
        cohort.id_of(2),
    ]
    assert [row.state for row in found] == ["confirmed", "cancelled", "confirmed"]
    assert {str(row.cohort_id) for row in found} == {cohort.id}
    assert other.id not in {str(row.cohort_id) for row in found}


def test_a2_another_tutors_approval_of_the_same_session_isnt_read(ctx):
    cohort = make_cohort(ctx, 2)
    stranger = create_user(ctx.url, roles=(TUTOR,))
    teach(ctx, cohort, 0)
    ids = [uuid.UUID(cohort.id_of(0)), uuid.UUID(cohort.id_of(1))]

    async def read_them(session: AsyncSession) -> Any:
        return (
            await academics.current_approvals_for_sessions(session, ctx.tutor.id, ids),
            await academics.current_approvals_for_sessions(session, stranger.id, ids),
        )

    own, theirs = in_uow(ctx.url, read_them)

    assert [str(row.class_session_id) for row in own] == [cohort.id_of(0)]
    assert theirs == []


# --- F1: the statement pin -----


def _stored(ctx: Ctx, cohort: Cohort, tutor: CreatedUser) -> None:
    for session in cohort.sessions:
        _store_closeout(
            ctx.url,
            Lesson(id=session["id"], tutor=tutor, students=[ctx.student]),
            ctx.owner,
        )


def test_f1_a_run_costs_the_same_whatever_the_sessions_and_cohorts(ctx):
    url = ctx.url
    other = new_tutor_user(url, qualified_for=(ctx.subject,))
    every_day = [1, 2, 3, 4, 5]
    few_sessions = make_cohort(ctx, 3, tutor=other, enrolled=False)
    many = [
        make_cohort(ctx, 15, start_time=start, enrolled=False, weekdays=every_day)
        for start in ("17:00", "18:00")
    ]
    _stored(ctx, few_sessions, other)
    for cohort in many:
        _stored(ctx, cohort, ctx.tutor)
    period_few = on_payroll(ctx, other)
    period_many = on_payroll(ctx)

    few, first = count_statements(
        url, lambda s: payroll_calculation.recalculate(s, period_few)
    )
    most, second = count_statements(
        url, lambda s: payroll_calculation.recalculate(s, period_many)
    )

    assert (first.line_count, second.line_count) == (3, 30)
    assert few == most == 15  # sixteen with the audit insert at commit


# --- Routes -----


def test_the_route_shows_the_share_and_the_total(ctx):
    cohort = make_cohort(ctx, 3)
    period = on_payroll(ctx)
    teach(ctx, cohort, 0, 1, 2)
    finance = create_user(ctx.url, roles=(FINANCE_STAFF,))
    cookie = as_user(ctx, finance, ctx.tl.now)

    with frozen_clock(ctx.tl.now):
        ran = post(
            ctx.client, cookie, f"/api/v1/payroll/periods/{period}/recalculate", None
        )
        read_back = get(ctx.client, cookie, f"/api/v1/payroll/periods/{period}/lines")

    assert code_of(ran) == OK, ran.text
    assert ran.json() == {"line_count": 3, "total": "1000.00"}
    body = read_back.json()
    assert body["total"] == "1000.00"
    assert [
        (line["amount"], line["cumulative_sessions"], line["cohort_sessions"])
        for line in body["lines"]
    ] == [("333.33", 1, 3), ("333.34", 2, 3), ("333.33", 3, 3)]
    assert {line["rate"] for line in body["lines"]} == {"1000.0000"}
    assert {line["rate_model"] for line in body["lines"]} == {"per_course"}


def test_the_share_ignores_the_minutes_and_the_line_keeps_them(ctx):
    cohort = make_cohort(ctx, 2)
    period = on_payroll(ctx)
    closed_out(ctx, cohort.session(0), minutes=30)
    run(ctx.url, period)

    [line] = lines(ctx.url, period)
    assert (line.minutes, line.amount) == (30, Decimal("500.00"))
