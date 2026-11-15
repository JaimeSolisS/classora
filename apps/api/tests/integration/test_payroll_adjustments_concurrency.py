"""Races of the adjustment job on real connections (#111 H, D2, D4; data-model
§15). As in ``test_payroll_workflow_concurrency.py``: a waiting statement is
detected in ``pg_stat_activity``, never timed, every wait is capped, and the
holder is paused **inside** its transaction, after its write and before its
commit. The ``queueing_lock`` is bypassed by calling :func:`post_one` twice.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll import rates as rate_rows
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import CompensationRateInUseError
from app.workflows import payroll_adjustments
from app.workflows.payroll_adjustments import Candidate
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.corrections_support import Staff, staff
from tests.integration.lifecycle_support import in_uow
from tests.integration.payroll_calculation_support import (
    add_period_row,
    add_terms,
    approved_lesson,
    clear,
    lines,
    no_cohorts,
    paid_tutor,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    code,
    locked,
    prepare,
    status_of,
)
from tests.integration.test_attendance import lesson
from tests.integration.test_closeout_review import correct_closeout, version_of
from tests.integration.test_payroll_workflow_concurrency import (
    WAIT_LIMIT,
    finalizing,
    overlap,
    preparing,
    waiting_on_a_lock,
)

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(autouse=True)
def at_now(frozen: FrozenClock) -> Iterator[None]:
    frozen.set(NOW)
    yield
    frozen.set(NOW)


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    set_floor(module_seeded_url, "10-01")
    yield
    clear(module_seeded_url)
    set_floor(module_seeded_url, None)


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


# --- Helpers -----


def selected(url: str, tutor: CreatedUser) -> tuple[date, str, date, Candidate]:
    """The run's facts and the tutor's one candidate."""

    async def read(session: AsyncSession) -> Any:
        zone = await administration.get_center_time_zone(session)
        floor = await payroll.payroll_starts_on(session)
        assert floor is not None
        today = await administration.center_today(session)
        selection = await payroll_adjustments.select_candidates(
            session, floor=floor, zone=zone
        )
        [candidate] = [c for c in selection.to_post if c.tutor_user_id == tutor.id]
        return floor, zone, today, candidate

    found: tuple[date, str, date, Candidate] = in_uow(url, read)
    return found


def posting(url: str, tutor: CreatedUser) -> Any:
    floor, zone, today, candidate = selected(url, tutor)

    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll_adjustments.post_one(
            session, candidate, floor=floor, zone=zone, today=today
        )

    return call


def adjustments(url: str, tutor: CreatedUser) -> list[Any]:
    return run_sql(
        url,
        "SELECT * FROM payroll_adjustments WHERE tutor_user_id = :t"
        " ORDER BY created_at, id",
        {"t": tutor.id},
    )


def late(
    url: str,
    client: TestClient,
    owner: Staff,
    finance: Staff,
    tutor: CreatedUser,
    *,
    later_periods: int = 1,
) -> tuple[Any, list[uuid.UUID]]:
    """The first week finalized with its closeout unapproved, then approved;
    ``later_periods`` open weeks after it."""
    periods = [paid_tutor(url, tutor.id)]
    for index in range(later_periods):
        start = 9 + 7 * index
        periods.append(
            add_period_row(
                url, tutor.id, f"11-{start:02d}", f"11-{start + 6:02d}", first=False
            )
        )
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    locked(client, url, finance, owner, periods[0])
    approved_lesson(client, made, owner.user)
    return made, periods


def session_total(url: str, session_id: Any) -> Decimal:
    [row] = run_sql(
        url,
        "SELECT COALESCE((SELECT sum(amount) FROM payroll_lines WHERE"
        " class_session_id = :s), 0) + COALESCE((SELECT sum(amount) FROM"
        " payroll_adjustments WHERE class_session_id = :s), 0) AS total",
        {"s": session_id},
    )
    return Decimal(row.total)


# --- H1: two runs, one row -----


def test_h1_two_runs_at_once_post_one_row(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    late(url, client, owner, finance, tutor)
    step = posting(url, tutor)

    results = overlap(url, monkeypatch, payroll, "post_adjustment", step, step)

    assert results == ["posted", "nothing"]
    assert len(adjustments(url, tutor)) == 1


def test_h1_a_first_payment_written_meanwhile_wins_on_the_index(
    module_seeded_url, client, owner, finance, tutor
):
    """A person's first payment (M7-08c's ``manual`` naming the session, by SQL)
    is uncommitted when the run re-reads, into another period, so the run's lock
    can't see it: only ``uq_payroll_adjustments_first_payment`` stops the second
    payment. The run waits on the index, then counts it as done."""
    url = module_seeded_url
    made, periods = late(url, client, owner, finance, tutor, later_periods=2)
    step = posting(url, tutor)

    async def _run() -> Any:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)

        async def run(call: Any) -> Any:
            async with UnitOfWork(sessions) as uow:
                return await call(uow.session)

        try:
            async with UnitOfWork(sessions) as person:
                await person.session.execute(
                    text(
                        "INSERT INTO payroll_adjustments (kind, tutor_user_id,"
                        " target_period_id, amount, reason, created_by_user_id,"
                        " class_session_id) VALUES ('manual', :t, :p, 110,"
                        " 'Pagado a mano', :o, :s)"
                    ),
                    {
                        "t": tutor.id,
                        "p": periods[2],
                        "o": owner.user.id,
                        "s": made.id,
                    },
                )
                task = asyncio.create_task(run(step))
                await waiting_on_a_lock(sessions)
            return await asyncio.wait_for(task, WAIT_LIMIT)
        finally:
            await engine.dispose()

    assert asyncio.run(_run()) == "already"
    [only] = adjustments(url, tutor)
    assert only.kind == "manual"
    assert [line.kind for line in lines(url, periods[1])] == []


# --- H2: a run and a re-approval -----


def test_h2_a_run_and_a_re_approval_sum_to_the_newest_revision(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    body = approved_lesson(client, made, owner.user, minutes=90)
    locked(client, url, finance, owner, first)
    response = correct_closeout(
        client, owner.cookie, made.id, version_of(body), actual_minutes=60
    )
    version = version_of(response.json())
    step = posting(url, tutor)

    def reapprove() -> None:
        again = correct_closeout(
            client, owner.cookie, made.id, version, actual_minutes=75
        )
        assert again.status_code == 200, again.text

    async def newer(session: AsyncSession) -> Any:
        # The re-approval waits for the first run (the adjustment's foreign keys
        # hold its session and revision rows FOR KEY SHARE, and the correction
        # takes the session FOR UPDATE), then commits; this run then prices the
        # newer revision on what the first one paid.
        await asyncio.to_thread(reapprove)
        return await step(session)

    results = overlap(url, monkeypatch, payroll, "post_adjustment", step, newer)

    assert results == ["posted", "posted"]
    assert [a.amount for a in adjustments(url, tutor)] == [
        Decimal("-60.00"),
        Decimal("30.00"),
    ]
    assert session_total(url, made.id) == Decimal("150.00")


# --- H3: a run and a rate delete -----


def rate_of(url: str, tutor: CreatedUser) -> uuid.UUID:
    [row] = run_sql(
        url,
        "SELECT id FROM compensation_rates WHERE tutor_user_id = :t",
        {"t": tutor.id},
    )
    return uuid.UUID(str(row.id))


def deleting(tutor: CreatedUser, rate: uuid.UUID) -> Any:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return rate_rows.delete_compensation_rate(
            session, tutor.id, rate, first_session_dates=no_cohorts
        )

    return call


def test_h3_a_delete_after_the_run_is_refused_by_the_snapshot(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    late(url, client, owner, finance, tutor)

    results = overlap(
        url,
        monkeypatch,
        payroll,
        "post_adjustment",
        posting(url, tutor),
        deleting(tutor, rate_of(url, tutor)),
    )

    assert results[0] == "posted"
    assert isinstance(results[1], CompensationRateInUseError)
    assert len(adjustments(url, tutor)) == 1


def test_h3_a_run_after_the_delete_sees_no_basis(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    late(url, client, owner, finance, tutor)
    step = posting(url, tutor)

    results = overlap(
        url,
        monkeypatch,
        rate_rows,
        "delete_lines_of_rate",
        deleting(tutor, rate_of(url, tutor)),
        step,
    )

    assert results == [None, "nothing"]
    assert adjustments(url, tutor) == []


# --- H4: a run and the finalize of an earlier period -----


@pytest.mark.parametrize("run_first", [True, False])
def test_h4_a_run_and_an_earlier_finalize_never_wait_on_each_other(
    module_seeded_url, client, owner, finance, tutor, monkeypatch, run_first
):
    url = module_seeded_url
    _, periods = late(url, client, owner, finance, tutor, later_periods=2)
    assert code(prepare(client, finance, periods[1])) == OK
    run_step = posting(url, tutor)
    finalize_step = finalizing(url, owner, periods[1])

    if run_first:
        results = overlap(
            url,
            monkeypatch,
            payroll,
            "post_adjustment",
            run_step,
            finalize_step,
            waits=False,
        )
        posted, finalized = results
    else:
        results = overlap(
            url,
            monkeypatch,
            payroll,
            "mark_finalized",
            finalize_step,
            run_step,
            waits=False,
        )
        finalized, posted = results

    assert posted == "posted"
    assert finalized.status == "finalized"
    [adjustment] = adjustments(url, tutor)
    assert adjustment.target_period_id == periods[2]


# --- D2: a run and prepare of the target -----


def test_d2_a_run_first_puts_the_adjustment_into_the_prepared_lines(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    _, periods = late(url, client, owner, finance, tutor)

    results = overlap(
        url,
        monkeypatch,
        payroll,
        "post_adjustment",
        posting(url, tutor),
        preparing(finance, periods[1]),
    )

    assert results[0] == "posted"
    assert results[1].status == "prepared"
    [line] = lines(url, periods[1])
    assert (line.kind, line.amount) == ("adjustment", Decimal("110.00"))


def test_d2_a_prepare_first_sends_the_adjustment_to_the_next_open_period(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    _, periods = late(url, client, owner, finance, tutor, later_periods=2)

    results = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_prepared",
        preparing(finance, periods[1]),
        posting(url, tutor),
    )

    assert results[0].status == "prepared"
    assert results[1] == "posted"
    assert lines(url, periods[1]) == []
    [adjustment] = adjustments(url, tutor)
    assert adjustment.target_period_id == periods[2]
    assert status_of(url, periods[1]) == "prepared"


# --- D4: a leaver's run and a terms write -----


def test_d4_a_leavers_run_and_a_terms_write_take_turns(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    set_floor(url, "11-02")
    first = paid_tutor(url, tutor.id)
    add_terms(url, tutor.id, "11-09", frequency=None)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    locked(client, url, finance, owner, first)
    approved_lesson(client, made, owner.user)

    def returning(session: AsyncSession) -> Awaitable[Any]:
        return rate_rows.add_pay_terms(
            session,
            tutor.id,
            effective_from=date(2026, 11, 12),
            pay_frequency="monthly",
            salary_amount=None,
        )

    results = overlap(
        url, monkeypatch, payroll, "post_adjustment", posting(url, tutor), returning
    )

    assert results[0] == "posted"
    # The terms write waited for the period the run opened, and saw it.
    assert type(results[1]).__name__ == "EffectiveFromInsideGeneratedPeriodError"
    [adjustment] = adjustments(url, tutor)
    assert status_of(url, adjustment.target_period_id) == "open"
