"""Races of the payroll calculation, on real connections (#107 I2-I5; data-model
§15). A waiting statement is detected in ``pg_stat_activity``, never timed.
"""

import asyncio
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import create_sessionmaker
from app.core.idempotency import REPLAYED_HEADER
from app.core.money import Money
from app.core.pagination import OffsetParams
from app.core.uow import UnitOfWork
from app.modules.academics.schemas import CloseoutApprove
from app.modules.identity.permissions import OWNER, TUTOR
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import (
    PayItemTypeInactiveError,
    PayItemTypeInUseError,
    PayrollPeriodNotOpenError,
    SessionPayRuleInUseError,
)
from app.modules.payroll.schemas import PayItemTypeUpdate, PayItemUpdate
from app.workflows import payroll_calculation, session_closeout_review
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.invitation_support import sign_in
from tests.integration.payroll_calculation_support import (
    add_item,
    add_pay_type,
    add_period_row,
    add_rate,
    add_rule,
    add_terms,
    approved_lesson,
    clear,
    line_of,
    lines,
    no_cohorts,
    paid_tutor,
    run,
    stable,
)
from tests.integration.test_attendance import lesson, principal_of
from tests.integration.test_closeout_review import version_of
from tests.integration.test_session_closeout import submitted

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)
WAIT_LIMIT = 15.0
POLLS = 750


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> Iterator[None]:
    yield
    clear(module_seeded_url)


@pytest.fixture(scope="module")
def owner(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(OWNER,), display_name="Dueña Centro")


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


async def waiting_on_a_lock(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Returns once another backend of this database waits on a lock. Capped
    at :data:`POLLS` polls."""
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


def in_turns(
    url: str,
    holder: Callable[[AsyncSession], Awaitable[Any]],
    waiter: Callable[[AsyncSession], Awaitable[Any]],
) -> list[Any]:
    """``holder`` runs in an open transaction; ``waiter`` starts and blocks;
    the holder commits; both results (an exception is a result)."""

    async def _run() -> list[Any]:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        try:
            task: asyncio.Task[Any] | None = None

            async def wait() -> Any:
                async with UnitOfWork(sessions) as uow:
                    return await waiter(uow.session)

            async with UnitOfWork(sessions) as uow:
                first = await holder(uow.session)
                task = asyncio.create_task(wait())
                await waiting_on_a_lock(sessions)
            done = await asyncio.wait_for(
                asyncio.gather(task, return_exceptions=True), WAIT_LIMIT
            )
            return [first, done[0]]
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def paused_run(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
    owner_of_step: Any,
    step: str,
    period: uuid.UUID,
    other: Callable[[AsyncSession], Awaitable[Any]],
) -> list[Any]:
    """A run stops at ``step``, inside its transaction; ``other`` starts and
    must wait on a lock; the run goes on. Both results (an exception is a
    result)."""

    async def _run() -> list[Any]:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        reached, resume = asyncio.Event(), asyncio.Event()
        real = getattr(owner_of_step, step)

        async def paused(*args: Any, **kwargs: Any) -> Any:
            reached.set()
            await resume.wait()
            return await real(*args, **kwargs)

        monkeypatch.setattr(owner_of_step, step, paused)

        async def first() -> Any:
            async with UnitOfWork(sessions) as uow:
                return await payroll_calculation.recalculate(uow.session, period)

        async def second() -> Any:
            async with UnitOfWork(sessions) as uow:
                return await other(uow.session)

        try:
            running = asyncio.create_task(first())
            await asyncio.wait_for(reached.wait(), WAIT_LIMIT)
            waiting = asyncio.create_task(second())
            try:
                await waiting_on_a_lock(sessions)
            finally:
                resume.set()
            return list(
                await asyncio.wait_for(
                    asyncio.gather(running, waiting, return_exceptions=True),
                    WAIT_LIMIT,
                )
            )
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def recalculating(period: uuid.UUID) -> Callable[[AsyncSession], Awaitable[Any]]:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll_calculation.recalculate(session, period)

    return call


# --- I2 -----


def test_two_runs_at_once_leave_one_runs_lines(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner)
    approved_lesson(client, lesson(url, starts_at=LATER, tutor=tutor), owner)
    run(url, period)
    one_run = stable(lines(url, period))

    async def _both() -> list[Any]:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        barrier = asyncio.Barrier(2)

        async def one() -> Any:
            await barrier.wait()
            async with UnitOfWork(sessions) as uow:
                return await payroll_calculation.recalculate(uow.session, period)

        try:
            return list(
                await asyncio.wait_for(
                    asyncio.gather(one(), one(), return_exceptions=True), WAIT_LIMIT
                )
            )
        finally:
            await engine.dispose()

    first, second = asyncio.run(_both())

    assert (first.line_count, second.line_count) == (2, 2)
    assert stable(lines(url, period)) == one_run


# --- I3 -----


def test_a_delete_after_a_run_removes_its_lines(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    rate = add_rate(url, tutor.id)
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner)

    def delete_rate(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_compensation_rate(
            session, tutor.id, rate, first_session_dates=no_cohorts
        )

    result, deleted = in_turns(url, recalculating(period), delete_rate)

    assert result.line_count == 1
    assert deleted is None
    assert lines(url, period) == []


def test_a_run_after_a_delete_picks_the_older_version(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    old = add_rate(url, tutor.id, rate="100")
    wrong = add_rate(url, tutor.id, "2026-01-01", rate="999")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner)

    def delete_rate(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_compensation_rate(
            session, tutor.id, wrong, first_session_dates=no_cohorts
        )

    deleted, result = in_turns(url, delete_rate, recalculating(period))

    assert deleted is None
    assert result.line_count == 1
    assert line_of(url, period).compensation_rate_id == old


def test_a_run_after_a_delete_may_have_no_basis(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    only = add_rate(url, tutor.id)
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner)

    def delete_rate(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_compensation_rate(
            session, tutor.id, only, first_session_dates=no_cohorts
        )

    _, result = in_turns(url, delete_rate, recalculating(period))

    assert result.line_count == 0


def rule_on_a_later_no_show(
    url: str,
    client: TestClient,
    frozen: FrozenClock,
    owner: CreatedUser,
    tutor: CreatedUser,
) -> tuple[uuid.UUID, uuid.UUID]:
    """An upcoming rule, dated 2099 for the guard's real date, and a period
    with its no-show approved and no line yet."""
    rule = add_rule(url, "no_show", "2099-03-01", "fixed", amount="70.00")
    period = paid_tutor(url, tutor.id, "2099-03-02", "2099-03-08")
    frozen.set(datetime(2099, 3, 7, 18, 0, tzinfo=UTC))
    made = lesson(url, starts_at=datetime(2099, 3, 4, 16, 0, tzinfo=UTC), tutor=tutor)
    approved_lesson(client, made, owner, statuses=["absent"])
    frozen.set(NOW)
    return rule, period


def deleting_rule(rule: uuid.UUID) -> Callable[[AsyncSession], Awaitable[None]]:
    def call(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_session_pay_rule(session, rule, today=date(2099, 1, 1))

    return call


def test_a_rule_delete_that_waited_for_a_run_is_refused(
    module_seeded_url, client, frozen, owner, tutor
):
    """G3: the line is written before the delete starts."""
    url = module_seeded_url
    rule, period = rule_on_a_later_no_show(url, client, frozen, owner, tutor)

    result, refused = in_turns(url, recalculating(period), deleting_rule(rule))

    assert result.line_count == 1
    assert isinstance(refused, SessionPayRuleInUseError)
    assert line_of(url, period).session_pay_rule_id == rule


def test_a_rule_read_by_a_run_waits_for_it_before_any_line_exists(
    module_seeded_url, client, frozen, owner, tutor, monkeypatch
):
    """C4: the run has read the rule and written nothing. Only ``FOR KEY
    SHARE`` holds the delete back; without it the delete wins and the run's
    insert fails with ``23503``, a ``500``."""
    url = module_seeded_url
    rule, period = rule_on_a_later_no_show(url, client, frozen, owner, tutor)

    result, refused = paused_run(
        url, monkeypatch, payroll, "replace_lines", period, deleting_rule(rule)
    )

    assert result.line_count == 1
    assert isinstance(refused, SessionPayRuleInUseError)
    assert line_of(url, period).session_pay_rule_id == rule


def test_a_rate_delete_takes_the_period_before_the_version(
    module_seeded_url, client, owner, tutor, monkeypatch
):
    """G1: the run holds the period and hasn't read the rates. A delete that
    locked the version first would wait for the period while the run waits for
    the version: ``40P01``, which kills the run."""
    url = module_seeded_url
    add_terms(url, tutor.id)
    rate = add_rate(url, tutor.id)
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner)

    def delete_rate(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_compensation_rate(
            session, tutor.id, rate, first_session_dates=no_cohorts
        )

    result, deleted = paused_run(
        url, monkeypatch, payroll_calculation, "compute_lines", period, delete_rate
    )

    assert result.line_count == 1
    assert deleted is None
    assert lines(url, period) == []


def test_a_terms_delete_takes_the_period_before_the_version(
    module_seeded_url, client, owner, tutor, monkeypatch
):
    """#108 F5, as above: a delete that locked the version first would wait for
    the period while the run waits for the version."""
    url = module_seeded_url
    terms = add_terms(url, tutor.id, salary="3000.00")
    period = add_period_row(url, tutor.id, "11-02", "11-08")

    def delete_terms(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_pay_terms(session, tutor.id, terms)

    result, deleted = paused_run(
        url, monkeypatch, payroll_calculation, "compute_lines", period, delete_terms
    )

    assert result.line_count == 1
    assert deleted is None
    assert lines(url, period) == []


def test_a_terms_delete_after_a_run_removes_its_lines(module_seeded_url, tutor):
    url = module_seeded_url
    kept = add_terms(url, tutor.id, salary="3000.00")
    gone = add_terms(url, tutor.id, "11-05", salary="3500.00")
    period = add_period_row(url, tutor.id, "11-02", "11-08")

    def delete_terms(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_pay_terms(session, tutor.id, gone)

    result, deleted = in_turns(url, recalculating(period), delete_terms)

    assert result.line_count == 2
    assert deleted is None
    assert line_of(url, period).tutor_pay_terms_id == kept


def test_a_run_after_a_terms_delete_uses_the_versions_left(module_seeded_url, tutor):
    url = module_seeded_url
    kept = add_terms(url, tutor.id, salary="3000.00")
    gone = add_terms(url, tutor.id, "11-05", salary="3500.00")
    period = add_period_row(url, tutor.id, "11-02", "11-08")

    def delete_terms(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_pay_terms(session, tutor.id, gone)

    deleted, result = in_turns(url, delete_terms, recalculating(period))

    assert deleted is None
    assert result.line_count == 1
    line = line_of(url, period)
    assert (line.tutor_pay_terms_id, line.proration_numerator) == (kept, 7)


def test_a_terms_version_read_by_a_run_waits_for_it(
    module_seeded_url, tutor, monkeypatch
):
    """#108 C3: the run stops after reading the versions and before any line
    exists, so only the ``FOR KEY SHARE`` of that read can hold this writer, which
    takes the version as a delete does, with no period lock."""
    url = module_seeded_url
    terms = add_terms(url, tutor.id, salary="3000.00")
    period = add_period_row(url, tutor.id, "11-02", "11-08")

    async def take(session: AsyncSession) -> None:
        await session.execute(
            text("SELECT id FROM tutor_pay_terms WHERE id = :id FOR UPDATE"),
            {"id": terms},
        )

    result, taken = paused_run(url, monkeypatch, payroll, "replace_lines", period, take)

    assert result.line_count == 1
    assert taken is None


# --- I4 -----


def test_a_run_and_a_closeout_approval_dont_wait_for_each_other(
    module_seeded_url, client, owner, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    body = submitted(client, made)
    principal = principal_of(owner.id, OWNER)

    async def _run() -> None:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)

        async def approve() -> None:
            async with UnitOfWork(sessions, owner.id) as uow:
                await session_closeout_review.approve_closeout(
                    uow.session,
                    principal=principal,
                    class_session_id=uuid.UUID(made.id),
                    body=CloseoutApprove(version=version_of(body)),
                )

        try:
            async with UnitOfWork(sessions) as uow:
                result = await payroll_calculation.recalculate(uow.session, period)
                assert result.line_count == 0
                # The period stays locked while the approval commits.
                await asyncio.wait_for(approve(), WAIT_LIMIT)
        finally:
            await engine.dispose()

    asyncio.run(_run())

    assert lines(url, period) == []
    assert run(url, period).line_count == 1


def test_a_run_ignores_an_uncommitted_approval(module_seeded_url, client, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    body = submitted(client, made)
    principal = principal_of(owner.id, OWNER)

    async def _run() -> Any:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)

        async def recalc() -> Any:
            async with UnitOfWork(sessions) as uow:
                return await payroll_calculation.recalculate(uow.session, period)

        try:
            async with UnitOfWork(sessions, owner.id) as uow:
                await session_closeout_review.approve_closeout(
                    uow.session,
                    principal=principal,
                    class_session_id=uuid.UUID(made.id),
                    body=CloseoutApprove(version=version_of(body)),
                )
                return await asyncio.wait_for(recalc(), WAIT_LIMIT)
        finally:
            await engine.dispose()

    assert asyncio.run(_run()).line_count == 0


# --- I5 -----


def test_a_run_that_waited_rereads_the_status(module_seeded_url, owner, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)

    async def prepare(session: AsyncSession) -> None:
        await session.execute(
            text(
                "UPDATE payroll_periods SET status = 'prepared',"
                " prepared_at = now(), prepared_by_user_id = :u WHERE id = :p"
            ),
            {"u": owner.id, "p": period},
        )

    _, result = in_turns(url, prepare, recalculating(period))

    assert isinstance(result, PayrollPeriodNotOpenError)
    assert result.details == {"status": "prepared"}
    assert (
        run_sql(url, "SELECT status FROM payroll_periods WHERE id = :p", {"p": period})[
            0
        ].status
        == "prepared"
    )


# --- #109 I: pay items -----


def recurring_on_a_period(url: str, tutor: CreatedUser) -> tuple[uuid.UUID, uuid.UUID]:
    item = add_item(
        url, tutor.id, add_pay_type(url), on="10-01", recurrence="recurring"
    )
    return item, add_period_row(url, tutor.id, "11-02", "11-08")


def test_i2_an_item_delete_during_a_run_waits_and_removes_the_line(
    module_seeded_url, tutor, monkeypatch
):
    """F6: the delete takes the period before the item. The run stops before it
    reads the item, so a delete that took the item first would wait for the
    period while the run waits for the item, a deadlock."""
    url = module_seeded_url
    item, period = recurring_on_a_period(url, tutor)

    def remove(session: AsyncSession) -> Awaitable[None]:
        return payroll.delete_pay_item(session, tutor.id, item)

    result, deleted = paused_run(
        url, monkeypatch, payroll_calculation, "compute_lines", period, remove
    )

    assert result.line_count == 1
    assert deleted is None
    assert lines(url, period) == []


def test_i3_an_item_read_by_a_run_waits_for_it_before_any_line_exists(
    module_seeded_url, tutor, monkeypatch
):
    """C2: the run stops after reading the items and before any line exists, so
    only the ``FOR KEY SHARE`` of that read can hold this writer."""
    url = module_seeded_url
    item, period = recurring_on_a_period(url, tutor)

    async def take(session: AsyncSession) -> None:
        await session.execute(
            text("SELECT id FROM pay_items WHERE id = :id FOR UPDATE"), {"id": item}
        )

    result, taken = paused_run(url, monkeypatch, payroll, "replace_lines", period, take)

    assert result.line_count == 1
    assert taken is None


def test_i3_a_lines_read_takes_no_item_lock(module_seeded_url, tutor):
    """A reader that writes no line takes no lock (C2)."""
    url = module_seeded_url
    item, period = recurring_on_a_period(url, tutor)
    run(url, period)

    async def _run() -> None:
        engine = short_lived_engine(url)
        sessions = create_sessionmaker(engine)
        try:
            async with UnitOfWork(sessions) as holder:
                await holder.session.execute(
                    text("SELECT id FROM pay_items WHERE id = :id FOR UPDATE"),
                    {"id": item},
                )
                async with UnitOfWork(sessions) as reader:
                    await asyncio.wait_for(
                        payroll.period_lines(reader.session, period), WAIT_LIMIT
                    )
                    await asyncio.wait_for(
                        payroll.list_pay_items(
                            reader.session, tutor.id, OffsetParams(limit=5, offset=0)
                        ),
                        WAIT_LIMIT,
                    )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def adding_item(
    tutor: CreatedUser, pay_type: uuid.UUID
) -> Callable[[AsyncSession], Awaitable[Any]]:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll.add_pay_item(
            session,
            tutor.id,
            pay_item_type_id=pay_type,
            description="Bono",
            amount=Money("100.00"),
            recurrence="recurring",
            effective_from=date(2026, 1, 1),
            effective_to=None,
        )

    return call


def deactivating(pay_type: uuid.UUID) -> Callable[[AsyncSession], Awaitable[Any]]:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return payroll.change_pay_item_type(
            session,
            pay_type,
            PayItemTypeUpdate(version=1, is_active=False),
            today=date(2026, 12, 7),
        )

    return call


def test_i4_an_item_that_waited_for_a_deactivation_is_refused(module_seeded_url, tutor):
    url = module_seeded_url
    pay_type = add_pay_type(url)

    deactivated, refused = in_turns(
        url, deactivating(pay_type), adding_item(tutor, pay_type)
    )

    assert deactivated.is_active is False
    assert isinstance(refused, PayItemTypeInactiveError)
    assert run_sql(url, "SELECT id FROM pay_items") == []


def test_i4_a_deactivation_that_waited_for_an_item_is_refused(module_seeded_url, tutor):
    url = module_seeded_url
    pay_type = add_pay_type(url)

    added, refused = in_turns(url, adding_item(tutor, pay_type), deactivating(pay_type))

    assert isinstance(added, uuid.UUID)
    assert isinstance(refused, PayItemTypeInUseError)
    assert run_sql(
        url,
        "SELECT is_active FROM pay_item_types WHERE id = :id",
        {"id": pay_type},
    )[0].is_active


def test_o3_a_reopen_that_waited_for_a_deactivation_is_refused(
    module_seeded_url, tutor
):
    """#109 QA O3: the item ended in the past, so the deactivation passes. Only
    the ``FOR SHARE`` on the type holds the reopen back."""
    url = module_seeded_url
    pay_type = add_pay_type(url)
    item = add_item(
        url, tutor.id, pay_type, on="10-01", until="10-31", recurrence="recurring"
    )

    def reopen(session: AsyncSession) -> Awaitable[None]:
        return payroll.end_pay_item(
            session, tutor.id, item, PayItemUpdate(version=1, effective_to=None)
        )

    deactivated, refused = in_turns(url, deactivating(pay_type), reopen)

    assert deactivated.is_active is False
    assert isinstance(refused, PayItemTypeInactiveError)


def test_i1_two_posts_with_one_key_make_one_item(
    module_seeded_url, client, frozen, owner, tutor, monkeypatch
):
    """The first holds its transaction after writing; the second, with the same
    key, waits and replays."""
    url = module_seeded_url
    pay_type = add_pay_type(url)
    cookie = sign_in(client, owner.username)
    entered, release = threading.Event(), threading.Event()
    original = payroll.add_pay_item

    async def held(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        if not entered.is_set():
            entered.set()
            await anyio.to_thread.run_sync(release.wait, 10)
        return result

    monkeypatch.setattr(payroll, "add_pay_item", held)
    same = f"key-{uuid.uuid4().hex}"
    body = {
        "pay_item_type_id": str(pay_type),
        "description": "Bono",
        "amount": "100.00",
        "recurrence": "one_time",
        "effective_from": "2026-11-03",
    }
    headers = {**signed_headers(cookie), "Idempotency-Key": same}
    path = f"/api/v1/payroll/tutors/{tutor.id}/pay-items"

    def send() -> Any:
        return client.post(path, json=body, headers=headers)

    def second() -> Any:
        entered.wait(10)
        timer = threading.Timer(0.5, release.set)
        timer.start()
        return send()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(send)
        second_future = pool.submit(second)
        responses = [first_future.result(30), second_future.result(30)]

    assert [r.status_code for r in responses] == [201, 201]
    assert sorted(r.headers.get(REPLAYED_HEADER, "") for r in responses) == ["", "true"]
    assert responses[0].json() == responses[1].json()
    count = run_sql(
        url,
        "SELECT count(*) AS n FROM pay_items WHERE tutor_user_id = :u",
        {"u": tutor.id},
    )
    assert count[0].n == 1
