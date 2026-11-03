"""``prepare``, ``return`` and ``finalize`` through their routes, the inbox type
and the notification (#110 B, C, D, G, K1-K3, O1; ADR-0017 E9, E10).

Everything here runs on real rows. A period only gets to ``finalized`` by the
route.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.modules.administration import service as administration
from app.modules.identity.permissions import (
    ADMINISTRATOR,
    FINANCE_STAFF,
    OWNER,
    TUTOR,
)
from app.modules.payroll.errors import PayrollReviewRouteRequiredError
from app.workflows.payroll_period_approval import PAYROLL_PERIOD_HANDLER
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import get as get_as
from tests.integration.conftest import run_sql
from tests.integration.corrections_support import Staff, staff
from tests.integration.guardian_support import delete as delete_as
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
)
from tests.integration.payroll_period_support import set_floor
from tests.integration.payroll_workflow_support import (
    APPROVALS,
    NOT_OPEN,
    NOT_PREPARED,
    OK,
    ROOT,
    UNRESOLVED,
    actions,
    code,
    events,
    finalize,
    line_ids,
    locked,
    name_of,
    new_key,
    notifications_of,
    prepare,
    prepared_at,
    read,
    recalculate,
    request_of,
    return_period,
    row_of,
    status_of,
)
from tests.integration.test_attendance import lesson, principal_of
from tests.integration.test_payroll_calculation import _store_closeout

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
GONE = "00000000-0000-7000-8000-000000000000"
TRANSITION_KEYS = {"id", "status", "prepared_at", "finalized_at"}


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


@pytest.fixture(scope="module")
def delegate(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(
        module_seeded_url,
        client,
        (ADMINISTRATOR,),
        delegations=["payroll.finalize"],
    )


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


def worked(url: str, client: TestClient, owner: Staff, tutor: CreatedUser) -> Any:
    """A period with one approved 55-minute lesson at 120 an hour: 110.00."""
    period = paid_tutor(url, tutor.id)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    return period


# --- B: prepare -----


def test_b1_b7_prepare_builds_the_lines_and_opens_the_review(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)

    response = prepare(client, finance, period)

    assert code(response) == OK, response.text
    body = response.json()
    assert set(body) == TRANSITION_KEYS
    assert (body["status"], body["finalized_at"]) == ("prepared", None)
    row = row_of(url, period)
    assert (row.status, row.prepared_at, row.prepared_by_user_id) == (
        "prepared",
        NOW,
        finance.user.id,
    )
    assert [line.amount for line in lines(url, period)] == [Decimal("110.00")]
    [event] = events(url, period)
    assert event.action == "payroll.period_prepared"
    assert event.details == {
        "payroll_period_id": str(period),
        "tutor_user_id": str(tutor.id),
        "status": "prepared",
        "line_count": 1,
    }
    request = request_of(url, period)
    assert (request.status, request.requester_user_id) == ("pending", finance.user.id)


def test_b2_an_unknown_period_is_404(client, finance):
    assert code(prepare(client, finance, GONE)) == (404, "not_found")


@pytest.mark.parametrize("state", ["prepared", "finalized"])
def test_b2_only_an_open_period_is_prepared(
    module_seeded_url, client, owner, finance, tutor, state
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    if state == "finalized":
        assert code(finalize(client, url, owner, period)) == OK

    response = prepare(client, finance, period)

    assert code(response) == NOT_OPEN
    assert response.json()["details"] == {"status": state}
    assert "isn't open" in response.json()["detail"]
    assert "recalculated" not in response.json()["detail"]


def test_b3_the_last_day_refuses_and_the_next_passes_in_the_centers_zone(
    module_seeded_url, client, frozen, tutor
):
    """The center is six hours behind UTC, so 05:59 UTC on the 8th is still the
    period's last day there, though UTC has moved on."""
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, "12-01", "12-07")
    frozen.set(datetime(2026, 12, 8, 5, 59, tzinfo=UTC))
    fresh = staff(url, client, (FINANCE_STAFF,), reauth=False)

    refused = prepare(client, fresh, period)

    assert code(refused) == (409, "payroll_period_not_ended")
    assert refused.json()["details"] == {"ends_on": "2026-12-07"}
    frozen.set(datetime(2026, 12, 8, 6, 0, tzinfo=UTC))
    assert code(prepare(client, fresh, period)) == OK


def test_b4_unresolved_work_names_at_most_twenty_newest_first(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = add_period_row(url, tutor.id, "10-01", "11-30", "monthly")
    add_terms(url, tutor.id, on="2000-01-01", frequency=None)
    made = [
        lesson(url, starts_at=datetime(2026, 11, 1 + i, 16, 0, tzinfo=UTC), tutor=tutor)
        for i in range(22)
    ]
    for each in made:
        _store_closeout(url, each, owner.user)

    response = prepare(client, finance, period)

    assert code(response) == UNRESOLVED
    details = response.json()["details"]
    assert details["count"] == 22
    assert details["items"][0] == {
        "class_session_id": str(made[-1].id),
        "service_date": "2026-11-22",
        "reason": "no_pay_terms",
    }
    assert [item["class_session_id"] for item in details["items"]] == [
        str(each.id) for each in reversed(made[2:])
    ]
    assert "amount" not in response.text


def test_b5_a_negative_total_refuses_with_a_real_deduction(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    add_item(url, tutor.id, add_pay_type(url, direction="deduction"), "110.01")

    response = prepare(client, finance, period)

    assert code(response) == (409, "payroll_negative_total")
    assert "110" not in response.text
    assert "-0.01" not in response.text


def test_b5_a_zero_total_passes_and_finalizes_with_lines(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    add_item(url, tutor.id, add_pay_type(url, direction="deduction"), "110.00")

    locked(client, url, finance, owner, period)

    assert row_of(url, period).total_amount == Decimal("0.00")


def test_b5_an_empty_period_is_prepared_and_finalized_at_zero(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)

    locked(client, url, finance, owner, period)

    assert lines(url, period) == []
    row = row_of(url, period)
    assert (row.status, row.total_amount) == ("finalized", Decimal("0.00"))


def nothing_changed(url: str, period: Any, ids: list[Any]) -> None:
    assert line_ids(url, period) == ids
    assert status_of(url, period) == "open"
    assert actions(url, period) == []
    assert request_of(url, period) is None
    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM audit_events WHERE record_id = :p"
            " AND action = 'payroll.period_recalculated'",
            {"p": period},
        )[0].n
        == 1
    )


def test_b6_a_refusal_for_unresolved_work_changes_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    """#110 E3: the run dropped the line, the item appeared, and the refusal
    brings the line back."""
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(recalculate(client, finance, period)) == OK
    ids = line_ids(url, period)
    assert len(ids) == 1
    add_terms(url, tutor.id, on="2026-11-01", frequency=None)

    assert code(prepare(client, finance, period)) == UNRESOLVED

    nothing_changed(url, period, ids)


def test_b6_a_refusal_for_a_negative_total_changes_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(recalculate(client, finance, period)) == OK
    ids = line_ids(url, period)
    add_item(url, tutor.id, add_pay_type(url, direction="deduction"), "500.00")

    assert code(prepare(client, finance, period)) == (409, "payroll_negative_total")

    nothing_changed(url, period, ids)


def test_b6_a_refusal_for_the_money_range_changes_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(recalculate(client, finance, period)) == OK
    ids = line_ids(url, period)
    pay_type = add_pay_type(url)
    add_item(url, tutor.id, pay_type, "999999999999.99")

    assert code(prepare(client, finance, period)) == (
        409,
        "payroll_amount_out_of_range",
    )

    nothing_changed(url, period, ids)


def test_b6_a_refusal_before_the_end_changes_nothing(
    module_seeded_url, client, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id, "12-01", "12-13")
    assert code(recalculate(client, finance, period)) == OK

    assert code(prepare(client, finance, period)) == (409, "payroll_period_not_ended")

    nothing_changed(url, period, [])


# --- C: return -----


def test_c3_return_keeps_the_lines_and_cancels_the_review(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    ids = line_ids(url, period)

    response = return_period(client, owner, period, "  Revisar la sesión del 3  ")

    assert code(response) == OK, response.text
    assert set(response.json()) == TRANSITION_KEYS
    row = row_of(url, period)
    assert (row.status, row.prepared_at, row.prepared_by_user_id) == (
        "open",
        None,
        None,
    )
    assert (row.returned_at, row.returned_by_user_id, row.return_reason) == (
        NOW,
        owner.user.id,
        "Revisar la sesión del 3",
    )
    assert line_ids(url, period) == ids
    request = request_of(url, period)
    assert (request.status, request.resolved_by_user_id) == ("cancelled", owner.user.id)
    assert actions(url, period) == [
        "payroll.period_prepared",
        "payroll.period_returned",
    ]
    assert "Revisar" not in str(events(url, period)[-1].details)
    assert events(url, period)[-1].details["line_count"] == 1


@pytest.mark.parametrize("state", ["open", "finalized"])
def test_c2_only_a_prepared_period_is_returned(
    module_seeded_url, client, owner, finance, tutor, state
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    if state == "finalized":
        locked(client, url, finance, owner, period)

    response = return_period(client, finance, period)

    assert code(response) == NOT_PREPARED
    assert response.json()["details"] == {"status": state}


def test_c2_a_second_click_is_refused(module_seeded_url, client, finance, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK
    assert code(return_period(client, finance, period)) == OK

    assert code(return_period(client, finance, period)) == NOT_PREPARED
    assert actions(url, period).count("payroll.period_returned") == 1


def test_c2_an_unknown_period_is_404(client, finance):
    assert code(return_period(client, finance, GONE)) == (404, "not_found")


@pytest.mark.parametrize(
    ("reason", "problem"),
    [
        ("   ", "text_empty"),
        ("x" * 1001, "text_too_long"),
        ("con\x00nulo", "text_invalid"),
        ("\ud800", "text_invalid"),
    ],
)
def test_c1_the_reason_is_checked(
    module_seeded_url, client, finance, tutor, reason, problem
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK

    response = client.post(
        f"{ROOT}/{period}/return",
        content=json.dumps({"reason": reason}),
        headers={**signed_headers(finance.cookie), "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert problem in response.text
    assert status_of(url, period) == "prepared"


def test_c1_the_longest_reason_fits(module_seeded_url, client, finance, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK

    assert code(return_period(client, finance, period, "x" * 1000)) == OK


def test_c4_preparing_again_reopens_the_same_request_and_clears_the_reason(
    module_seeded_url, client, frozen, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    first = request_of(url, period).id
    old_review = prepared_at(url, period)
    assert code(return_period(client, finance, period)) == OK
    frozen.set(datetime(2026, 12, 7, 18, 1, tzinfo=UTC))

    assert code(prepare(client, finance, period)) == OK

    request = request_of(url, period)
    assert (request.id, request.status, request.resolved_at) == (first, "pending", None)
    row = row_of(url, period)
    assert (row.returned_at, row.returned_by_user_id, row.return_reason) == (
        None,
        None,
        None,
    )
    assert code(finalize(client, url, owner, period, at=old_review)) == (
        409,
        "payroll_review_outdated",
    )


# --- D: finalize -----


def test_d5_finalize_locks_the_period_in_one_transaction(
    module_seeded_url, client, frozen, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    frozen.set(datetime(2026, 12, 7, 18, 2, tzinfo=UTC))

    response = finalize(client, url, owner, period)

    assert code(response) == OK, response.text
    assert set(response.json()) == TRANSITION_KEYS
    assert response.json()["status"] == "finalized"
    row = row_of(url, period)
    assert (row.status, row.total_amount, row.finalized_by_user_id) == (
        "finalized",
        Decimal("110.00"),
        owner.user.id,
    )
    assert row.finalized_at == datetime(2026, 12, 7, 18, 2, tzinfo=UTC)
    event = events(url, period)[-1]
    assert event.action == "payroll.period_finalized"
    assert event.details == {
        "payroll_period_id": str(period),
        "tutor_user_id": str(tutor.id),
        "status": "finalized",
        "line_count": 1,
    }
    request = request_of(url, period)
    assert (request.status, request.resolved_by_user_id) == ("approved", owner.user.id)
    [notice] = notifications_of(url, tutor.id)
    assert notice.params == {
        "payroll_period_id": str(period),
        "starts_on": "2026-11-02",
        "ends_on": "2026-11-08",
    }


def test_d4_the_total_is_all_the_lines_the_same_as_the_read(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    add_item(url, tutor.id, add_pay_type(url), "40.50")
    add_item(url, tutor.id, add_pay_type(url, direction="deduction"), "10.25")

    locked(client, url, finance, owner, period)

    assert row_of(url, period).total_amount == Decimal("140.25")
    body = read(client, finance, period).json()
    assert body["total"] == "140.25"
    assert body["period"]["total_amount"] == "140.25"


def test_d6_a_second_finalize_with_another_key_changes_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    locked(client, url, finance, owner, period)

    response = finalize(client, url, owner, period, key=new_key())

    assert code(response) == NOT_PREPARED
    assert response.json()["details"] == {"status": "finalized"}
    assert actions(url, period).count("payroll.period_finalized") == 1
    assert len(notifications_of(url, tutor.id)) == 1


@pytest.mark.parametrize("state", ["open"])
def test_d2_an_open_period_is_not_finalized(
    module_seeded_url, client, owner, tutor, state
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)

    response = finalize(client, url, owner, period)

    assert code(response) == NOT_PREPARED
    assert response.json()["details"] == {"status": state}


def test_d2_an_unknown_period_is_404(module_seeded_url, client, owner):
    response = finalize(
        client, module_seeded_url, owner, GONE, at="2026-12-07T18:00:00+00:00"
    )
    assert code(response) == (404, "not_found")


# --- G: the inbox and the notification -----


def test_g1_g3_the_inbox_lists_it_with_no_amount_and_no_decisions(
    module_seeded_url, client, owner, finance, delegate, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    request = request_of(url, period)

    for who in (owner, delegate):
        response = get_as(client, who.cookie, f"{APPROVALS}/{request.id}")
        assert code(response) == OK, response.text
        body = response.json()
        assert body["allowed_actions"] == []
        assert body["subject"] == {
            "period_id": str(period),
            "tutor": {"id": str(tutor.id), "display_name": "Tutora Lista"},
            "starts_on": "2026-11-02",
            "ends_on": "2026-11-08",
            "prepared_by": {"display_name": name_of(url, finance.user.id)},
            "prepared_at": NOW.isoformat(),
        }
        assert "110" not in str(body["subject"])
    assert code(get_as(client, finance.cookie, f"{APPROVALS}/{request.id}")) == (
        404,
        "not_found",
    )


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("approve", None),
        ("reject", {"reason": "No"}),
        ("request-changes", {"reason": "No"}),
    ],
)
@pytest.mark.parametrize("state", ["pending", "approved", "cancelled"])
def test_g2_every_generic_route_sends_to_the_page(
    module_seeded_url, client, owner, finance, tutor, action, body, state
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK
    if state == "approved":
        assert code(finalize(client, url, owner, period)) == OK
    if state == "cancelled":
        assert code(return_period(client, finance, period)) == OK
    request = request_of(url, period)

    client.cookies.clear()
    response = client.post(
        f"{APPROVALS}/{request.id}/{action}",
        json=body,
        headers=signed_headers(owner.cookie),
    )

    assert code(response) == (409, "payroll_review_route_required")
    assert response.json()["details"] == {"review_path": f"/payroll/periods/{period}"}
    assert request_of(url, period).status == state


def test_g2_the_handler_approves_only_a_finalized_period(
    module_seeded_url, client, owner, finance, tutor
):
    """No route gets here on a period that isn't finalized: the handler is the
    backstop behind ``finalize`` and the inbox hook, so it is called directly."""
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK
    request = request_of(url, period)
    view = administration.ApprovalRequestView(
        id=request.id,
        request_type=request.request_type,
        subject_id=period,
        requester_user_id=finance.user.id,
        status="pending",
    )
    who = principal_of(owner.user.id, OWNER)

    def approving(session: Any) -> Any:
        return PAYROLL_PERIOD_HANDLER.approve(session, view, who)

    with pytest.raises(PayrollReviewRouteRequiredError):
        in_uow(url, approving)
    with pytest.raises(PayrollReviewRouteRequiredError):
        in_uow(url, lambda s: PAYROLL_PERIOD_HANDLER.reject(s, view, who, "No"))
    with pytest.raises(PayrollReviewRouteRequiredError):
        in_uow(
            url, lambda s: PAYROLL_PERIOD_HANDLER.request_changes(s, view, who, "No")
        )

    assert code(finalize(client, url, owner, period)) == OK

    in_uow(url, approving)


def test_g4_a_tutor_who_left_still_gets_the_notification(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    run_sql(url, "UPDATE users SET state = 'archived' WHERE id = :u", {"u": tutor.id})

    locked(client, url, finance, owner, period)

    assert len(notifications_of(url, tutor.id)) == 1


# --- K1-K3: the lock on a period finalized by the route -----


@pytest.fixture
def locked_period(module_seeded_url, client, owner, finance, tutor) -> Any:
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    add_item(url, tutor.id, add_pay_type(url), "40.00")
    locked(client, url, finance, owner, period)
    return period


def sqlstate(url: str, statement: str, params: Any = None) -> str:
    with pytest.raises(DBAPIError) as caught:
        run_sql(url, statement, params or {})
    return str(getattr(caught.value.orig, "sqlstate", ""))


def test_k1_the_guards_refuse_every_write(module_seeded_url, tutor, locked_period):
    url = module_seeded_url
    period = {"p": locked_period, "u": tutor.id}
    for statement in (
        "UPDATE payroll_lines SET amount = 1 WHERE payroll_period_id = :p",
        "DELETE FROM payroll_lines WHERE payroll_period_id = :p",
        "INSERT INTO payroll_lines (payroll_period_id, tutor_user_id, kind, amount,"
        " pay_item_id) SELECT :p, :u, 'pay_item', 1, id FROM pay_items LIMIT 1",
        "UPDATE payroll_periods SET total_amount = 1 WHERE id = :p",
        "DELETE FROM payroll_periods WHERE id = :p",
    ):
        assert sqlstate(url, statement, period) == "42501", statement
    assert sqlstate(url, "TRUNCATE payroll_lines CASCADE") == "42501"
    assert sqlstate(url, "TRUNCATE payroll_periods CASCADE") == "42501"


def test_k1_a_payment_can_still_lock_the_row(module_seeded_url, locked_period):
    url = module_seeded_url

    [row] = run_sql(
        url,
        "SELECT id FROM payroll_periods WHERE id = :p FOR UPDATE",
        {"p": locked_period},
    )

    assert row.id == locked_period


def test_k2_no_route_moves_it(module_seeded_url, client, finance, locked_period):
    period = locked_period
    assert code(recalculate(client, finance, period)) == NOT_OPEN
    assert code(prepare(client, finance, period)) == NOT_OPEN
    assert code(return_period(client, finance, period)) == NOT_PREPARED


def test_k3_the_guards_go_live_on_real_data(
    module_seeded_url, client, owner, finance, tutor
):
    """Deleting what a finalized line uses is the matching ``409``, and a sibling
    version stays writable."""
    url = module_seeded_url
    terms = add_terms(url, tutor.id)
    rate = add_rate(url, tutor.id)
    sibling_rate = add_rate(url, tutor.id, "2026-12-01", "150")
    pay_type = add_pay_type(url)
    item = add_item(url, tutor.id, pay_type, "40.00")
    sibling_item = add_item(url, tutor.id, pay_type, "5.00", on="2027-01-01")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    assert terms
    locked(client, url, finance, owner, period)
    base = f"/api/v1/payroll/tutors/{tutor.id}"

    def gone(path: str) -> tuple[int, str | None]:
        return code(delete_as(client, owner.cookie, path))

    assert gone(f"{base}/rates/{rate}") == (409, "compensation_rate_in_use")
    assert gone(f"{base}/pay-items/{item}") == (409, "pay_item_in_use")
    assert gone(f"{base}/rates/{sibling_rate}") == (204, None)
    assert gone(f"{base}/pay-items/{sibling_item}") == (204, None)


def test_k3_a_salary_terms_version_a_finalized_line_uses_stays(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    set_floor(url, "11-02")
    terms = add_terms(url, tutor.id, salary="700.00")
    period = add_period_row(url, tutor.id, "11-02", "11-08")
    locked(client, url, finance, owner, period)
    sibling = add_terms(url, tutor.id, "2027-01-04")
    base = f"/api/v1/payroll/tutors/{tutor.id}/pay-terms"

    response = delete_as(client, owner.cookie, f"{base}/{terms}")

    assert code(response) == (409, "pay_terms_in_use")
    assert response.json()["detail"] == "A payroll period uses these pay terms."
    assert code(delete_as(client, owner.cookie, f"{base}/{sibling}")) == (204, None)
