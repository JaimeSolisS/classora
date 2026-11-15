"""The recurring expense job ``finance:generate_recurring_expenses`` (#116 D;
ADR-0017 E17; data-model §12.7, §13.1 X2, §15). The routes are
``test_finance_expense_templates.py``; the schedule pins
``tests/unit/test_job_rules.py``.

Runs use :func:`run_generate_recurring_expenses` with its own direct engine,
as the worker does. Concurrency tests use real connections: ``overlap`` stops
the first unit inside its transaction and checks that the second waits on a
lock (``pg_stat_activity``).
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money
from app.modules.finance import templates as finance_templates
from app.modules.finance.models import ExpenseTemplate
from app.modules.finance.schemas import ExpenseTemplateUpdate
from app.modules.identity.permissions import FINANCE_STAFF, OWNER
from app.workflows import recurring_expenses
from app.workflows.recurring_expenses import RecurringExpenseRunResult
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import auth_client
from tests.integration.catalog_support import patch
from tests.integration.conftest import run_sql, settings_for
from tests.integration.corrections_support import Staff, staff
from tests.integration.finance_support import CATEGORIES, VENDORS, reset
from tests.integration.template_support import (
    category_of,
    change,
    clear_queue,
    code,
    generated,
    insert_template,
    new_category,
    new_vendor,
    run_job,
    set_active,
    set_next,
    template_row,
)
from tests.integration.test_payroll_workflow_concurrency import overlap

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
TODAY = date(2026, 12, 7)
"""The center's today at :data:`NOW` (noon in Mexico City)."""


def local_noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 18, 0, tzinfo=UTC)


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
    yield
    reset(module_seeded_url)
    clear_queue(module_seeded_url)


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance_staff(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture
def rent(module_seeded_url: str) -> str:
    return category_of(module_seeded_url, "rent")


def counts(result: RecurringExpenseRunResult) -> tuple[int, int, int, int]:
    return (
        result.templates,
        result.generated,
        result.already_generated,
        result.failed,
    )


def periods(url: str, template: str) -> list[date]:
    return [row.period_start for row in generated(url, template)]


def unit(template: str, today: date = TODAY) -> Any:
    """The job's unit for one template, as a step on a session."""

    def call(session: AsyncSession) -> Awaitable[Any]:
        return recurring_expenses.generate_for_template(
            session, uuid.UUID(template), today=today
        )

    return call


# --- D2, D4: candidates and catch-up -----


def test_d4_a_late_worker_catches_up_oldest_first_in_one_run(module_seeded_url, rent):
    url = module_seeded_url
    template = insert_template(url, rent, date(2026, 9, 7), due_offset_days=10)

    result = run_job(url)

    assert counts(result) == (1, 4, 0, 0)
    rows = generated(url, template)
    assert [r.period_start for r in rows] == [
        date(2026, 9, 7),
        date(2026, 10, 7),
        date(2026, 11, 7),
        date(2026, 12, 7),
    ]
    for row in rows:
        assert row.incurred_on == row.period_start
        assert row.due_on == row.period_start + timedelta(days=10)
        assert row.created_by_user_id is None
        assert (row.receipt_reference, row.note) == (None, None)
        assert row.amount == Decimal("8000.00")
    assert template_row(url, template).next_period_start == date(2027, 1, 7)


def test_d4_nothing_after_today_or_after_ends_on_and_an_ended_template_is_done(
    module_seeded_url, rent
):
    url = module_seeded_url
    ending = insert_template(
        url, rent, date(2026, 9, 7), ends_on=date(2026, 10, 31), name="Termina"
    )
    future = insert_template(url, rent, date(2026, 12, 8), name="Futura")
    paused = insert_template(url, rent, date(2026, 11, 7), is_active=False)

    first = run_job(url)
    second = run_job(url)

    assert periods(url, ending) == [date(2026, 9, 7), date(2026, 10, 7)]
    assert template_row(url, ending).next_period_start == date(2026, 11, 7)
    assert periods(url, future) == periods(url, paused) == []
    assert counts(first) == (1, 2, 0, 0)
    assert counts(second) == (0, 0, 0, 0)


def test_d4_an_ends_on_on_a_period_start_posts_that_period(module_seeded_url, rent):
    url = module_seeded_url
    template = insert_template(url, rent, date(2026, 10, 7), ends_on=date(2026, 11, 7))

    run_job(url)

    assert periods(url, template) == [date(2026, 10, 7), date(2026, 11, 7)]


def test_d2_candidates_are_paged_by_id(module_seeded_url, monkeypatch, rent):
    url = module_seeded_url
    monkeypatch.setattr(recurring_expenses.finance, "TEMPLATE_CANDIDATE_PAGE", 2)
    original = finance_templates.due_template_ids

    async def small_pages(session: AsyncSession, **kwargs: Any) -> Any:
        return await original(session, **{**kwargs, "limit": 2})

    monkeypatch.setattr(recurring_expenses.finance, "due_template_ids", small_pages)
    templates = [insert_template(url, rent, TODAY) for _ in range(5)]

    result = run_job(url)

    assert counts(result) == (5, 5, 0, 0)
    for template in templates:
        assert periods(url, template) == [TODAY]


# --- D3: one Unit of Work per template -----


def test_d3_a_failing_template_rolls_back_alone(
    module_seeded_url, monkeypatch, rent, caplog
):
    url = module_seeded_url
    bad = insert_template(url, rent, date(2026, 11, 7), name="Falla")
    good = insert_template(url, rent, date(2026, 11, 7), name="Funciona")
    real = finance_templates.insert_expense
    calls: list[date] = []

    async def failing(session: AsyncSession, **kwargs: Any) -> Any:
        if str(kwargs["expense_template_id"]) == bad:
            calls.append(kwargs["period_start"])
            if kwargs["period_start"] == date(2026, 12, 7):
                raise RuntimeError("disk full")
        return await real(session, **kwargs)

    monkeypatch.setattr(finance_templates, "insert_expense", failing)

    with caplog.at_level(logging.ERROR, logger=recurring_expenses.__name__):
        result = run_job(url)

    assert counts(result) == (2, 2, 0, 1)
    assert calls == [date(2026, 11, 7), date(2026, 12, 7)]
    assert periods(url, bad) == []  # the November insert rolled back too
    assert template_row(url, bad).next_period_start == date(2026, 11, 7)
    assert periods(url, good) == [date(2026, 11, 7), date(2026, 12, 7)]
    [record] = caplog.records
    assert record.expense_template_id == bad
    assert record.error == "RuntimeError"

    monkeypatch.setattr(finance_templates, "insert_expense", real)
    assert counts(run_job(url)) == (1, 2, 0, 0)  # the next run catches up
    assert periods(url, bad) == [date(2026, 11, 7), date(2026, 12, 7)]


# --- D5, D8: idempotent -----


def test_d5_a_rerun_creates_nothing_and_a_stale_row_counts_already_generated(
    module_seeded_url, rent
):
    url = module_seeded_url
    template = insert_template(url, rent, date(2026, 11, 7))
    assert counts(run_job(url)) == (1, 2, 0, 0)

    assert counts(run_job(url)) == (0, 0, 0, 0)

    set_next(url, template, date(2026, 11, 7))
    assert counts(run_job(url)) == (1, 0, 2, 0)
    assert periods(url, template) == [date(2026, 11, 7), date(2026, 12, 7)]
    assert template_row(url, template).next_period_start == date(2027, 1, 7)


def test_d8_a_voided_generated_expense_keeps_its_period(module_seeded_url, owner, rent):
    url = module_seeded_url
    template = insert_template(url, rent, TODAY)
    run_job(url)
    [expense] = generated(url, template)
    run_sql(
        url,
        "INSERT INTO expense_adjustments (expense_id, amount, reason,"
        " created_by_user_id) VALUES (:e, :a, 'Anulado', :u)",
        {"e": expense.id, "a": -expense.amount, "u": owner.user.id},
    )
    set_next(url, template, TODAY)

    result = run_job(url)

    assert counts(result) == (1, 0, 1, 0)
    assert [row.id for row in generated(url, template)] == [expense.id]


# --- D6: concurrent runs -----


def test_d6_two_concurrent_runs_leave_one_expense_per_period(module_seeded_url, rent):
    url = module_seeded_url
    templates = [insert_template(url, rent, date(2026, 10, 7)) for _ in range(4)]

    async def both() -> list[RecurringExpenseRunResult]:
        settings = settings_for(url)
        return list(
            await asyncio.gather(
                recurring_expenses.run_generate_recurring_expenses(settings),
                recurring_expenses.run_generate_recurring_expenses(settings),
            )
        )

    first, second = asyncio.run(both())

    assert first.failed == second.failed == 0
    assert first.generated + second.generated == 12
    for template in templates:
        assert periods(url, template) == [
            date(2026, 10, 7),
            date(2026, 11, 7),
            date(2026, 12, 7),
        ]


def test_d6_the_second_unit_waits_on_the_template_and_finds_nothing_due(
    module_seeded_url, monkeypatch, rent
):
    url = module_seeded_url
    template = insert_template(url, rent, date(2026, 11, 7))

    first, second = overlap(
        url,
        monkeypatch,
        finance_templates,
        "insert_expense",
        unit(template),
        unit(template),
    )

    assert (first.generated, first.already_generated) == (2, 0)
    assert (second.generated, second.already_generated) == (0, 0)
    assert periods(url, template) == [date(2026, 11, 7), date(2026, 12, 7)]


def test_d6_without_the_row_lock_the_unique_index_and_savepoint_hold(
    module_seeded_url, monkeypatch, rent
):
    url = module_seeded_url
    template = insert_template(url, rent, date(2026, 11, 7))

    async def unlocked(session: AsyncSession, template_id: uuid.UUID) -> Any:
        return await session.scalar(
            select(ExpenseTemplate)
            .where(ExpenseTemplate.id == template_id)
            .execution_options(populate_existing=True)
        )

    monkeypatch.setattr(finance_templates, "lock_template", unlocked)

    first, second = overlap(
        url,
        monkeypatch,
        finance_templates,
        "insert_expense",
        unit(template),
        unit(template),
    )

    assert not isinstance(first, BaseException), first
    assert not isinstance(second, BaseException), second
    assert (first.generated, first.already_generated) == (2, 0)
    assert (second.generated, second.already_generated) == (0, 2)
    assert periods(url, template) == [date(2026, 11, 7), date(2026, 12, 7)]
    assert template_row(url, template).next_period_start == date(2027, 1, 7)


# --- D7: a run racing a PATCH -----


def _pausing(template: str, version: int) -> Any:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance_templates.change_template(
            session,
            uuid.UUID(template),
            ExpenseTemplateUpdate(version=version, is_active=False),
        )

    return call


def _repricing(template: str, version: int, amount: str) -> Any:
    def call(session: AsyncSession) -> Awaitable[Any]:
        return finance_templates.change_template(
            session,
            uuid.UUID(template),
            ExpenseTemplateUpdate(version=version, amount=Money(amount)),
        )

    return call


def test_d7_a_pause_committed_first_posts_nothing(module_seeded_url, monkeypatch, rent):
    url = module_seeded_url
    template = insert_template(url, rent, date(2026, 11, 7))

    first, second = overlap(
        url,
        monkeypatch,
        finance_templates,
        "lock_template",
        _pausing(template, 1),
        unit(template),
    )

    assert first.item.status == "paused"
    assert (second.generated, second.already_generated) == (0, 0)
    assert periods(url, template) == []


def test_d7_a_run_first_makes_the_pause_wait_and_apply_after_the_posting(
    module_seeded_url, monkeypatch, rent
):
    url = module_seeded_url
    template = insert_template(url, rent, date(2026, 11, 7))

    first, second = overlap(
        url,
        monkeypatch,
        finance_templates,
        "insert_expense",
        unit(template),
        _pausing(template, 1),
    )

    assert first.generated == 2
    assert not isinstance(second, BaseException), second
    assert second.item.status == "paused"
    assert second.item.next_period_start == date(2027, 1, 7)
    assert periods(url, template) == [date(2026, 11, 7), date(2026, 12, 7)]


def test_d7_an_amount_change_and_a_run_each_period_all_old_or_all_new(
    module_seeded_url, monkeypatch, rent
):
    url = module_seeded_url
    run_first = insert_template(url, rent, date(2026, 11, 7))
    change_first = insert_template(url, rent, date(2026, 11, 7))

    overlap(
        url,
        monkeypatch,
        finance_templates,
        "insert_expense",
        unit(run_first),
        _repricing(run_first, 1, "9000.00"),
    )
    overlap(
        url,
        monkeypatch,
        finance_templates,
        "lock_template",
        _repricing(change_first, 1, "9000.00"),
        unit(change_first),
    )

    assert {r.amount for r in generated(url, run_first)} == {Decimal("8000.00")}
    assert {r.amount for r in generated(url, change_first)} == {Decimal("9000.00")}
    assert template_row(url, run_first).amount == Decimal("9000.00")


# --- D9: the center's today and the month end -----


def test_d9_a_period_is_due_on_the_centers_date_not_utcs(
    module_seeded_url, frozen, rent
):
    url = module_seeded_url
    template = insert_template(url, rent, date(2027, 1, 1))

    # 05:30 UTC on 1 January is 23:30 on 31 December in Mexico City.
    frozen.set(datetime(2027, 1, 1, 5, 30, tzinfo=UTC))
    assert counts(run_job(url)) == (0, 0, 0, 0)
    frozen.set(datetime(2027, 1, 1, 6, 30, tzinfo=UTC))
    assert counts(run_job(url)) == (1, 1, 0, 0)

    assert periods(url, template) == [date(2027, 1, 1)]


@pytest.mark.parametrize("offset", [0, 60])
def test_d9_a_template_on_the_31st_posts_on_each_month_end(
    module_seeded_url, frozen, rent, offset
):
    url = module_seeded_url
    template = insert_template(url, rent, date(2028, 1, 31), due_offset_days=offset)
    frozen.set(local_noon(date(2028, 4, 30)))

    run_job(url)

    rows = generated(url, template)
    assert [r.period_start for r in rows] == [
        date(2028, 1, 31),
        date(2028, 2, 29),
        date(2028, 3, 31),
        date(2028, 4, 30),
    ]
    for row in rows:
        assert row.incurred_on == row.period_start
        assert row.due_on == row.period_start + timedelta(days=offset)
    assert template_row(url, template).next_period_start == date(2028, 5, 31)


# --- D10: the category/vendor invariant -----


def test_d10_a_live_templates_category_and_vendor_stay_active_so_runs_post(
    module_seeded_url, client, finance_staff
):
    url = module_seeded_url
    category = new_category(url)
    vendor = new_vendor(url)
    live = insert_template(url, category, TODAY, vendor_id=vendor)

    refused = patch(
        client,
        finance_staff.cookie,
        f"{CATEGORIES}/{category}",
        {"version": 1, "is_active": False},
    )
    vendor_refused = patch(
        client,
        finance_staff.cookie,
        f"{VENDORS}/{vendor}",
        {"version": 1, "is_active": False},
    )
    assert code(refused) == (409, "expense_category_in_use")
    assert code(vendor_refused) == (409, "vendor_in_use")
    assert counts(run_job(url)) == (1, 1, 0, 0)

    # Paused, the category deactivated, then a reactivation is refused.
    paused = insert_template(url, category, TODAY, is_active=False, name="Pausada")
    set_active(url, "expense_templates", live, False)
    deactivated = patch(
        client,
        finance_staff.cookie,
        f"{CATEGORIES}/{category}",
        {"version": 1, "is_active": False},
    )
    assert code(deactivated) == (200, None)
    resumed = change(
        client, finance_staff.cookie, paused, {"version": 1, "is_active": True}
    )
    assert code(resumed) == (409, "expense_category_inactive")
    assert periods(url, paused) == []


# --- D11, D12: logs, actor, run by hand -----


def test_d11_logs_hold_counts_and_ids_and_generated_events_have_no_actor(
    module_seeded_url, rent, caplog
):
    url = module_seeded_url
    template = insert_template(
        url, rent, TODAY, name="Renta secreta 2468", amount=Decimal("1357.91")
    )

    with caplog.at_level(logging.DEBUG):
        result = run_job(url)

    assert counts(result) == (1, 1, 0, 0)
    [line] = [r for r in caplog.records if r.message == "Generated recurring expenses"]
    assert (
        line.templates_count,
        line.generated_count,
        line.already_generated_count,
        line.failed_count,
    ) == (1, 1, 0, 0)
    for secret in ("Renta secreta 2468", "1357.91"):
        assert secret not in caplog.text
    [expense] = generated(url, template)
    assert expense.description == "Renta secreta 2468"
    events = run_sql(
        url,
        "SELECT record_type, actor_user_id, changes FROM audit_events"
        " WHERE record_id = ANY(:ids) ORDER BY occurred_at, id",
        {"ids": [expense.id, uuid.UUID(template)]},
    )
    assert [(e.record_type, e.actor_user_id) for e in events] == [
        ("expenses", None),
        ("expense_templates", None),
    ]
    assert events[0].changes["description"]["new"] == "[masked]"
    assert events[0].changes["amount"]["new"] == "[masked]"
    assert events[0].changes["expense_template_id"]["new"] == template
    assert events[0].changes["period_start"]["new"] == TODAY.isoformat()


def test_d12_the_run_by_hand_is_documented_and_builds_its_own_engine():
    doc = recurring_expenses.__doc__ or ""
    assert "run_generate_recurring_expenses as run" in doc
    assert (
        "create_worker_engine"
        in recurring_expenses.run_generate_recurring_expenses.__code__.co_names
    )
