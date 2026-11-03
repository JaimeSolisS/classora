"""Carrying a negative payroll total forward through the real route (M7-08c C,
D1, D2, E2, E4, E5; qa-104 O2; ADR-0017 E9 as amended by M7-08c E3).

Closeouts, re-approvals, ``manual`` adjustments, ``prepare``, ``return``,
``finalize`` and ``GET …/lines`` go through their routes, and the #111 job runs
by hand; terms, rates and periods are set by SQL, as in every payroll test.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.idempotency import REPLAYED_HEADER
from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.workflows import payroll_adjustments
from app.workflows.payroll_adjustments import PostAdjustmentsRunResult
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import CreatedUser, auth_client, create_user
from tests.integration.conftest import settings_for
from tests.integration.corrections_support import Staff, staff
from tests.integration.lifecycle_support import code_of
from tests.integration.payroll_calculation_support import (
    add_period_row,
    add_terms,
    approved_lesson,
    clear,
    paid_tutor,
)
from tests.integration.payroll_manual_support import (
    CARRIED_KEYS,
    CREATED,
    NEGATIVE,
    NOT_FOUND,
    NOT_NEGATIVE,
    adjustment_of,
    adjustments,
    audit_rows,
    carry,
    manual,
    stored_keys,
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    OUT_OF_ORDER,
    UNRESOLVED,
    code,
    finalize,
    locked,
    new_key,
    prepare,
    read,
    return_period,
    row_of,
    status_of,
)
from tests.integration.test_attendance import lesson
from tests.integration.test_closeout_review import correct_closeout, version_of

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
WEEK = timedelta(days=7)
NOT_OPEN = (409, "payroll_period_not_open")
NOT_ENDED = (409, "payroll_period_not_ended")


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


@pytest.fixture
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    """Per test, and not reauthenticated: carry-forward needs none (E4)."""
    return staff(module_seeded_url, client, (FINANCE_STAFF,), reauth=False)


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


# --- Helpers -----


def run_job(url: str) -> PostAdjustmentsRunResult:
    return asyncio.run(payroll_adjustments.run_post_adjustments(settings_for(url)))


def shown_total(client: TestClient, who: Staff, period: uuid.UUID) -> Decimal:
    """``GET …/lines``' total."""
    response = read(client, who, period)
    assert code(response) == OK, response.text
    return Decimal(response.json()["total"])


def weeks(url: str, tutor: CreatedUser, count: int) -> list[uuid.UUID]:
    """Hourly terms and 120.00 an hour from long ago: ``count`` weeks from the
    week of ``STARTS``."""
    periods = [paid_tutor(url, tutor.id)]
    for index in range(1, count):
        start = 2 + 7 * index
        periods.append(
            add_period_row(
                url,
                tutor.id,
                f"11-{start:02d}",
                f"11-{start + 6:02d}",
                first=False,
            )
        )
    return periods


def worked(
    client: TestClient, owner: Staff, url: str, tutor: CreatedUser, week: int
) -> dict[str, Any]:
    """60 minutes in week ``week`` (0 = the week of ``STARTS``), approved."""
    made = lesson(url, starts_at=STARTS + week * WEEK, tutor=tutor)
    return approved_lesson(client, made, owner.user, minutes=60)


def negative_second_week(
    url: str,
    client: TestClient,
    owner: Staff,
    finance: Staff,
    tutor: CreatedUser,
    source: str,
) -> tuple[list[uuid.UUID], Decimal]:
    """qa-104 O2: 120 minutes finalized in the first week, 60 in each of the
    next two, then a negative adjustment larger than the second week's
    earnings: a re-approval at 30 minutes (``-180.00``), or a ``manual`` of
    ``-500.00``. Returns the weeks and the correction."""
    periods = weeks(url, tutor, 4)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    body = approved_lesson(client, made, owner.user, minutes=120)
    locked(client, url, finance, owner, periods[0])
    worked(client, owner, url, tutor, 1)
    worked(client, owner, url, tutor, 2)
    if source == "correction":
        response = correct_closeout(
            client, owner.cookie, made.id, version_of(body), actual_minutes=30
        )
        assert code_of(response) == OK, response.text
        assert run_job(url) == PostAdjustmentsRunResult(candidates=1, posted=1)
        return periods, Decimal("-180.00")
    response = manual(client, owner, tutor.id, "-500.00", reason="Anticipo")
    assert code(response) == CREATED, response.text
    return periods, Decimal("-500.00")


# --- D1: qa-104 O2, end to end -----


@pytest.mark.parametrize("source", ["correction", "manual"])
def test_d1_a_negative_week_is_carried_and_every_week_finalizes_in_order(
    module_seeded_url, client, owner, finance, tutor, source
):
    url = module_seeded_url
    periods, correction = negative_second_week(
        url, client, owner, finance, tutor, source
    )
    _, second, third, fourth = periods
    earnings = Decimal("240.00")  # 60 minutes in each of weeks two and three

    # The trap: the short week can't prepare, and oldest first stops the next.
    assert code(prepare(client, finance, second)) == NEGATIVE
    assert code(prepare(client, finance, third)) == OK
    assert code(finalize(client, url, owner, third)) == OUT_OF_ORDER
    assert code(return_period(client, finance, third)) == OK

    response = carry(client, finance, second)

    assert code(response) == CREATED, response.text
    carried = response.json()
    assert set(carried) == CARRIED_KEYS
    assert (carried["period_id"], carried["target_period_id"]) == (
        str(second),
        str(third),
    )
    plus, minus = (adjustment_of(url, i) for i in carried["adjustment_ids"])
    # X is the negated total after a recalculation (C2).
    x = -(Decimal("120.00") + correction)
    assert (plus.kind, plus.amount, plus.target_period_id) == (
        "carry_forward",
        x,
        second,
    )
    assert (minus.kind, minus.amount, minus.target_period_id) == (
        "carry_forward",
        -x,
        third,
    )
    assert plus.origin_period_id == minus.origin_period_id == second
    assert (plus.paired_adjustment_id, minus.paired_adjustment_id) == (None, plus.id)
    assert plus.created_by_user_id == minus.created_by_user_id == finance.user.id
    assert str(x) not in response.text
    assert shown_total(client, finance, second) == Decimal("0.00")
    assert shown_total(client, finance, third) == Decimal("120.00") - x

    locked(client, url, finance, owner, second)
    assert row_of(url, second).total_amount == Decimal("0.00")
    if source == "correction":
        locked(client, url, finance, owner, third)
        totals = [row_of(url, p).total_amount for p in (second, third)]
        assert sum(totals) == earnings + correction == Decimal("60.00")
        return

    # The third week is short too: carried again, into the fourth.
    assert code(prepare(client, finance, third)) == NEGATIVE
    again = carry(client, finance, third)
    assert code(again) == CREATED, again.text
    assert again.json()["target_period_id"] == str(fourth)
    locked(client, url, finance, owner, third)
    totals = [row_of(url, p).total_amount for p in (second, third)]
    assert totals == [Decimal("0.00"), Decimal("0.00")]
    assert shown_total(client, finance, fourth) == earnings + correction
    assert status_of(url, fourth) == "open"


def test_d1_a_week_with_no_later_period_gets_one_opened(
    module_seeded_url, client, owner, finance, tutor
):
    """The target is opened as #111 opens one: by the normal rule first."""
    url = module_seeded_url
    [first] = weeks(url, tutor, 1)
    response = manual(client, owner, tutor.id, "-50.00", reason="Anticipo")
    assert response.json()["target_period_id"] == str(first)

    carried = carry(client, finance, first)

    assert code(carried) == CREATED, carried.text
    target = uuid.UUID(carried.json()["target_period_id"])
    assert (str(row_of(url, target).starts_on), status_of(url, target)) == (
        "2026-11-09",
        "open",
    )
    assert shown_total(client, finance, target) == Decimal("-50.00")


# --- D2: a leaver stays negative -----


def test_d2_a_leavers_last_period_stays_negative_until_a_repayment(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    [first] = weeks(url, tutor, 1)
    add_terms(url, tutor.id, "11-09", frequency=None)
    worked(client, owner, url, tutor, 0)
    locked(client, url, finance, owner, first)

    taken = manual(client, owner, tutor.id, "-500.00", reason="Anticipo")
    opened = uuid.UUID(taken.json()["target_period_id"])
    assert str(row_of(url, opened).starts_on) == "2026-11-09"
    assert code(prepare(client, finance, opened)) == NEGATIVE

    carried = carry(client, finance, opened)

    assert code(carried) == CREATED, carried.text
    last = uuid.UUID(carried.json()["target_period_id"])
    assert (str(row_of(url, last).starts_on), str(row_of(url, last).ends_on)) == (
        "2026-11-16",
        "2026-11-22",
    )
    locked(client, url, finance, owner, opened)
    assert row_of(url, opened).total_amount == Decimal("0.00")
    assert code(prepare(client, finance, last)) == NEGATIVE

    repaid = manual(client, owner, tutor.id, "500.00", reason="Devolución recibida")
    assert repaid.json()["target_period_id"] == str(last)
    locked(client, url, finance, owner, last)
    assert row_of(url, last).total_amount == Decimal("0.00")


# --- C1-C3: the refusals, the replay -----


def test_c1_each_refusal_changes_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first, _ = weeks(url, tutor, 2)
    worked(client, owner, url, tutor, 0)

    assert code(carry(client, finance, uuid.uuid4())) == NOT_FOUND
    assert code(carry(client, finance, first)) == NOT_NEGATIVE
    assert code(prepare(client, finance, first)) == OK
    prepared = carry(client, finance, first)
    assert code(prepared) == NOT_OPEN
    assert prepared.json()["details"] == {"status": "prepared"}
    assert code(finalize(client, url, owner, first)) == OK
    assert carry(client, finance, first).json()["details"] == {"status": "finalized"}

    # Not ended: a negative period that ends on or after the center's today.
    newcomer = create_user(url, roles=(TUTOR,))
    add_terms(url, newcomer.id)
    running = add_period_row(url, newcomer.id, "12-07", "12-13")
    taken = manual(client, owner, newcomer.id, "-10.00", reason="Anticipo")
    assert taken.json()["target_period_id"] == str(running)
    unended = carry(client, finance, running)
    assert code(unended) == NOT_ENDED
    assert unended.json()["details"] == {"ends_on": "2026-12-13"}

    assert adjustments(url, tutor.id) == []
    assert [row.kind for row in adjustments(url, newcomer.id)] == ["manual"]
    assert stored_keys(url, finance.user.id) == []


def test_c3_a_replay_answers_the_same_and_a_new_key_finds_nothing_to_carry(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first, _ = weeks(url, tutor, 2)
    manual(client, owner, tutor.id, "-75.00", reason="Anticipo")
    key = new_key()

    done = carry(client, finance, first, key=key)
    replay = carry(client, finance, first, key=key)
    again = carry(client, finance, first)

    assert code(done) == CREATED
    assert code(replay) == CREATED
    assert replay.headers.get(REPLAYED_HEADER) == "true"
    assert replay.json() == done.json()
    assert code(again) == NOT_NEGATIVE
    kinds = [row.kind for row in adjustments(url, tutor.id)]
    assert kinds == ["manual", "carry_forward", "carry_forward"]


# --- Edges -----


def test_a_prepared_week_between_is_skipped_and_still_waits_its_turn(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first, second, third, fourth = weeks(url, tutor, 4)
    locked(client, url, finance, owner, first)
    manual(client, owner, tutor.id, "-40.00", reason="Anticipo")
    assert code(prepare(client, finance, third)) == OK

    carried = carry(client, finance, second)

    assert carried.json()["target_period_id"] == str(fourth)
    assert code(finalize(client, url, owner, third)) == OUT_OF_ORDER
    locked(client, url, finance, owner, second)
    assert code(finalize(client, url, owner, third)) == OK
    assert shown_total(client, finance, fourth) == Decimal("-40.00")


def test_blocking_work_doesnt_stop_a_carry_but_still_stops_prepare(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first = paid_tutor(url, tutor.id, rate=None)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    manual(client, owner, tutor.id, "-40.00", reason="Anticipo")

    assert code(carry(client, finance, first)) == CREATED
    assert code(prepare(client, finance, first)) == UNRESOLVED


# --- E2, E4, E5, audit -----


def test_e4_e5_own_pay_needs_no_reauthentication_and_the_store_holds_no_amount(
    module_seeded_url, client, owner
):
    url = module_seeded_url
    teller = staff(url, client, (FINANCE_STAFF, TUTOR), reauth=False)
    [first] = weeks(url, teller.user, 1)
    manual(client, owner, teller.user.id, "-321.09", reason="Anticipo")

    response = carry(client, teller, first)

    assert code(response) == CREATED, response.text
    [stored] = stored_keys(url, teller.user.id)
    assert set(stored.response_body) == CARRIED_KEYS
    assert "321" not in json.dumps(stored.response_body)
    ids = response.json()["adjustment_ids"]
    audits = audit_rows(url, ids)
    assert [a.action for a in audits] == ["create", "create"]
    for audit in audits:
        assert audit.actor_user_id == teller.user.id
        assert audit.changes["amount"]["new"] == "[masked]"
        assert "321" not in json.dumps(audit.changes, default=str)
