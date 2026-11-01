"""``payroll:open_periods`` and the terms refusals (#106 D, F, G, H) on real
PostgreSQL through the real app. The pure calendar is in
``tests/unit/test_payroll_periods.py``; this file has another name because
pytest can't collect two test modules with one basename.

Each test starts with payroll off, no terms and no periods, and sets the
frozen clock itself.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import Audited
from app.core.db import create_sessionmaker
from app.core.uow import UnitOfWork
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.modules.payroll import service as payroll
from app.modules.payroll.errors import (
    EffectiveFromBeforeFirstPeriodError,
    EffectiveFromInsideGeneratedPeriodError,
)
from app.modules.payroll.models import PayrollPeriod
from app.workflows import payroll_periods
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import SetCookie, auth_client, create_user
from tests.integration.catalog_support import post
from tests.integration.conftest import run_sql, settings_for, short_lived_engine
from tests.integration.guardian_support import delete
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, events
from tests.integration.payroll_period_support import (
    add_terms_row,
    d,
    finalize,
    periods_of,
    reset,
    run_job,
    set_floor,
)

ZONE = ZoneInfo("America/Mexico_City")
ROOT = "/api/v1/payroll/tutors"
HOLD_SECONDS = 0.4
INSIDE = (422, "effective_from_inside_generated_period")
BEFORE = (422, "effective_from_before_first_period")
PREDICATE = (
    "CREATE OR REPLACE FUNCTION tutor_pay_terms_is_referenced(id uuid)"
    " RETURNS boolean LANGUAGE sql VOLATILE AS $$ {body} $$"
)


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(datetime(2026, 11, 10, 12, 0, tzinfo=ZONE)) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def staff(client: TestClient, module_seeded_url: str) -> SetCookie:
    return sign_in(
        client, create_user(module_seeded_url, roles=(FINANCE_STAFF,)).username
    )


@pytest.fixture(autouse=True)
def clean(module_seeded_url: str) -> None:
    reset(module_seeded_url)


@pytest.fixture
def tutor(module_seeded_url: str) -> uuid.UUID:
    return create_user(module_seeded_url, roles=(TUTOR,)).id


@pytest.fixture
def at(frozen: FrozenClock, module_seeded_url: str) -> Callable[[str], None]:
    """Move the clock to noon of a center-local date, and keep the sessions
    alive across the jump."""

    def _at(day: str) -> None:
        value = d(day)
        noon = datetime(value.year, value.month, value.day, 12, tzinfo=ZONE)
        frozen.set(noon)
        run_sql(
            module_seeded_url,
            "UPDATE auth_sessions SET last_seen_at = :now, expires_at = :until",
            {"now": noon, "until": noon + timedelta(days=1)},
        )

    return _at


def add(
    client: TestClient,
    cookie: SetCookie,
    tutor: uuid.UUID,
    on: str,
    frequency: str | None,
    salary: str | None = None,
) -> Any:
    return post(
        client,
        cookie,
        f"{ROOT}/{tutor}/pay-terms",
        {
            "effective_from": str(d(on)),
            "pay_frequency": frequency,
            "salary_amount": salary,
        },
    )


def added(
    client: TestClient,
    cookie: SetCookie,
    tutor: uuid.UUID,
    on: str,
    frequency: str | None,
) -> str:
    response = add(client, cookie, tutor, on, frequency)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def remove(
    client: TestClient, cookie: SetCookie, tutor: uuid.UUID, terms_id: str
) -> Any:
    return delete(client, cookie, f"{ROOT}/{tutor}/pay-terms/{terms_id}")


def row(starts: str, ends: str, frequency: str) -> tuple[str, str, str]:
    return (str(d(starts)), str(d(ends)), frequency)


# --- D: the job ----------------------------------------------------------------------


def test_nothing_opens_while_payroll_is_off(tutor, module_seeded_url, at, monkeypatch):
    url = module_seeded_url
    at("11-10")
    add_terms_row(url, tutor, "01-01", "weekly")

    async def _no_read(session: AsyncSession) -> list[uuid.UUID]:
        raise AssertionError("no tutor is read")

    monkeypatch.setattr(payroll, "tutors_with_terms", _no_read)
    result = run_job(url)

    assert result == payroll_periods.OpenPeriodsRunResult()
    assert periods_of(url, tutor) == []


def test_a_floor_without_terms_opens_nothing(tutor, module_seeded_url, at):
    at("11-10")
    set_floor(module_seeded_url, "11-01")

    assert run_job(module_seeded_url).tutors == 0


def test_terms_in_the_future_open_nothing_until_their_date(
    tutor, module_seeded_url, at
):
    url = module_seeded_url
    set_floor(url, "11-01")
    add_terms_row(url, tutor, "11-20", "weekly")

    at("11-19")
    assert run_job(url).opened == 0

    at("11-20")
    assert run_job(url).opened == 1
    assert periods_of(url, tutor) == [row("11-20", "11-22", "weekly")]


def test_a_past_floor_catches_up_oldest_first_with_the_stub_first(
    tutor, module_seeded_url, at
):
    url = module_seeded_url
    at("12-01")
    set_floor(url, "11-04")
    add_terms_row(url, tutor, "01-01", "weekly")

    result = run_job(url)

    assert (result.tutors, result.opened, result.failed) == (1, 5, 0)
    assert periods_of(url, tutor) == [
        row("11-04", "11-08", "weekly"),
        row("11-09", "11-15", "weekly"),
        row("11-16", "11-22", "weekly"),
        row("11-23", "11-29", "weekly"),
        row("11-30", "12-06", "weekly"),
    ]


def test_only_the_first_row_is_the_first_period(tutor, module_seeded_url, at):
    url = module_seeded_url
    at("11-20")
    set_floor(url, "11-04")
    add_terms_row(url, tutor, "01-01", "weekly")

    run_job(url)

    rows = run_sql(
        url,
        "SELECT is_first_period, previous_ends_on, status FROM payroll_periods"
        " WHERE tutor_user_id = :u ORDER BY starts_on",
        {"u": tutor},
    )
    assert [r.is_first_period for r in rows] == [True, False, False]
    assert [r.previous_ends_on for r in rows] == [None, d("11-08"), d("11-15")]
    assert {r.status for r in rows} == {"open"}


def test_a_rerun_adds_nothing(tutor, module_seeded_url, at):
    url = module_seeded_url
    at("11-20")
    set_floor(url, "11-04")
    add_terms_row(url, tutor, "01-01", "monthly")

    assert run_job(url).opened == 1
    before = periods_of(url, tutor)

    assert run_job(url).opened == 0
    assert periods_of(url, tutor) == before


def test_a_failing_tutor_does_not_block_the_others_and_the_retry_catches_up(
    tutor, module_seeded_url, at, monkeypatch, caplog
):
    url = module_seeded_url
    at("11-20")
    set_floor(url, "11-04")
    other = create_user(url, roles=(TUTOR,)).id
    add_terms_row(url, tutor, "01-01", "weekly")
    add_terms_row(url, other, "01-01", "weekly")
    real = payroll_periods.open_tutor_periods

    async def _opens_then_fails(
        session: AsyncSession, tutor_user_id: uuid.UUID, *, today: date
    ) -> int:
        count = await real(session, tutor_user_id, today=today)
        if tutor_user_id == tutor:
            raise RuntimeError("boom")
        return count

    with monkeypatch.context() as patched:
        patched.setattr(payroll_periods, "open_tutor_periods", _opens_then_fails)
        with caplog.at_level(logging.INFO, logger=payroll_periods.__name__):
            result = run_job(url)

    assert (result.tutors, result.opened, result.failed) == (2, 3, 1)
    assert periods_of(url, tutor) == []  # rolled back
    assert len(periods_of(url, other)) == 3
    assert "boom" not in caplog.text
    retry = run_job(url)
    assert (retry.opened, retry.failed) == (3, 0)
    assert periods_of(url, tutor) == periods_of(url, other)


def test_two_concurrent_runs_leave_the_same_rows(module_seeded_url, at):
    url = module_seeded_url
    at("11-20")
    set_floor(url, "11-04")
    tutors = [create_user(url, roles=(TUTOR,)).id for _ in range(4)]
    for tutor in tutors:
        add_terms_row(url, tutor, "01-01", "weekly")

    async def _both() -> list[payroll_periods.OpenPeriodsRunResult]:
        settings = settings_for(url)
        return await asyncio.gather(
            payroll_periods.run_open_periods(settings),
            payroll_periods.run_open_periods(settings),
        )

    first, second = asyncio.run(_both())

    assert first.failed == second.failed == 0
    assert first.opened + second.opened == 12
    for tutor in tutors:
        assert len(periods_of(url, tutor)) == 3


def test_stop_change_and_return_through_the_routes(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    at("11-02")
    set_floor(url, "11-01")
    added(client, staff, tutor, "10-01", "weekly")
    # Recorded before their periods exist, so they cut stubs (E2).
    added(client, staff, tutor, "11-11", "monthly")
    added(client, staff, tutor, "11-20", None)
    added(client, staff, tutor, "12-16", "weekly")

    at("12-20")
    run_job(url)

    assert periods_of(url, tutor) == [
        row("11-01", "11-01", "weekly"),
        row("11-02", "11-08", "weekly"),
        row("11-09", "11-10", "weekly"),
        row("11-11", "11-19", "monthly"),
        row("11-20", "12-20", "weekly"),
    ]


def test_a_stop_after_the_period_exists_leaves_its_dates_and_ends_the_chain(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    at("11-03")
    set_floor(url, "11-01")
    added(client, staff, tutor, "11-01", "monthly")
    run_job(url)
    added(client, staff, tutor, "11-15", None)

    at("12-20")
    assert run_job(url).opened == 0
    assert periods_of(url, tutor) == [row("11-01", "11-30", "monthly")]


def test_a_salary_change_does_not_split_a_period(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    at("11-20")
    set_floor(url, "11-01")
    added(client, staff, tutor, "11-01", "monthly")
    response = add(client, staff, tutor, "11-15", "monthly", "1000.00")
    assert response.status_code == 201

    run_job(url)

    assert periods_of(url, tutor) == [row("11-01", "11-30", "monthly")]


def test_periods_follow_terms_for_an_archived_tutor_and_one_who_lost_the_role(
    module_seeded_url, at
):
    url = module_seeded_url
    at("11-03")
    set_floor(url, "11-02")
    archived = create_user(url, roles=(TUTOR,), state="archived").id
    former = create_user(url, roles=(TUTOR,)).id
    run_sql(url, "DELETE FROM user_roles WHERE user_id = :u", {"u": former})
    add_terms_row(url, archived, "01-01", "weekly")
    add_terms_row(url, former, "01-01", "weekly")

    assert run_job(url).opened == 2
    assert periods_of(url, archived) == periods_of(url, former) != []


def test_the_job_uses_the_centers_local_date(tutor, module_seeded_url, frozen):
    url = module_seeded_url
    set_floor(url, "10-31")
    add_terms_row(url, tutor, "10-01", "monthly")

    # 05:59 UTC on 11-01 is still 10-31 in the center.
    frozen.set(datetime(2026, 10, 31, 23, 59, tzinfo=ZONE))
    assert run_job(url).opened == 1
    assert periods_of(url, tutor) == [row("10-31", "10-31", "monthly")]

    frozen.set(datetime(2026, 11, 1, 0, 1, tzinfo=ZONE))
    assert run_job(url).opened == 1
    assert periods_of(url, tutor)[-1] == row("11-01", "11-30", "monthly")


def test_a_biweekly_chain_uses_the_stored_anchor(tutor, module_seeded_url, at):
    url = module_seeded_url
    at("11-10")
    set_floor(url, "11-04")
    run_sql(url, "UPDATE payroll_settings SET biweekly_anchor_on = '2026-01-12'")
    add_terms_row(url, tutor, "01-01", "biweekly")

    run_job(url)

    assert periods_of(url, tutor) == [
        row("11-04", "11-15", "biweekly"),
    ]


def test_the_job_writes_no_audit_event_and_logs_counts(
    tutor, module_seeded_url, at, caplog
):
    url = module_seeded_url
    at("11-10")
    set_floor(url, "11-02")
    add_terms_row(url, tutor, "01-01", "weekly")

    with caplog.at_level(logging.INFO, logger=payroll_periods.__name__):
        run_job(url)

    assert not issubclass(PayrollPeriod, Audited)
    assert events(url, record_type="payroll_periods") == []
    [record] = [r for r in caplog.records if r.message == "Opened payroll periods"]
    assert record.__dict__["opened_count"] == 2
    assert str(tutor) not in caplog.text


# --- F: the refusals, by route (F6) --------------------------------------------------


def monthly_with_period(
    client: TestClient,
    staff: SetCookie,
    tutor: uuid.UUID,
    url: str,
    at: Callable[[str], None],
) -> str:
    """Monthly from 11-01, with ``11-01…11-30`` generated."""
    at("11-03")
    set_floor(url, "11-01")
    terms = added(client, staff, tutor, "11-01", "monthly")
    run_job(url)
    assert periods_of(url, tutor) == [row("11-01", "11-30", "monthly")]
    return terms


def test_a_stop_inside_a_period_and_its_deletion(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    monthly_with_period(client, staff, tutor, url, at)

    stop = add(client, staff, tutor, "11-15", None)

    assert stop.status_code == 201
    assert remove(client, staff, tutor, stop.json()["id"]).status_code == 204
    assert periods_of(url, tutor) == [row("11-01", "11-30", "monthly")]


def test_a_same_frequency_version_inside_a_period(
    client, staff, tutor, module_seeded_url, at
):
    monthly_with_period(client, staff, tutor, module_seeded_url, at)

    assert add(client, staff, tutor, "11-15", "monthly", "900.00").status_code == 201


def test_another_frequency_inside_a_period_and_after_it(
    client, staff, tutor, module_seeded_url, at
):
    monthly_with_period(client, staff, tutor, module_seeded_url, at)

    assert code_of(add(client, staff, tutor, "11-15", "weekly")) == INSIDE
    assert add(client, staff, tutor, "12-01", "weekly").status_code == 201


def test_returns_inside_a_period(client, staff, tutor, module_seeded_url, at):
    monthly_with_period(client, staff, tutor, module_seeded_url, at)
    added(client, staff, tutor, "11-15", None)

    assert code_of(add(client, staff, tutor, "11-25", "weekly")) == INSIDE
    assert add(client, staff, tutor, "11-25", "monthly").status_code == 201


def test_deleting_a_stop_that_would_expose_another_frequency(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    monthly_with_period(client, staff, tutor, url, at)
    stop = added(client, staff, tutor, "11-15", None)
    added(client, staff, tutor, "12-07", "weekly")
    at("12-08")
    run_job(url)
    assert periods_of(url, tutor)[-1] == row("12-01", "12-13", "weekly")

    assert code_of(remove(client, staff, tutor, stop)) == INSIDE


def test_terms_back_dated_before_the_first_period(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    at("11-06")
    set_floor(url, "10-01")
    added(client, staff, tutor, "11-05", "weekly")
    run_job(url)
    assert periods_of(url, tutor)[0][0] == "2026-11-05"

    assert code_of(add(client, staff, tutor, "10-01", "weekly")) == BEFORE
    assert code_of(add(client, staff, tutor, "11-04", "weekly")) == BEFORE


def test_deleting_a_stop_that_would_cover_days_before_the_first_period(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    at("11-06")
    set_floor(url, "10-15")
    added(client, staff, tutor, "09-01", "weekly")
    stop = added(client, staff, tutor, "10-01", None)
    added(client, staff, tutor, "11-05", "weekly")
    run_job(url)
    assert periods_of(url, tutor)[0][0] == "2026-11-05"

    assert code_of(remove(client, staff, tutor, stop)) == BEFORE


def test_terms_before_the_floor_are_accepted(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    at("11-06")
    set_floor(url, "11-05")
    added(client, staff, tutor, "09-01", "weekly")
    run_job(url)
    assert periods_of(url, tutor)[0][0] == "2026-11-05"

    assert add(client, staff, tutor, "08-01", "weekly").status_code == 201


def test_a_same_frequency_version_in_a_terms_gap_is_the_way_out(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    monthly_with_period(client, staff, tutor, url, at)
    added(client, staff, tutor, "11-10", None)

    assert add(client, staff, tutor, "11-20", "monthly").status_code == 201


def test_without_periods_any_date_is_accepted(
    client, staff, tutor, module_seeded_url, at
):
    at("11-06")
    set_floor(module_seeded_url, "11-01")

    assert add(client, staff, tutor, "2020-01-01", "weekly").status_code == 201
    assert add(client, staff, tutor, "2026-10-15", "monthly").status_code == 201
    assert add(client, staff, tutor, "2030-01-01", None).status_code == 201


def test_a_stop_and_a_salary_version_inside_a_finalized_period(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    monthly_with_period(client, staff, tutor, url, at)
    [period] = run_sql(url, "SELECT id FROM payroll_periods")
    finalize(url, period.id, create_user(url, roles=(OWNER,)).id)

    assert add(client, staff, tutor, "11-15", None).status_code == 201
    assert add(client, staff, tutor, "11-20", "monthly", "900.00").status_code == 201
    assert code_of(add(client, staff, tutor, "11-25", "weekly")) == INSIDE
    assert periods_of(url, tutor) == [row("11-01", "11-30", "monthly")]


def test_rate_versions_are_not_affected(client, staff, tutor, module_seeded_url, at):
    monthly_with_period(client, staff, tutor, module_seeded_url, at)

    response = post(
        client,
        staff,
        f"{ROOT}/{tutor}/rates",
        {
            "class_kind": "individual",
            "effective_from": "2026-11-15",
            "rate_model": "per_hour",
            "rate": "250.0000",
        },
    )

    assert response.status_code == 201


def test_the_refusals_come_after_own_pay_and_the_404(
    client, tutor, module_seeded_url, at
):
    url = module_seeded_url
    at("11-03")
    set_floor(url, "11-01")
    add_terms_row(url, tutor, "11-01", "monthly")
    run_job(url)
    finance_tutor = create_user(url, roles=(FINANCE_STAFF, TUTOR))
    add_terms_row(url, finance_tutor.id, "11-01", "monthly")
    run_job(url)
    cookie = sign_in(client, finance_tutor.username)

    own = add(client, cookie, finance_tutor.id, "11-15", "weekly")
    missing = add(client, cookie, uuid.uuid4(), "11-15", "weekly")

    assert code_of(own) == (403, "payroll_own_compensation")
    assert code_of(missing) == (404, "not_found")


def test_the_refusal_comes_before_the_taken_date(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    monthly_with_period(client, staff, tutor, url, at)
    added(client, staff, tutor, "11-15", "monthly")

    assert code_of(add(client, staff, tutor, "11-15", "weekly")) == INSIDE
    assert code_of(add(client, staff, tutor, "11-15", "monthly")) == (
        409,
        "pay_terms_exists",
    )


@contextmanager
def referenced(url: str) -> Iterator[None]:
    """Every terms row is "used by a finalized line" (the #105 helper)."""
    [original] = run_sql(
        url,
        "SELECT prosrc FROM pg_proc WHERE proname = 'tutor_pay_terms_is_referenced'",
    )
    run_sql(url, PREDICATE.format(body="SELECT true"))
    try:
        yield
    finally:
        run_sql(url, PREDICATE.format(body=original.prosrc))


def test_delete_order_404_then_the_refusal_then_in_use(
    client, staff, tutor, module_seeded_url, at
):
    url = module_seeded_url
    terms = monthly_with_period(client, staff, tutor, url, at)
    stop = added(client, staff, tutor, "11-15", None)
    added(client, staff, tutor, "12-07", "weekly")
    other = create_user(url, roles=(TUTOR,)).id
    at("12-08")
    run_job(url)

    with referenced(url):
        assert code_of(remove(client, staff, other, stop)) == (404, "not_found")
        assert code_of(remove(client, staff, tutor, stop)) == INSIDE
        assert code_of(remove(client, staff, tutor, terms)) == (409, "pay_terms_in_use")


# --- G: concurrency, two real sessions -----------------------------------------------


async def _in_order(
    url: str,
    holds: Callable[[AsyncSession], Awaitable[Any]],
    waits: Callable[[AsyncSession], Awaitable[Any]],
) -> list[Any]:
    """``holds`` runs and stays uncommitted while ``waits`` starts."""
    engine = short_lived_engine(url)
    sessions = create_sessionmaker(engine)
    started = asyncio.Event()

    async def first() -> Any:
        async with UnitOfWork(sessions) as uow:
            result = await holds(uow.session)
            started.set()
            await asyncio.sleep(HOLD_SECONDS)
            return result

    async def second() -> Any:
        await started.wait()
        async with UnitOfWork(sessions) as uow:
            return await waits(uow.session)

    try:
        return await asyncio.gather(first(), second(), return_exceptions=True)
    finally:
        await engine.dispose()


def test_a_terms_insert_waits_for_the_job_and_is_refused(tutor, module_seeded_url):
    url = module_seeded_url
    set_floor(url, "11-01")
    add_terms_row(url, tutor, "11-01", "monthly")

    opened, refused = asyncio.run(
        _in_order(
            url,
            lambda s: payroll.open_due_periods(s, tutor, today=d("11-03")),
            lambda s: payroll.add_pay_terms(
                s,
                tutor,
                effective_from=d("11-15"),
                pay_frequency="weekly",
                salary_amount=None,
            ),
        )
    )

    assert opened == 1
    assert isinstance(refused, EffectiveFromInsideGeneratedPeriodError)


def test_the_job_waits_for_a_terms_insert_and_cuts_a_stub(tutor, module_seeded_url):
    url = module_seeded_url
    set_floor(url, "11-01")
    add_terms_row(url, tutor, "11-01", "monthly")

    inserted, opened = asyncio.run(
        _in_order(
            url,
            lambda s: payroll.add_pay_terms(
                s,
                tutor,
                effective_from=d("11-15"),
                pay_frequency="weekly",
                salary_amount=None,
            ),
            lambda s: payroll.open_due_periods(s, tutor, today=d("11-03")),
        )
    )

    assert isinstance(inserted, uuid.UUID)
    assert opened == 1
    assert periods_of(url, tutor) == [row("11-01", "11-14", "monthly")]


def test_a_first_terms_insert_racing_the_job_for_another_period(
    tutor, module_seeded_url
):
    # Back-dating needs a period to compare with, and the first one is being
    # opened: the insert waits and sees it.
    url = module_seeded_url
    set_floor(url, "10-01")
    add_terms_row(url, tutor, "11-05", "weekly")

    opened, refused = asyncio.run(
        _in_order(
            url,
            lambda s: payroll.open_due_periods(s, tutor, today=d("11-06")),
            lambda s: payroll.add_pay_terms(
                s,
                tutor,
                effective_from=d("10-01"),
                pay_frequency="weekly",
                salary_amount=None,
            ),
        )
    )

    assert opened == 1
    assert isinstance(refused, EffectiveFromBeforeFirstPeriodError)


def test_two_terms_writes_serialize(tutor, module_seeded_url, client, staff, at):
    # The stop is refused to delete, until the first write ends the monthly
    # terms before the weekly period: the delete must see that insert.
    url = module_seeded_url
    monthly_with_period(client, staff, tutor, url, at)
    stop = added(client, staff, tutor, "11-15", None)
    added(client, staff, tutor, "12-07", "weekly")
    at("12-08")
    run_job(url)
    assert code_of(remove(client, staff, tutor, stop)) == INSIDE

    inserted, deleted = asyncio.run(
        _in_order(
            url,
            lambda s: payroll.add_pay_terms(
                s,
                tutor,
                effective_from=d("12-01"),
                pay_frequency=None,
                salary_amount=None,
            ),
            lambda s: payroll.delete_pay_terms(s, tutor, uuid.UUID(stop)),
        )
    )

    assert isinstance(inserted, uuid.UUID)
    assert deleted is None
