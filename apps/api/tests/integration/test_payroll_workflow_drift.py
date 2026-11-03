"""``blocking_work`` in both orders (#110 I), drift (J, ADR-0017 E10) and P11's
guard (H10). Each change goes through its real route unless it says otherwise."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import FINANCE_STAFF, OWNER, TUTOR
from app.workflows import payroll_adjustments
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import get as get_as
from tests.integration.catalog_support import patch, post
from tests.integration.conftest import run_sql, settings_for
from tests.integration.corrections_support import Staff, staff
from tests.integration.guardian_support import delete
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
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    OK,
    STALE,
    UNRESOLVED,
    code,
    finalize,
    locked,
    new_key,
    prepare,
    recalculate,
    return_period,
    row_of,
    status_of,
)
from tests.integration.test_attendance import lesson
from tests.integration.test_closeout_review import correct_closeout, version_of
from tests.integration.test_payroll_calculation import _store_closeout

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


def worked(url: str, client: TestClient, owner: Staff, tutor: CreatedUser) -> Any:
    period = paid_tutor(url, tutor.id)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    return period


def rate_path(tutor: CreatedUser) -> str:
    return f"/api/v1/payroll/tutors/{tutor.id}/rates"


def add_rate_route(
    client: TestClient,
    who: Staff,
    tutor: CreatedUser,
    on: str,
    rate: str | None = "150",
    *,
    kind: str = "individual",
    model: str | None = "per_hour",
) -> Any:
    return post(
        client,
        who.cookie,
        rate_path(tutor),
        {
            "class_kind": kind,
            "effective_from": on,
            "rate_model": None if rate is None else model,
            "rate": rate,
        },
    )


def stale_with(response: Any, **counts: int) -> None:
    assert code(response) == STALE, response.text
    assert response.json()["details"] == {
        "added": 0,
        "removed": 0,
        "changed": 0,
        **counts,
    }


# --- I: blocking_work, both orders -----


def test_i1_prepare_recalculates_before_it_looks_for_work(
    module_seeded_url, client, owner, finance, tutor
):
    """An open line keeps its work off the list, and the stop makes the line
    drop: only a list read after the run can name that session."""
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user)
    assert code(recalculate(client, finance, period)) == OK
    assert len(lines(url, period)) == 1
    add_terms(url, tutor.id, on="2026-11-01", frequency=None)

    response = prepare(client, finance, period)

    assert code(response) == UNRESOLVED
    assert response.json()["details"] == {
        "count": 1,
        "items": [
            {
                "class_session_id": str(made.id),
                "service_date": "2026-11-03",
                "reason": "no_pay_terms",
            }
        ],
    }
    assert status_of(url, period) == "open"


def test_i1_i2_the_web_way_out_recalculate_commits_and_the_list_agrees(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(recalculate(client, finance, period)) == OK
    add_terms(url, tutor.id, on="2026-11-01", frequency=None)
    assert code(prepare(client, finance, period)) == UNRESOLVED

    assert code(recalculate(client, finance, period)) == OK

    assert lines(url, period) == []
    listed = get_as(
        client,
        finance.cookie,
        "/api/v1/payroll/unresolved-work",
        tutor_user_id=str(tutor.id),
    ).json()["items"]
    assert [item["reason"] for item in listed] == ["no_pay_terms"]


def test_i2_finalize_looks_for_work_again(
    module_seeded_url, client, owner, finance, tutor
):
    """Approved after ``prepare`` for a kind with no basis: no draft, so no drift,
    and only the second look stops it."""
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK
    made = lesson(url, kind="group", starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user)

    response = finalize(client, url, owner, period)

    assert code(response) == UNRESOLVED
    assert response.json()["details"]["items"] == [
        {
            "class_session_id": str(made.id),
            "service_date": "2026-11-03",
            "reason": "no_rate",
        }
    ]
    assert status_of(url, period) == "prepared"


def test_i3_the_same_arrival_with_a_basis_is_drift(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)

    stale_with(finalize(client, url, owner, period), added=1)


def test_i4_an_item_in_a_finalized_period_blocks_a_later_one_until_it_has_a_basis(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    first = add_period_row(url, tutor.id, "11-02", "11-08")
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    locked(client, url, finance, owner, first)
    made = lesson(url, kind="group", starts_at=STARTS, tutor=tutor)
    approved_lesson(client, made, owner.user)

    refused = prepare(client, finance, second)

    assert code(refused) == UNRESOLVED
    assert refused.json()["details"]["items"][0]["class_session_id"] == str(made.id)

    created = add_rate_route(client, owner, tutor, "2026-11-01", "90", kind="group")
    assert created.status_code == 201, created.text

    assert code(prepare(client, finance, second)) == OK


def test_i4_before_first_period_work_never_blocks(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    made = lesson(url, starts_at=datetime(2026, 10, 20, 16, 0, tzinfo=UTC), tutor=tutor)
    _store_closeout(url, made, owner.user)

    assert code(prepare(client, finance, period)) == OK


def test_i4_an_item_dated_after_the_end_doesnt_block(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    add_terms(url, tutor.id)
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    made = lesson(url, starts_at=datetime(2026, 11, 10, 16, 0, tzinfo=UTC), tutor=tutor)
    _store_closeout(url, made, owner.user)

    assert code(prepare(client, finance, period)) == OK


# --- J: each change gives 409, the period stays prepared and can go back -----


def goes_back(url: str, client: TestClient, finance: Staff, period: Any) -> None:
    assert status_of(url, period) == "prepared"
    assert row_of(url, period).total_amount is None
    assert code(return_period(client, finance, period)) == OK


def test_j1_a_terms_change_that_changes_a_salary_line(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    set_floor(url, "11-02")
    period = paid_tutor(url, tutor.id, rate=None, salary="700.00")
    assert code(prepare(client, finance, period)) == OK

    created = post(
        client,
        owner.cookie,
        f"/api/v1/payroll/tutors/{tutor.id}/pay-terms",
        {
            "effective_from": "2026-11-05",
            "pay_frequency": "weekly",
            "salary_amount": "1400.00",
        },
    )
    assert created.status_code == 201, created.text

    stale_with(finalize(client, url, owner, period), added=1, changed=1)
    goes_back(url, client, finance, period)


def test_j2_a_proration_basis_change(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    set_floor(url, "11-02")
    period = paid_tutor(url, tutor.id, rate=None, salary="700.00")
    add_terms(url, tutor.id, "2026-11-05", salary="1400.00")
    assert code(prepare(client, finance, period)) == OK
    settings = get_as(client, owner.cookie, "/api/v1/payroll/settings").json()
    try:
        changed = patch(
            client,
            owner.cookie,
            "/api/v1/payroll/settings",
            {
                "version": settings["version"],
                "payroll_starts_on": settings["payroll_starts_on"],
                "biweekly_anchor_on": settings["biweekly_anchor_on"],
                "salary_proration_basis": "weekdays",
            },
        )
        assert code(changed) == OK, changed.text

        stale_with(finalize(client, url, owner, period), changed=2)
        goes_back(url, client, finance, period)
    finally:
        run_sql(
            url, "UPDATE payroll_settings SET salary_proration_basis = 'calendar_days'"
        )


def test_j3_a_new_closeout_revision(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    closeout = approved_lesson(client, made, owner.user)
    assert code(prepare(client, finance, period)) == OK

    corrected = correct_closeout(
        client, owner.cookie, made.id, version_of(closeout), actual_minutes=30
    )
    assert code(corrected) == OK, corrected.text

    stale_with(finalize(client, url, owner, period), changed=1)
    goes_back(url, client, finance, period)


def test_j4_a_back_dated_rate_into_the_prepared_period(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK

    created = add_rate_route(client, owner, tutor, "2026-11-01")
    assert created.status_code == 201, created.text

    stale_with(finalize(client, url, owner, period), changed=1)
    goes_back(url, client, finance, period)


def item_body(pay_type: Any, **changes: Any) -> dict[str, Any]:
    return {
        "pay_item_type_id": str(pay_type),
        "description": "Bono",
        "amount": "40.00",
        "recurrence": "one_time",
        "effective_from": "2026-11-03",
        **changes,
    }


def create_item(
    client: TestClient, who: Staff, tutor: CreatedUser, body: dict[str, Any]
) -> Any:
    client.cookies.clear()
    return client.post(
        f"/api/v1/payroll/tutors/{tutor.id}/pay-items",
        json=body,
        headers={**signed_headers(who.cookie), "Idempotency-Key": new_key()},
    )


@pytest.mark.parametrize("on", ["2026-11-03", "2026-10-15"])
def test_j5_an_item_created_or_dated_into_the_prepared_period(
    module_seeded_url, client, owner, finance, tutor, on
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK

    created = create_item(
        client, owner, tutor, item_body(add_pay_type(url), effective_from=on)
    )
    assert created.status_code == 201, created.text

    stale_with(finalize(client, url, owner, period), added=1)
    goes_back(url, client, finance, period)


def test_j6_a_recurring_item_ended_before_the_period(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    created = create_item(
        client,
        owner,
        tutor,
        item_body(
            add_pay_type(url), recurrence="recurring", effective_from="2026-10-01"
        ),
    )
    assert created.status_code == 201, created.text
    assert code(prepare(client, finance, period)) == OK

    ended = patch(
        client,
        owner.cookie,
        f"/api/v1/payroll/tutors/{tutor.id}/pay-items/{created.json()['id']}",
        {"version": 1, "effective_to": "2026-10-31"},
    )
    assert code(ended) == OK, ended.text

    stale_with(finalize(client, url, owner, period), removed=1)
    goes_back(url, client, finance, period)


def test_j6_a_deleted_item_with_a_line_is_refused(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    item = add_item(url, tutor.id, add_pay_type(url))
    assert code(prepare(client, finance, period)) == OK

    response = delete(
        client, owner.cookie, f"/api/v1/payroll/tutors/{tutor.id}/pay-items/{item}"
    )

    assert code(response) == (409, "pay_item_in_use")
    assert code(finalize(client, url, owner, period)) == OK


# --- J8, J9: no false alarm -----


def test_j8_a_salary_a_covered_line_and_an_item_prepare_and_finalize(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    set_floor(url, "11-02")
    period = paid_tutor(url, tutor.id, rate=None, salary="700.00")
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    add_item(url, tutor.id, add_pay_type(url), "40.00")
    add_item(
        url,
        tutor.id,
        add_pay_type(url, direction="deduction"),
        "5.00",
        on="10-01",
        recurrence="recurring",
    )

    locked(client, url, finance, owner, period)

    assert sorted(line.kind for line in lines(url, period)) == [
        "pay_item",
        "pay_item",
        "salary",
        "session",
    ]
    assert row_of(url, period).total_amount == Decimal("735.00")


def test_j8_a_zero_salary_piece_and_a_stub(
    module_seeded_url, client, owner, finance, tutor
):
    """One centavo a week: six days round to 0.01 and the last to 0.00. A short
    period prorates on its natural week."""
    url = module_seeded_url
    set_floor(url, "11-04")
    period = paid_tutor(url, tutor.id, "11-04", "11-08", rate=None, salary="0.01")
    add_terms(url, tutor.id, "2026-11-08", salary="0.02")

    locked(client, url, finance, owner, period)

    pieces = [line.amount for line in lines(url, period)]
    assert Decimal("0.00") in pieces
    assert len(pieces) == 2


def test_j8_a_one_time_item_stays_in_its_prepared_period(
    module_seeded_url, client, owner, finance, tutor
):
    """``compute_lines`` places it in the prepared period again, and reads it
    ``FOR KEY SHARE``."""
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    add_item(url, tutor.id, add_pay_type(url), "40.00", on="11-03")

    locked(client, url, finance, owner, first)
    locked(client, url, finance, owner, second)

    assert row_of(url, first).total_amount == Decimal("40.00")
    assert row_of(url, second).total_amount == Decimal("0.00")


def test_j9_the_counts_add_up(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    closeout = approved_lesson(client, made, owner.user)
    add_item(url, tutor.id, add_pay_type(url), "40.00")
    gone = add_item(
        url,
        tutor.id,
        add_pay_type(url),
        "5.00",
        on="10-01",
        recurrence="recurring",
    )
    assert code(prepare(client, finance, period)) == OK

    correct = correct_closeout(
        client, owner.cookie, made.id, version_of(closeout), actual_minutes=30
    )
    assert code(correct) == OK, correct.text
    run_sql(
        url,
        "UPDATE pay_items SET effective_to = '2026-10-31' WHERE id = :i",
        {"i": gone},
    )
    add_item(url, tutor.id, add_pay_type(url), "1.00")

    stale_with(finalize(client, url, owner, period), added=1, removed=1, changed=1)


def test_d3_d4_a_surviving_line_is_outside_drift_and_inside_the_total(
    module_seeded_url, client, owner, finance, tutor
):
    """#111 F3, on a real row: the job's ``late_approval`` line is in the period,
    ``prepare`` and ``finalize`` see no drift, and the total counts it."""
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    made = lesson(url, starts_at=STARTS, tutor=tutor)
    locked(client, url, finance, owner, first)
    approved_lesson(client, made, owner.user)
    asyncio.run(payroll_adjustments.run_post_adjustments(settings_for(url)))
    assert [line.kind for line in lines(url, second)] == ["adjustment"]
    assert code(prepare(client, finance, second)) == OK

    assert code(finalize(client, url, owner, second)) == OK

    assert row_of(url, second).total_amount == Decimal("110.00")


# --- H10: P11's guard -----


def line_in(url: str, period: Any, tutor: CreatedUser, item: Any) -> None:
    """By SQL: **no route writes this line**. Placement reaches a later period
    only once the earlier one is finalized (#110 H10)."""
    run_sql(
        url,
        "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind, amount,"
        " pay_item_id) VALUES (:p, :u, 'pay_item', 40, :i)",
        {"p": period, "u": tutor.id, "i": item},
    )


def test_h10_a_line_in_a_later_open_period_is_dropped_and_pays_once(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    item = add_item(url, tutor.id, add_pay_type(url), "40.00", on="11-03")
    line_in(url, second, tutor, item)

    locked(client, url, finance, owner, first)
    locked(client, url, finance, owner, second)

    assert lines(url, second) == []
    assert row_of(url, first).total_amount + row_of(
        url, second
    ).total_amount == Decimal("40.00")


def test_h10_a_later_period_already_prepared_goes_stale(
    module_seeded_url, client, frozen, owner, finance, tutor
):
    url = module_seeded_url
    first = paid_tutor(url, tutor.id)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    item = add_item(url, tutor.id, add_pay_type(url), "40.00", on="11-03")
    assert code(prepare(client, finance, second)) == OK
    line_in(url, second, tutor, item)
    locked(client, url, finance, owner, first)

    stale_with(finalize(client, url, owner, second), removed=1)

    assert code(return_period(client, finance, second)) == OK
    frozen.set(NOW + timedelta(seconds=1))
    locked(client, url, finance, owner, second)
    assert row_of(url, second).total_amount == Decimal("0.00")
