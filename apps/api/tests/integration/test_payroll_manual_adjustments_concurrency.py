"""Races of the manual adjustment and the carry-forward, on real connections
(M7-08c E3, F1-F5; data-model §15). As in ``test_payroll_workflow_concurrency``:
a waiting statement is detected in ``pg_stat_activity``, never timed, every wait
is capped, and the holder is paused **inside** its transaction, after its write
and before its commit (``overlap``).
"""

import asyncio
import uuid
from collections.abc import Awaitable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import create_sessionmaker
from app.core.idempotency import REPLAYED_HEADER
from app.core.money import Money
from app.modules.administration import service as administration
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import (
    PayrollPeriodNotOpenError,
    PayrollWorkNotUnresolvedError,
    PayrollWorkSettledError,
)
from app.modules.payroll.schemas import PayrollAdjustmentCreate
from app.workflows import (
    payroll_adjustments,
    payroll_calculation,
    payroll_manual_adjustments,
)
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import run_sql_async, short_lived_engine
from tests.integration.corrections_support import Staff, staff
from tests.integration.lifecycle_support import in_uow
from tests.integration.payroll_calculation_support import (
    add_period_row,
    add_rate,
    approved_lesson,
    clear,
    lines,
    paid_tutor,
)
from tests.integration.payroll_manual_support import (
    CREATED,
    REASON,
    SETTLED,
    adjustments,
    manual,
    period_total,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    code,
    locked,
    new_key,
    prepare,
    status_of,
)
from tests.integration.test_attendance import lesson, principal_of
from tests.integration.test_payroll_workflow_concurrency import (
    POLLS,
    finalizing,
    overlap,
    preparing,
)

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
CROWD = 8


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


def weeks(url: str, tutor: CreatedUser, *, rate: str | None) -> list[uuid.UUID]:
    """The week of ``STARTS`` and the two after it."""
    return [
        paid_tutor(url, tutor.id, rate=rate),
        add_period_row(url, tutor.id, "11-09", "11-15", first=False),
        add_period_row(url, tutor.id, "11-16", "11-22", first=False),
    ]


def late(
    url: str,
    client: TestClient,
    owner: Staff,
    finance: Staff,
    tutor: CreatedUser,
    *,
    rate: str | None,
) -> tuple[Any, list[uuid.UUID]]:
    """The first week finalized while its closeout was unapproved, then
    approved: unresolved with no rate, or the job's with one."""
    periods = weeks(url, tutor, rate=rate)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    locked(client, url, finance, owner, periods[0])
    approved_lesson(client, made, owner.user, minutes=60)
    return made, periods


def resolving(by: Staff, tutor: CreatedUser, session_id: Any) -> Any:
    principal = principal_of(by.user.id, OWNER)
    body = PayrollAdjustmentCreate(
        amount=Money("100.00"), reason=REASON, class_session_id=session_id
    )

    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll_manual_adjustments.post_manual(
            session, principal, tutor.id, body
        )

    return call


def free_manual(by: Staff, tutor: CreatedUser, amount: str = "-500.00") -> Any:
    principal = principal_of(by.user.id, OWNER)
    body = PayrollAdjustmentCreate(amount=Money(amount), reason=REASON)

    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll_manual_adjustments.post_manual(
            session, principal, tutor.id, body
        )

    return call


def carrying(by: Staff, period: uuid.UUID) -> Any:
    principal = principal_of(by.user.id, FINANCE_STAFF)

    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll_manual_adjustments.carry_forward(session, principal, period)

    return call


async def _facts(session: AsyncSession) -> tuple[date, str, date]:
    floor = await payroll.payroll_starts_on(session)
    assert floor is not None
    zone = await administration.get_center_time_zone(session)
    return floor, zone, await administration.center_today(session)


def job_step(url: str, tutor: CreatedUser, *, rate_first: bool = False) -> Any:
    """The job's selection and one post, in the step's session. With
    ``rate_first``, the tutor's rate is inserted (committed) just before: a
    basis appearing meanwhile."""

    async def call(session: AsyncSession) -> Any:
        if rate_first:
            await run_sql_async(
                url,
                "INSERT INTO compensation_rates (tutor_user_id, class_kind,"
                " effective_from, rate_model, rate) VALUES (:u, 'individual',"
                " '2000-01-01', 'per_hour', 120)",
                {"u": tutor.id},
            )
        floor, zone, today = await _facts(session)
        selection = await payroll_adjustments.select_candidates(
            session, floor=floor, zone=zone
        )
        [candidate] = [c for c in selection.to_post if c.tutor_user_id == tutor.id]
        return await payroll_adjustments.post_one(
            session, candidate, floor=floor, zone=zone, today=today
        )

    return call


def recalculating(url: str, tutor: CreatedUser, period: uuid.UUID, rate: bool) -> Any:
    async def call(session: AsyncSession) -> Any:
        if rate:
            await run_sql_async(
                url,
                "INSERT INTO compensation_rates (tutor_user_id, class_kind,"
                " effective_from, rate_model, rate) VALUES (:u, 'individual',"
                " '2000-01-01', 'per_hour', 120)",
                {"u": tutor.id},
            )
        return await payroll_calculation.recalculate(session, period)

    return call


def session_paid(url: str, session_id: Any) -> list[tuple[str, Decimal]]:
    """Every payment of the session: its lines and its adjustments."""

    async def read(session: AsyncSession) -> Any:
        rows = await session.execute(
            text(
                "SELECT kind, amount FROM payroll_lines WHERE class_session_id = :s"
                " UNION ALL SELECT kind, amount FROM payroll_adjustments"
                " WHERE class_session_id = :s"
            ),
            {"s": session_id},
        )
        return sorted((row.kind, row.amount) for row in rows)

    found: list[tuple[str, Decimal]] = in_uow(url, read)
    return found


# --- E3: eight at once -----


def crowd(
    url: str,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    send: Any,
    keys: list[str],
) -> list[Any]:
    """Eight requests; the first writer waits, inside its transaction, until the
    seven others wait on a lock (capped), then commits."""
    real = payroll.post_manual_adjustment
    seen: list[int] = []

    async def held(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        if not seen:
            seen.append(0)
            engine = short_lived_engine(url)
            try:
                for _ in range(POLLS):
                    async with create_sessionmaker(engine)() as probe:
                        waiting = await probe.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE"
                                " datname = current_database()"
                                " AND wait_event_type = 'Lock'"
                            )
                        )
                    if waiting >= CROWD - 1:
                        seen.append(waiting)
                        break
                    await asyncio.sleep(0.02)
            finally:
                await engine.dispose()
        return result

    monkeypatch.setattr(payroll, "post_manual_adjustment", held)
    client.cookies.clear()
    with ThreadPoolExecutor(max_workers=CROWD) as pool:
        futures = [pool.submit(send, key) for key in keys]
        responses = [future.result(60) for future in futures]
    assert seen[1:] and seen[1] >= CROWD - 1, "the crowd never waited together"
    return responses


def test_e3_eight_posts_with_one_key_have_one_effect(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    paid_tutor(url, tutor.id)
    key = new_key()

    responses = crowd(
        url,
        client,
        monkeypatch,
        lambda k: manual(client, owner, tutor.id, "50.00", key=k),
        [key] * CROWD,
    )

    assert [code(r) for r in responses] == [CREATED] * CROWD
    replayed = sorted(r.headers.get(REPLAYED_HEADER, "") for r in responses)
    assert replayed == [""] + ["true"] * (CROWD - 1)
    assert all(r.json() == responses[0].json() for r in responses)
    assert len(adjustments(url, tutor.id)) == 1


def test_e3_eight_keys_on_one_resolution_pay_once(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    made, _ = late(url, client, owner, finance, tutor, rate=None)

    responses = crowd(
        url,
        client,
        monkeypatch,
        lambda k: manual(client, owner, tutor.id, key=k, class_session_id=made.id),
        [new_key() for _ in range(CROWD)],
    )

    assert sorted(code(r) for r in responses) == sorted(
        [CREATED] + [SETTLED] * (CROWD - 1)
    )
    assert session_paid(url, made.id) == [("manual", Decimal("100.00"))]


# --- F1: a resolution and the job's late approval -----


def test_f1_a_resolution_first_and_the_job_with_a_new_basis_pay_once(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    made, periods = late(url, client, owner, finance, tutor, rate=None)

    done, job = overlap(
        url,
        monkeypatch,
        payroll,
        "post_manual_adjustment",
        resolving(owner, tutor, made.id),
        job_step(url, tutor, rate_first=True),
    )

    assert done.target_period_id == periods[1]
    assert job == "nothing"
    assert session_paid(url, made.id) == [("manual", Decimal("100.00"))]


def test_f1_the_job_first_makes_the_resolution_settled(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    made, _ = late(url, client, owner, finance, tutor, rate="120")

    job, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "post_adjustment",
        job_step(url, tutor),
        resolving(owner, tutor, made.id),
    )

    assert job == "posted"
    assert isinstance(refused, PayrollWorkSettledError)
    assert session_paid(url, made.id) == [("late_approval", Decimal("120.00"))]


# --- F2: a resolution and a recalculation of the item's period -----


def open_item(
    url: str, client: TestClient, owner: Staff, tutor: CreatedUser
) -> tuple[Any, uuid.UUID]:
    """No rate, a 60-minute closeout in the open first week."""
    periods = weeks(url, tutor, rate=None)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user, minutes=60)
    return made, periods[0]


def test_f2_a_resolution_first_leaves_a_later_run_no_line(
    module_seeded_url, client, owner, tutor, monkeypatch
):
    url = module_seeded_url
    made, first = open_item(url, client, owner, tutor)

    done, run = overlap(
        url,
        monkeypatch,
        payroll,
        "post_manual_adjustment",
        resolving(owner, tutor, made.id),
        recalculating(url, tutor, first, rate=True),
    )

    assert done.target_period_id == first
    assert run.line_count == 1  # the adjustment line only
    assert session_paid(url, made.id) == [("manual", Decimal("100.00"))]


def test_f2_a_run_first_makes_the_resolution_refused(
    module_seeded_url, client, owner, tutor, monkeypatch
):
    url = module_seeded_url
    made, first = open_item(url, client, owner, tutor)
    add_rate(url, tutor.id)

    run, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "replace_lines",
        recalculating(url, tutor, first, rate=False),
        resolving(owner, tutor, made.id),
    )

    assert run.line_count == 1
    assert isinstance(refused, PayrollWorkNotUnresolvedError)
    assert session_paid(url, made.id) == [("session", Decimal("120.00"))]


# --- F3: carry-forward and the job in the same period -----


def short_week_with_late_work(
    url: str, client: TestClient, owner: Staff, finance: Staff, tutor: CreatedUser
) -> list[uuid.UUID]:
    """A late closeout for the job (60 minutes at 120.00), and a ``manual`` of
    ``-500.00`` in the second week."""
    _, periods = late(url, client, owner, finance, tutor, rate="120")
    response = manual(client, owner, tutor.id, "-500.00")
    assert response.json()["target_period_id"] == str(periods[1])
    return periods


def test_f3_a_carry_that_waited_for_the_job_sees_its_line(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    _, second, third = short_week_with_late_work(url, client, owner, finance, tutor)

    job, carried = overlap(
        url,
        monkeypatch,
        payroll,
        "post_adjustment",
        job_step(url, tutor),
        carrying(finance, second),
    )

    assert job == "posted"
    assert carried.target_period_id == third
    assert period_total(url, second) == Decimal("0.00")
    assert period_total(url, third) == Decimal("-380.00")


def test_f3_a_job_that_waited_for_a_carry_posts_after_it(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    _, second, third = short_week_with_late_work(url, client, owner, finance, tutor)

    carried, job = overlap(
        url,
        monkeypatch,
        payroll,
        "post_carry_forward",
        carrying(finance, second),
        job_step(url, tutor),
    )

    assert carried.target_period_id == third
    assert job == "posted"
    # The job's line came after the carry: the week is positive and prepares.
    assert period_total(url, second) == Decimal("120.00")
    assert period_total(url, third) == Decimal("-500.00")
    assert code(prepare(client, finance, second)) == OK


# --- F4: carry-forward and prepare of the same period -----


def test_f4_a_prepare_that_waited_for_a_carry_prepares_at_zero(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    first, second, _ = weeks(url, tutor, rate="120")
    manual(client, owner, tutor.id, "-75.00")

    carried, prepared = overlap(
        url,
        monkeypatch,
        payroll,
        "post_carry_forward",
        carrying(finance, first),
        preparing(finance, first),
    )

    assert (carried.period_id, carried.target_period_id) == (first, second)
    assert prepared.status == "prepared"
    assert period_total(url, first) == Decimal("0.00")


def test_f4_a_carry_that_waited_for_a_prepare_is_refused_by_status(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    first, _, _ = weeks(url, tutor, rate="120")

    prepared, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_prepared",
        preparing(finance, first),
        carrying(finance, first),
    )

    assert prepared.status == "prepared"
    assert isinstance(refused, PayrollPeriodNotOpenError)
    assert refused.details == {"status": "prepared"}
    assert adjustments(url, tutor.id) == []


# --- F5: a manual and the target's prepare or finalize -----


def test_f5_a_manual_that_waited_for_a_prepare_lands_in_the_next_open_week(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    first, second, _ = weeks(url, tutor, rate="120")

    prepared, posted = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_prepared",
        preparing(finance, first),
        free_manual(owner, tutor, "40.00"),
    )

    assert prepared.status == "prepared"
    assert posted.target_period_id == second
    assert lines(url, first) == []


def test_f5_a_prepare_that_waited_for_a_manual_holds_its_line(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    first, _, _ = weeks(url, tutor, rate="120")

    posted, prepared = overlap(
        url,
        monkeypatch,
        payroll,
        "post_manual_adjustment",
        free_manual(owner, tutor, "40.00"),
        preparing(finance, first),
    )

    assert posted.target_period_id == first
    assert prepared.status == "prepared"
    assert [line.kind for line in lines(url, first)] == ["adjustment"]


def test_f5_a_manual_that_waited_for_a_finalize_lands_in_an_open_week(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    first, second, _ = weeks(url, tutor, rate="120")
    assert code(prepare(client, finance, first)) == OK

    finalized, posted = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_finalized",
        finalizing(url, owner, first),
        free_manual(owner, tutor, "40.00"),
    )

    assert finalized.status == "finalized"
    assert posted.target_period_id == second
    assert status_of(url, second) == "open"
    assert lines(url, first) == []
