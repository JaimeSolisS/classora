"""The lock's invariants under seeded random sequences (#110 K4, E9; ADR-0017 E10;
data-model §13.1 P4, P5, P11).

Runs, prepares, returns, item writes and finalizes, through the routes, in the
normal shards. A failing seed is in the assertion's message, so a rerun with
``SEEDS = [<seed>]`` repeats it. After each finalize: ``total_amount`` is the sum
of the lines and what ``GET …/lines`` shows, it isn't negative, the lines of every
period finalized so far are byte-identical, and a one-time item has a line in at
most one finalized period.
"""

import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff
from tests.integration.payroll_calculation_support import (
    add_item,
    add_pay_type,
    add_period_row,
    add_rate,
    add_terms,
    approved_lesson,
    clear,
    lines,
)
from tests.integration.payroll_workflow_support import (
    NOT_OPEN,
    NOT_PREPARED,
    OK,
    OUT_OF_ORDER,
    STALE,
    code,
    finalize,
    prepare,
    read,
    recalculate,
    return_period,
    row_of,
    status_of,
)
from tests.integration.test_attendance import lesson

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
SEEDS = [110, 20261102, 7]
STEPS = 45
WEEKS = (
    ("11-02", "11-08"),
    ("11-09", "11-15"),
    ("11-16", "11-22"),
    ("11-23", "11-29"),
)
EXPECTED: dict[str, set[Any]] = {
    "recalculate": {OK, NOT_OPEN},
    "prepare": {OK, NOT_OPEN, (409, "payroll_negative_total")},
    "return": {OK, NOT_PREPARED},
    "finalize": {OK, NOT_PREPARED, OUT_OF_ORDER, STALE},
}


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
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


def scene(
    url: str, client: TestClient, owner: Staff, tutor: CreatedUser, rng: random.Random
) -> list[Any]:
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    periods = [
        add_period_row(url, tutor.id, starts, ends, first=index == 0)
        for index, (starts, ends) in enumerate(WEEKS)
    ]
    for day in rng.sample(range(2, 29), 6):
        starts_at = datetime(2026, 11, day, 16, 0, tzinfo=UTC)
        approved_lesson(
            client,
            lesson(url, starts_at=starts_at, tutor=tutor),
            owner.user,
            minutes=rng.choice((30, 45, 55, 60)),
        )
    return periods


def snapshot(url: str, period: Any) -> list[dict[str, Any]]:
    return [dict(line._mapping) for line in lines(url, period)]


def checked(
    url: str,
    client: TestClient,
    finance: Staff,
    finalized: dict[Any, list[dict[str, Any]]],
    period: Any,
    seed: int,
) -> None:
    row = row_of(url, period)
    found = snapshot(url, period)
    total = sum((line["amount"] for line in found), Decimal("0.00"))
    assert row.total_amount == total, f"seed {seed}: the total isn't the lines"
    assert row.total_amount >= 0, f"seed {seed}: a negative total"
    assert read(client, finance, period).json()["total"] == str(row.total_amount), (
        f"seed {seed}: GET shows another total"
    )
    finalized[period] = found
    for each, lines_then in finalized.items():
        assert snapshot(url, each) == lines_then, f"seed {seed}: a locked line moved"
    repeated = run_sql(
        url,
        "SELECT l.pay_item_id FROM payroll_lines AS l"
        " JOIN payroll_periods AS p ON p.id = l.payroll_period_id"
        " JOIN pay_items AS i ON i.id = l.pay_item_id"
        " WHERE p.status = 'finalized' AND i.recurrence = 'one_time'"
        " GROUP BY l.pay_item_id HAVING count(*) > 1",
    )
    assert repeated == [], f"seed {seed}: a one-time item was paid twice"


def act(
    client: TestClient, url: str, owner: Staff, finance: Staff, action: str, period: Any
) -> Any:
    if action == "recalculate":
        return recalculate(client, finance, period)
    if action == "prepare":
        return prepare(client, finance, period)
    if action == "return":
        return return_period(client, owner, period)
    return finalize(client, url, owner, period)


@pytest.mark.parametrize("seed", SEEDS)
def test_k4_the_lock_holds_under_random_sequences(
    module_seeded_url, client, frozen, owner, finance, seed
):
    url = module_seeded_url
    rng = random.Random(seed)  # noqa: S311 - a test's sequence, not a secret
    tutor = create_user(url, roles=(TUTOR,))
    frozen.set(NOW)
    periods = scene(url, client, owner, tutor, rng)
    bonus, deduction = add_pay_type(url), add_pay_type(url, direction="deduction")
    finalized: dict[Any, list[dict[str, Any]]] = {}
    outcomes: list[str] = []

    for step in range(STEPS):
        frozen.set(NOW + timedelta(seconds=step))
        period = rng.choice(periods)
        action = rng.choice(
            (
                "recalculate",
                "prepare",
                "prepare",
                "return",
                "finalize",
                "finalize",
                "item",
            )
        )
        if action == "item":
            add_item(
                url,
                tutor.id,
                rng.choice((bonus, deduction)),
                str(rng.randint(1, 60)),
                on=rng.choice(("11-03", "11-10", "11-17", "11-24")),
                recurrence=rng.choice(("one_time", "one_time", "recurring")),
            )
            continue
        was = status_of(url, period)
        response = act(client, url, owner, finance, action, period)
        answer = code(response)
        assert answer in EXPECTED[action], f"seed {seed}, step {step}: {response.text}"
        outcomes.append(f"{action}:{answer[1] or answer[0]}")
        if action == "finalize" and answer == OK:
            assert was == "prepared", f"seed {seed}, step {step}"
            checked(url, client, finance, finalized, period, seed)

    # The sequence has to have reached the lock, or it proved nothing.
    assert finalized, f"seed {seed}: nothing finalized: {outcomes}"
    oldest_first = [p for p in periods if p in finalized]
    assert oldest_first == periods[: len(oldest_first)], f"seed {seed}: out of order"
