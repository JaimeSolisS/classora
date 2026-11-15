"""Races of the money lock, on real connections (#110 M, F5, H9, L5; data-model
§15). A waiting statement is detected in ``pg_stat_activity``, never timed, and
every wait is capped. The holder is paused **inside** the workflow, after its
write and before its commit.
"""

import asyncio
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import create_sessionmaker
from app.core.idempotency import REPLAYED_HEADER
from app.core.money import Rate
from app.core.pagination import OffsetParams
from app.core.uow import UnitOfWork
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll import pay_items as pay_item_rows
from app.modules.payroll import rates as rate_rows
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import (
    EffectiveFromUnderFinalizedLineError,
    PayItemEndBeforePaidPeriodError,
    PayItemInUseError,
    PayrollPeriodNotOpenError,
    PayrollPeriodNotPreparedError,
    PayrollPeriodOutOfOrderError,
    PayrollPeriodStaleError,
    PayrollReviewOutdatedError,
)
from app.modules.payroll.schemas import PayItemUpdate
from app.workflows import payroll_calculation, payroll_workflow
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.corrections_support import Staff, staff
from tests.integration.lifecycle_support import in_uow
from tests.integration.payroll_calculation_support import (
    add_item,
    add_pay_type,
    add_period_row,
    add_rate,
    add_terms,
    approved_lesson,
    clear,
    lines,
    paid_tutor,
    stable,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    ROOT,
    actions,
    code,
    new_key,
    notifications_of,
    prepare,
    prepared_at,
    request_of,
    return_period,
    row_of,
    status_of,
)
from tests.integration.test_attendance import lesson, principal_of
from tests.integration.test_payroll_calculation import _store_closeout

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
WAIT_LIMIT = 15.0
POLLS = 750

type Step = Callable[[AsyncSession], Awaitable[Any]]


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


async def waiting_on_a_lock(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Returns once another backend of this database waits on a lock. Capped at
    :data:`POLLS` polls."""
    for _ in range(POLLS):
        async with sessions() as probe:
            count = await probe.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database()"
                    " AND wait_event_type = 'Lock'"
                )
            )
        if count:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("nothing waited on a lock")


def overlap(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
    owner_of_step: Any,
    step: str,
    first: Step,
    second: Step,
    *,
    waits: bool = True,
) -> list[Any]:
    """``first`` stops right after ``step``, inside its transaction; ``second``
    starts, and (with ``waits``) must wait on a lock, or (without) must finish;
    ``first`` goes on and commits. Both results (an exception is a result)."""

    async def _run() -> list[Any]:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        reached, resume = asyncio.Event(), asyncio.Event()
        real = getattr(owner_of_step, step)

        async def paused(*args: Any, **kwargs: Any) -> Any:
            result = await real(*args, **kwargs)
            if not reached.is_set():
                reached.set()
                await asyncio.wait_for(resume.wait(), WAIT_LIMIT)
            return result

        monkeypatch.setattr(owner_of_step, step, paused)

        async def run(call: Step) -> Any:
            async with UnitOfWork(sessions) as uow:
                return await call(uow.session)

        try:
            running = asyncio.create_task(run(first))
            await asyncio.wait_for(reached.wait(), WAIT_LIMIT)
            other = asyncio.create_task(run(second))
            try:
                if waits:
                    await waiting_on_a_lock(sessions)
                else:
                    finished, _ = await asyncio.wait({other}, timeout=WAIT_LIMIT)
                    assert finished, "the second step waited"
            finally:
                resume.set()
            return list(
                await asyncio.wait_for(
                    asyncio.gather(running, other, return_exceptions=True),
                    WAIT_LIMIT,
                )
            )
        finally:
            monkeypatch.setattr(owner_of_step, step, real)
            await engine.dispose()

    return asyncio.run(_run())


def finalizing(url: str, by: Staff, period: uuid.UUID, at: str | None = None) -> Step:
    review = datetime.fromisoformat(at or prepared_at(url, period) or "")
    principal = principal_of(by.user.id, OWNER)

    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll_workflow.finalize(session, principal, period, review)

    return call


def returning(by: Staff, period: uuid.UUID) -> Step:
    principal = principal_of(by.user.id, FINANCE_STAFF)

    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll_workflow.return_period(session, principal, period, "Falta")

    return call


def preparing(by: Staff, period: uuid.UUID) -> Step:
    principal = principal_of(by.user.id, FINANCE_STAFF)

    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll_workflow.prepare(session, principal, period)

    return call


def worked(url: str, client: TestClient, owner: Staff, tutor: CreatedUser) -> Any:
    period = paid_tutor(url, tutor.id)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    return period


def ready(
    url: str, client: TestClient, owner: Staff, finance: Staff, tutor: CreatedUser
) -> Any:
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    return period


# --- M1, M2: two finalizes through the route -----


def two_finalizes(
    url: str,
    client: TestClient,
    who: Staff,
    period: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str, str],
) -> list[Any]:
    """The first holds its transaction after the write; the second starts, then
    the first commits."""
    entered, release = threading.Event(), threading.Event()
    original = payroll.mark_finalized

    async def held(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        if not entered.is_set():
            entered.set()
            await anyio.to_thread.run_sync(release.wait, 10)
        return result

    monkeypatch.setattr(payroll, "mark_finalized", held)
    review = prepared_at(url, period)

    def send(key: str) -> Any:
        client_headers = {**signed_headers(who.cookie), "Idempotency-Key": key}
        return client.post(
            f"{ROOT}/{period}/finalize",
            json={"prepared_at": review},
            headers=client_headers,
        )

    def second() -> Any:
        entered.wait(10)
        threading.Timer(0.5, release.set).start()
        return send(keys[1])

    client.cookies.clear()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(send, keys[0])
        second_future = pool.submit(second)
        return [first_future.result(30), second_future.result(30)]


def one_effect(url: str, period: uuid.UUID, tutor: CreatedUser) -> None:
    assert actions(url, period).count("payroll.period_finalized") == 1
    assert len(notifications_of(url, tutor.id)) == 1
    assert request_of(url, period).status == "approved"
    assert status_of(url, period) == "finalized"


def test_m1_two_finalizes_with_one_key_have_one_effect(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = ready(url, client, owner, finance, tutor)
    key = new_key()

    responses = two_finalizes(url, client, owner, period, monkeypatch, (key, key))

    assert [r.status_code for r in responses] == [200, 200]
    assert sorted(r.headers.get(REPLAYED_HEADER, "") for r in responses) == ["", "true"]
    assert responses[0].json() == responses[1].json()
    one_effect(url, period, tutor)


def test_m2_two_finalizes_with_different_keys_have_one_effect(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = ready(url, client, owner, finance, tutor)

    responses = two_finalizes(
        url, client, owner, period, monkeypatch, (new_key(), new_key())
    )

    assert sorted(code(r) for r in responses) == [
        (200, None),
        (409, "payroll_period_not_prepared"),
    ]
    one_effect(url, period, tutor)


# --- M3: finalize against return and prepare -----


def test_m3_a_return_that_waited_for_a_finalize_sees_it(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = ready(url, client, owner, finance, tutor)

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_finalized",
        finalizing(url, owner, period),
        returning(finance, period),
    )

    assert done.status == "finalized"
    assert isinstance(refused, PayrollPeriodNotPreparedError)
    assert refused.details == {"status": "finalized"}
    assert status_of(url, period) == "finalized"


def test_m3_a_finalize_that_waited_for_a_return_sees_it(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = ready(url, client, owner, finance, tutor)

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_returned",
        returning(finance, period),
        finalizing(url, owner, period),
    )

    assert done.status == "open"
    assert isinstance(refused, PayrollPeriodNotPreparedError)
    assert refused.details == {"status": "open"}
    assert notifications_of(url, tutor.id) == []


def test_m3_a_finalize_that_waited_for_a_new_prepare_is_outdated(
    module_seeded_url, client, frozen, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = ready(url, client, owner, finance, tutor)
    old_review = prepared_at(url, period)
    assert code(return_period(client, finance, period)) == OK
    frozen.set(NOW + timedelta(seconds=1))

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_prepared",
        preparing(finance, period),
        finalizing(url, owner, period, at=old_review),
    )

    assert done.status == "prepared"
    assert isinstance(refused, PayrollReviewOutdatedError)
    assert status_of(url, period) == "prepared"


# --- M4: finalize against the item routes' writes -----


def test_m4_an_item_delete_that_waited_for_a_finalize_is_refused(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    item = add_item(url, tutor.id, add_pay_type(url), "40.00")
    assert code(prepare(client, finance, period)) == OK

    def delete(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_pay_item(session, tutor.id, item)

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_finalized",
        finalizing(url, owner, period),
        delete,
    )

    assert done.status == "finalized"
    assert isinstance(refused, PayItemInUseError)
    assert row_of(url, period).total_amount == Decimal("150.00")


def test_m4_an_item_write_starting_before_finalize_reads_items_doesnt_deadlock(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    """The finalize holds the period and hasn't read the items yet. A write that
    took the item before the periods would hold what the finalize needs while
    waiting for what the finalize has: PostgreSQL would abort one with 40P01."""
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    item = add_item(url, tutor.id, add_pay_type(url), "40.00")
    assert code(prepare(client, finance, period)) == OK

    def delete(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_pay_item(session, tutor.id, item)

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "lock_period",
        finalizing(url, owner, period),
        delete,
    )

    assert not isinstance(done, Exception), done
    assert done.status == "finalized"
    assert isinstance(refused, PayItemInUseError)


def test_m4_a_patch_that_waited_for_a_finalize_is_refused(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    item = add_item(
        url, tutor.id, add_pay_type(url), "40.00", on="10-01", recurrence="recurring"
    )
    assert code(prepare(client, finance, period)) == OK

    def end(session: AsyncSession) -> Awaitable[None]:
        return payroll.end_pay_item(
            session,
            tutor.id,
            item,
            PayItemUpdate(version=1, effective_to=date(2026, 10, 31)),
        )

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_finalized",
        finalizing(url, owner, period),
        end,
    )

    assert done.status == "finalized"
    assert isinstance(refused, PayItemEndBeforePaidPeriodError)


def test_m4_a_finalize_that_waited_for_an_item_write_is_stale(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    item = add_item(
        url, tutor.id, add_pay_type(url), "40.00", on="10-01", recurrence="recurring"
    )
    assert code(prepare(client, finance, period)) == OK
    review = finalizing(url, owner, period)

    def end(session: AsyncSession) -> Awaitable[None]:
        return payroll.end_pay_item(
            session,
            tutor.id,
            item,
            PayItemUpdate(version=1, effective_to=date(2026, 10, 31)),
        )

    done, refused = overlap(
        url,
        monkeypatch,
        pay_item_rows,
        "lock_unfinalized_periods_of_tutor",
        end,
        review,
    )

    assert done is None
    assert isinstance(refused, PayrollPeriodStaleError)
    assert status_of(url, period) == "prepared"


# --- M5: the guard's FOR SHARE, raced by a real finalize -----


def test_m5_a_line_write_that_waited_for_a_finalize_fails_42501(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    """#107 QA O5. The write starts while the period is still ``prepared`` on
    disk. Without the guard's ``FOR SHARE`` it would read that and pass."""
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    item = add_item(url, tutor.id, add_pay_type(url), "40.00")
    assert code(prepare(client, finance, period)) == OK

    async def write_a_line(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE payroll_lines SET amount = 1 WHERE pay_item_id = :i"),
            {"i": item},
        )

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_finalized",
        finalizing(url, owner, period),
        write_a_line,
    )

    assert done.status == "finalized"
    assert isinstance(refused, DBAPIError)
    assert getattr(refused.orig, "sqlstate", None) == "42501"
    assert row_of(url, period).total_amount == Decimal("150.00")
    assert [line.amount for line in lines(url, period) if line.pay_item_id] == [
        Decimal("40.00")
    ]


# --- M6: two periods of one tutor -----


def test_m6_the_later_period_never_finalizes_alone(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    """P1 has written and not committed, so P2 reads it as ``prepared``."""
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    assert code(prepare(client, finance, first)) == OK
    assert code(prepare(client, finance, second)) == OK
    later = finalizing(url, owner, second)

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_finalized",
        finalizing(url, owner, first),
        later,
        waits=False,
    )

    assert done.status == "finalized"
    assert isinstance(refused, PayrollPeriodOutOfOrderError)
    assert status_of(url, second) == "prepared"

    finished = in_uow(url, later)

    assert finished.status == "finalized"


def test_m6_both_at_once_from_a_barrier_leave_no_later_period_alone(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    assert code(prepare(client, finance, first)) == OK
    assert code(prepare(client, finance, second)) == OK
    calls = [finalizing(url, owner, first), finalizing(url, owner, second)]

    async def _both() -> list[Any]:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        barrier = asyncio.Barrier(2)

        async def one(call: Step) -> Any:
            await barrier.wait()
            async with UnitOfWork(sessions) as uow:
                return await call(uow.session)

        try:
            return list(
                await asyncio.wait_for(
                    asyncio.gather(*map(one, calls), return_exceptions=True),
                    WAIT_LIMIT,
                )
            )
        finally:
            await engine.dispose()

    older, newer = asyncio.run(_both())

    assert older.status == "finalized"
    assert (
        isinstance(newer, PayrollPeriodOutOfOrderError) or newer.status == "finalized"
    )


# --- M7: finalize against recalculate -----


def test_m7_a_recalculation_that_waited_for_a_finalize_is_refused(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = ready(url, client, owner, finance, tutor)
    before = stable(lines(url, period))

    def recalculating(session: AsyncSession) -> Awaitable[Any]:
        return payroll_calculation.recalculate(session, period)

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_finalized",
        finalizing(url, owner, period),
        recalculating,
    )

    assert done.status == "finalized"
    assert isinstance(refused, PayrollPeriodNotOpenError)
    assert refused.details == {"status": "finalized"}
    assert stable(lines(url, period)) == before


# --- M8: the statements of finalize -----


def test_m8_finalize_costs_the_same_at_3_and_at_30_lines(
    module_seeded_url, client, owner, finance
):
    url = module_seeded_url

    def built(count: int) -> Any:
        tutor = create_user(url, roles=(TUTOR,))
        add_terms(url, tutor.id, frequency="monthly")
        add_rate(url, tutor.id)
        period = add_period_row(url, tutor.id, "10-01", "11-30", "monthly")
        for index in range(count):
            made = lesson(
                url, starts_at=STARTS + timedelta(hours=2 * index), tutor=tutor
            )
            _store_closeout(url, made, owner.user)
        assert code(prepare(client, finance, period)) == OK
        assert len(lines(url, period)) == count
        return period

    three = built(3)
    thirty = built(30)

    few, _ = count_statements(url, finalizing(url, owner, three))
    many, _ = count_statements(url, finalizing(url, owner, thirty))

    assert few == many


# --- F5: readers take no lock -----


def test_f5_a_read_finishes_while_a_writer_holds_the_period_and_a_rate(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    rate = add_rate(url, tutor.id)
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    assert code(prepare(client, finance, period)) == OK
    principal = principal_of(owner.user.id, OWNER)

    async def _run() -> list[Any]:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        try:
            async with UnitOfWork(sessions) as holder:
                await holder.session.execute(
                    text("SELECT id FROM payroll_periods WHERE id = :p FOR UPDATE"),
                    {"p": period},
                )
                await holder.session.execute(
                    text("SELECT id FROM compensation_rates WHERE id = :r FOR UPDATE"),
                    {"r": rate},
                )

                async def read() -> list[Any]:
                    async with UnitOfWork(sessions) as reader:
                        session = reader.session
                        summary = await payroll.period_row(session, period)
                        return [
                            await payroll_workflow.read_period(
                                session, principal, period
                            ),
                            await payroll_workflow.list_periods(
                                session,
                                principal,
                                page=OffsetParams(limit=25, offset=0),
                                status=None,
                                tutor_user_id=None,
                            ),
                            await payroll_calculation.blocking_work(session, summary),
                        ]

                return await asyncio.wait_for(read(), WAIT_LIMIT)
        finally:
            await engine.dispose()

    review, page, blocking = asyncio.run(_run())

    assert review.total.amount == Decimal("110.00")
    assert page.total == 1
    assert blocking == []


def test_f5_the_period_reads_send_no_for_clause(module_seeded_url, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    principal = principal_of(owner.user.id, OWNER)
    seen: list[str] = []

    def record(conn, cursor, statement, *_: Any) -> None:  # type: ignore[no-untyped-def]
        seen.append(statement)

    async def read(session: AsyncSession) -> None:
        await payroll_workflow.read_period(session, principal, period)
        await payroll_workflow.list_periods(
            session,
            principal,
            page=OffsetParams(limit=25, offset=0),
            status=None,
            tutor_user_id=None,
        )

    event.listen(Engine, "before_cursor_execute", record)
    try:
        in_uow(url, read)
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    assert seen
    assert [statement for statement in seen if " FOR " in statement.upper()] == []


# --- L5: a rate insert can't race a finalize -----


async def no_cohorts(_ids: Any) -> dict[uuid.UUID, date]:
    return {}


def adding_rate(tutor: CreatedUser) -> Step:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll.add_compensation_rate(
            session,
            tutor.id,
            class_kind="individual",
            effective_from=date(2026, 11, 1),
            rate_model="per_hour",
            rate=Rate("150"),
            first_session_dates=no_cohorts,
        )

    return call


def test_l5_a_rate_that_waited_for_a_finalize_is_refused(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = ready(url, client, owner, finance, tutor)

    done, refused = overlap(
        url,
        monkeypatch,
        payroll,
        "mark_finalized",
        finalizing(url, owner, period),
        adding_rate(tutor),
    )

    assert done.status == "finalized"
    assert isinstance(refused, EffectiveFromUnderFinalizedLineError)
    assert (
        run_sql(
            url,
            "SELECT id FROM compensation_rates WHERE tutor_user_id = :u"
            " AND effective_from = '2026-11-01'",
            {"u": tutor.id},
        )
        == []
    )


def test_l5_a_finalize_that_waited_for_a_rate_is_stale(
    module_seeded_url, client, owner, finance, tutor, monkeypatch
):
    url = module_seeded_url
    period = ready(url, client, owner, finance, tutor)
    review = finalizing(url, owner, period)

    added, refused = overlap(
        url,
        monkeypatch,
        rate_rows,
        "lock_unfinalized_periods_of_tutor",
        adding_rate(tutor),
        review,
    )

    assert isinstance(added, uuid.UUID)
    assert isinstance(refused, PayrollPeriodStaleError)
