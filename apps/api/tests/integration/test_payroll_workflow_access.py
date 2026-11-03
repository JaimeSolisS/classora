"""Who may do what (#110 N, F, E8, E11), duty separation, reauthentication,
idempotency and what stays out of side channels. The permission matrix covers the
placeholder cases; here every row of the table runs on a real period."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.idempotency import REPLAYED_HEADER
from app.core.pagination import OffsetParams
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.workflows import payroll_workflow
from tests.conftest import FrozenClock, frozen_clock
from tests.integration.auth_support import (
    CreatedUser,
    auth_client,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements, get
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
    paid_tutor,
)
from tests.integration.payroll_workflow_support import (
    OK,
    ROOT,
    actions,
    code,
    finalize,
    locked,
    name_of,
    new_key,
    notifications_of,
    prepare,
    prepared_at,
    read,
    recalculate,
    return_period,
    status_of,
)
from tests.integration.test_attendance import lesson, principal_of

NOW = datetime(2026, 12, 7, 18, 0, tzinfo=UTC)
STARTS = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
DENIED = (403, "permission_denied")
SELF = (403, "payroll_self_finalize")
REAUTH = (403, "reauth_required")
HIDDEN = (404, "not_found")
ROLES = {
    "administrator": (ADMINISTRATOR,),
    "coordinator": (ACADEMIC_COORDINATOR,),
    "tutor": (TUTOR,),
    "student": (STUDENT,),
    "guardian": (GUARDIAN,),
}


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
    clear(module_seeded_url)


@pytest.fixture(scope="module")
def owner(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (OWNER,))


@pytest.fixture(scope="module")
def finance(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(module_seeded_url, client, (FINANCE_STAFF,))


@pytest.fixture(scope="module")
def delegate(module_seeded_url: str, client: TestClient) -> Staff:
    return staff(
        module_seeded_url, client, (ADMINISTRATOR,), delegations=["payroll.finalize"]
    )


@pytest.fixture(scope="module")
def others(module_seeded_url: str, client: TestClient) -> dict[str, Staff]:
    return {
        name: staff(module_seeded_url, client, roles) for name, roles in ROLES.items()
    }


@pytest.fixture
def tutor(module_seeded_url: str) -> CreatedUser:
    return create_user(module_seeded_url, roles=(TUTOR,), display_name="Tutora Lista")


def worked(url: str, client: TestClient, owner: Staff, tutor: CreatedUser) -> Any:
    period = paid_tutor(url, tutor.id)
    approved_lesson(client, lesson(url, starts_at=STARTS, tutor=tutor), owner.user)
    return period


# --- N1: every row of the table -----


@pytest.mark.parametrize("name", list(ROLES))
def test_n1_everyone_else_gets_403_on_every_route(
    module_seeded_url, client, finance, others, tutor, name
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    who = others[name]

    assert code(get(client, who.cookie, ROOT)) == DENIED
    assert code(read(client, who, period)) == DENIED
    assert code(recalculate(client, who, period)) == DENIED
    assert code(prepare(client, who, period)) == DENIED
    assert code(prepare(client, finance, period)) == OK
    assert code(read(client, who, period)) == DENIED
    assert code(return_period(client, who, period)) == DENIED
    assert code(finalize(client, url, who, period)) == DENIED
    assert status_of(url, period) == "prepared"


def test_n1_a_tutor_reads_nothing_here_not_even_their_own_period(
    module_seeded_url, client, finance
):
    url = module_seeded_url
    teacher = staff(url, client, (TUTOR,))
    period = paid_tutor(url, teacher.user.id)
    assert code(prepare(client, finance, period)) == OK

    assert code(read(client, teacher, period)) == DENIED
    assert code(get(client, teacher.cookie, ROOT)) == DENIED


def test_n1_finance_prepares_returns_and_reads_but_cant_finalize(
    module_seeded_url, client, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(recalculate(client, finance, period)) == OK
    assert code(read(client, finance, period)) == OK
    assert code(prepare(client, finance, period)) == OK
    assert code(finalize(client, url, finance, period)) == DENIED
    assert code(return_period(client, finance, period)) == OK


def test_n1_a_delegated_administrator_finalizes_but_cant_prepare(
    module_seeded_url, client, finance, delegate, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(recalculate(client, delegate, period)) == DENIED
    assert code(prepare(client, delegate, period)) == DENIED
    assert code(prepare(client, finance, period)) == OK
    assert code(return_period(client, delegate, period)) == OK
    assert code(prepare(client, finance, period)) == OK
    assert code(finalize(client, url, delegate, period)) == OK


def test_e11_a_finance_tutor_prepares_their_own_period_and_someone_else_finalizes(
    module_seeded_url, client, delegate
):
    url = module_seeded_url
    both = staff(url, client, (FINANCE_STAFF, TUTOR))
    period = paid_tutor(url, both.user.id)

    assert code(prepare(client, both, period)) == OK
    assert code(finalize(client, url, delegate, period)) == OK
    assert status_of(url, period) == "finalized"


# --- E8, F: what a delegated Administrator reads -----


def test_n2_e8_an_open_period_is_the_same_404_as_a_missing_one(
    module_seeded_url, client, finance, delegate, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(read(client, delegate, period)) == HIDDEN
    assert (
        code(read(client, delegate, "01a19e7e-f0b8-70b4-bc3e-86d6bf528ed6")) == HIDDEN
    )
    assert code(get(client, delegate.cookie, ROOT, status="open")) == OK
    assert get(client, delegate.cookie, ROOT, status="open").json()["items"] == []

    assert code(prepare(client, finance, period)) == OK
    assert read(client, delegate, period).json()["period"]["status"] == "prepared"
    assert code(finalize(client, url, delegate, period)) == OK
    assert read(client, delegate, period).json()["period"]["status"] == "finalized"


def test_e8_a_hidden_period_cant_answer_the_lines_409(
    module_seeded_url, client, delegate, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    pay_type = add_pay_type(url)
    add_item(
        url, tutor.id, pay_type, "999999999999.99", on="10-01", recurrence="recurring"
    )
    add_item(
        url, tutor.id, pay_type, "999999999999.99", on="10-01", recurrence="recurring"
    )
    add_item(
        url,
        tutor.id,
        add_pay_type(url, direction="deduction"),
        "999999999999.99",
    )
    assert code(recalculate(client, finance, period)) == OK
    run_sql(url, "DELETE FROM payroll_lines WHERE amount < 0")

    assert code(read(client, finance, period)) == (409, "payroll_amount_out_of_range")
    assert code(read(client, delegate, period)) == HIDDEN


def test_e8_nothing_else_opens_to_a_delegated_administrator(
    client, delegate, module_seeded_url, tutor
):
    paid_tutor(module_seeded_url, tutor.id)
    base = "/api/v1/payroll"
    assert code(get(client, delegate.cookie, f"{base}/unresolved-work")) == DENIED
    assert code(get(client, delegate.cookie, f"{base}/settings")) == DENIED
    assert code(
        get(client, delegate.cookie, f"{base}/tutors/{tutor.id}/pay-items")
    ) == (DENIED)
    assert code(
        get(client, delegate.cookie, f"{base}/tutors/{tutor.id}/compensation")
    ) == (HIDDEN)


def test_f1_the_list_filters_orders_and_names_the_tutor(
    module_seeded_url, client, owner, finance, delegate, tutor
):
    url = module_seeded_url
    other = create_user(url, roles=(TUTOR,), display_name="Otra Tutora")
    first = worked(url, client, owner, tutor)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    third = paid_tutor(url, other.id, "11-02", "11-08")
    run_sql(url, "UPDATE users SET state = 'archived' WHERE id = :u", {"u": other.id})
    assert code(prepare(client, finance, first)) == OK

    everything = get(client, owner.cookie, ROOT).json()

    assert everything["total"] == 3
    assert everything["items"][0]["id"] == str(second)
    assert {item["id"] for item in everything["items"][1:]} == {str(first), str(third)}
    [row] = [item for item in everything["items"] if item["id"] == str(first)]
    assert row == {
        "id": str(first),
        "tutor_user_id": str(tutor.id),
        "tutor_display_name": "Tutora Lista",
        "tutor_state": "active",
        "starts_on": "2026-11-02",
        "ends_on": "2026-11-08",
        "pay_frequency": "weekly",
        "status": "prepared",
        "line_count": 1,
        "total": "110.00",
        "prepared_at": NOW.isoformat().replace("+00:00", "Z"),
        "finalized_at": None,
        "paid_total": None,
        "paid_state": None,
    }
    [archived] = get(client, owner.cookie, ROOT, tutor_user_id=str(other.id)).json()[
        "items"
    ]
    assert archived["tutor_state"] == "archived"
    [prepared] = get(client, finance.cookie, ROOT, status="prepared").json()["items"]
    assert prepared["id"] == str(first)
    assert [
        item["id"] for item in get(client, delegate.cookie, ROOT).json()["items"]
    ] == [str(first)]
    assert get(client, owner.cookie, ROOT, status="nope").status_code == 422


def test_f1_the_list_statements_dont_grow_with_the_page(module_seeded_url, owner):
    url = module_seeded_url
    principal = principal_of(owner.user.id, OWNER)

    def page(session: Any) -> Any:
        return payroll_workflow.list_periods(
            session,
            principal,
            page=OffsetParams(limit=25, offset=0),
            status=None,
            tutor_user_id=None,
        )

    def tutors(count: int) -> None:
        for _ in range(count):
            paid_tutor(url, create_user(url, roles=(TUTOR,)).id)

    tutors(1)
    one, _ = count_statements(url, page)
    tutors(4)
    many, result = count_statements(url, page)

    assert len(result.items) == 5
    assert one == many == 3


def test_f2_one_bad_period_cant_take_the_list_down(
    module_seeded_url, client, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    pay_type = add_pay_type(url)
    for _ in range(2):
        add_item(
            url,
            tutor.id,
            pay_type,
            "999999999999.99",
            on="10-01",
            recurrence="recurring",
        )
    add_item(url, tutor.id, add_pay_type(url, direction="deduction"), "999999999999.99")
    assert code(recalculate(client, finance, period)) == OK
    run_sql(url, "DELETE FROM payroll_lines WHERE amount < 0")

    [row] = get(client, finance.cookie, ROOT, tutor_user_id=str(tutor.id)).json()[
        "items"
    ]

    assert (row["line_count"], row["total"]) == (2, None)
    assert code(read(client, finance, period)) == (409, "payroll_amount_out_of_range")


def test_f3_the_review_says_who_did_what(
    module_seeded_url, client, frozen, owner, finance, tutor
):
    url = module_seeded_url
    first = worked(url, client, owner, tutor)
    second = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    assert code(prepare(client, finance, second)) == OK
    assert code(return_period(client, owner, second, "Falta")) == OK

    returned = read(client, finance, second).json()["period"]

    assert returned["tutor_display_name"] == "Tutora Lista"
    assert returned["tutor_state"] == "active"
    assert returned["returned_by"] == {
        "id": str(owner.user.id),
        "display_name": name_of(url, owner.user.id),
    }
    assert returned["return_reason"] == "Falta"
    assert returned["prepared_by"] is None
    assert returned["earlier_unfinalized_period_id"] == str(first)
    assert returned["pending_closeouts"] == 0

    assert code(prepare(client, finance, first)) == OK
    review = read(client, owner, first).json()["period"]
    assert review["prepared_by"]["id"] == str(finance.user.id)
    assert review["prepared_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert review["earlier_unfinalized_period_id"] is None

    # The page's own value finalizes it (#110 E4).
    response = finalize(client, url, owner, first, at=review["prepared_at"])
    assert code(response) == OK, response.text
    done = read(client, finance, first).json()["period"]
    assert done["finalized_by"]["id"] == str(owner.user.id)
    assert done["total_amount"] == "110.00"
    assert (
        read(client, finance, second).json()["period"]["earlier_unfinalized_period_id"]
        is None
    )


def test_f4_pending_closeouts_is_a_warning_and_counts_held_sessions(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    lesson(url, starts_at=STARTS + timedelta(days=1), tutor=tutor)
    lesson(url, starts_at=STARTS + timedelta(days=2), tutor=tutor, state="cancelled")
    lesson(
        url,
        starts_at=STARTS + timedelta(days=2, hours=2),
        tutor=tutor,
        state="skipped_closure",
    )
    lesson(url, starts_at=STARTS + timedelta(days=9), tutor=tutor)

    assert read(client, finance, period).json()["period"]["pending_closeouts"] == 1

    assert code(prepare(client, finance, period)) == OK
    assert code(finalize(client, url, owner, period)) == OK


@pytest.mark.parametrize(
    ("starts_at", "expected"),
    [
        (datetime(2026, 11, 9, 5, 59, tzinfo=UTC), 1),
        (datetime(2026, 11, 9, 6, 0, tzinfo=UTC), 0),
    ],
)
def test_f4_the_edge_is_the_end_of_the_centers_last_day(
    module_seeded_url, client, finance, tutor, starts_at, expected
):
    """The period ends on Sunday the 8th, center time."""
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    lesson(url, starts_at=starts_at, tutor=tutor)

    body = read(client, finance, period).json()

    assert body["period"]["pending_closeouts"] == expected


# --- N2-N4: the second person -----


def test_n2_a_delegated_administrator_cant_finalize_what_they_prepared(
    module_seeded_url, client, tutor
):
    url = module_seeded_url
    both = staff(
        url, client, (ADMINISTRATOR, FINANCE_STAFF), delegations=["payroll.finalize"]
    )
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, both, period)) == OK

    response = finalize(client, url, both, period)

    assert code(response) == SELF
    assert response.json()["details"] == {"reason": "preparer"}
    assert status_of(url, period) == "prepared"


def test_n2_nobody_finalizes_their_own_pay(module_seeded_url, client, finance):
    url = module_seeded_url
    teacher = staff(
        url, client, (ADMINISTRATOR, TUTOR), delegations=["payroll.finalize"]
    )
    period = paid_tutor(url, teacher.user.id)
    assert code(prepare(client, finance, period)) == OK

    response = finalize(client, url, teacher, period)

    assert code(response) == SELF
    assert response.json()["details"] == {"reason": "own_pay"}


def test_n2_preparer_comes_first_when_both_apply(module_seeded_url, client):
    url = module_seeded_url
    both = staff(
        url,
        client,
        (ADMINISTRATOR, FINANCE_STAFF, TUTOR),
        delegations=["payroll.finalize"],
    )
    period = paid_tutor(url, both.user.id)
    assert code(prepare(client, both, period)) == OK

    assert finalize(client, url, both, period).json()["details"] == {
        "reason": "preparer"
    }


def test_n3_an_owner_finalizes_what_they_prepared(module_seeded_url, client, tutor):
    url = module_seeded_url
    sole = staff(url, client, (OWNER,))
    period = paid_tutor(url, tutor.id)

    locked(client, url, sole, sole, period)

    assert status_of(url, period) == "finalized"


def test_n3_an_owner_finalizes_their_own_pay(module_seeded_url, client):
    url = module_seeded_url
    sole = staff(url, client, (OWNER, TUTOR))
    period = paid_tutor(url, sole.user.id)

    locked(client, url, sole, sole, period)

    assert status_of(url, period) == "finalized"


def test_n4_the_latest_preparer_counts(module_seeded_url, client, finance, tutor):
    url = module_seeded_url
    first = staff(
        url, client, (ADMINISTRATOR, FINANCE_STAFF), delegations=["payroll.finalize"]
    )
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, first, period)) == OK
    assert code(return_period(client, first, period)) == OK
    assert code(prepare(client, finance, period)) == OK

    assert code(finalize(client, url, first, period)) == OK


# --- N5: reauthentication -----


def test_n5_no_reauthentication_is_refused_and_claims_no_key(
    module_seeded_url, client, finance, tutor
):
    url = module_seeded_url
    fresh = staff(url, client, (OWNER,), reauth=False)
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK
    key = new_key()

    assert code(finalize(client, url, fresh, period, key=key)) == REAUTH

    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM idempotency_keys WHERE key = :k",
            {"k": key},
        )[0].n
        == 0
    )
    assert status_of(url, period) == "prepared"


def test_n5_reauthentication_lasts_five_minutes_and_activity_doesnt_extend_it(
    module_seeded_url, client, frozen, finance, tutor
):
    url = module_seeded_url
    recent = staff(url, client, (OWNER,))
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK

    frozen.set(NOW + timedelta(minutes=4))
    assert code(get(client, recent.cookie, ROOT)) == OK
    frozen.set(NOW + timedelta(minutes=6))
    assert code(get(client, recent.cookie, ROOT)) == OK

    assert code(finalize(client, url, recent, period)) == REAUTH
    assert status_of(url, period) == "prepared"


def test_e7_prepare_and_return_need_no_reauthentication(
    module_seeded_url, client, tutor
):
    url = module_seeded_url
    fresh = staff(url, client, (FINANCE_STAFF,), reauth=False)
    period = paid_tutor(url, tutor.id)

    assert code(prepare(client, fresh, period)) == OK
    assert code(return_period(client, fresh, period)) == OK


# --- N6, N7: idempotency -----


def test_n6_no_key_is_400(module_seeded_url, client, owner, finance, tutor):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK

    client.cookies.clear()
    response = client.post(
        f"{ROOT}/{period}/finalize",
        json={"prepared_at": prepared_at(url, period)},
        headers=signed_headers(owner.cookie),
    )

    assert code(response) == (400, "idempotency_key_required")


def test_n6_a_replay_answers_the_first_response_and_writes_nothing(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    review = prepared_at(url, period)
    key = new_key()

    first = finalize(client, url, owner, period, key=key, at=review)
    again = finalize(client, url, owner, period, key=key, at=review)

    assert code(first) == code(again) == OK
    assert first.json() == again.json()
    assert REPLAYED_HEADER not in first.headers
    assert again.headers[REPLAYED_HEADER] == "true"
    assert actions(url, period).count("payroll.period_finalized") == 1
    assert len(notifications_of(url, tutor.id)) == 1


def test_n6_the_same_key_with_another_body_is_refused(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK
    key = new_key()
    assert code(finalize(client, url, owner, period, key=key)) == OK

    response = finalize(
        client, url, owner, period, key=key, at="2026-01-01T00:00:00+00:00"
    )

    assert code(response) == (422, "idempotency_key_reused")


def test_n6_a_replay_after_the_delegation_is_revoked_is_403(
    module_seeded_url, client, finance, tutor
):
    url = module_seeded_url
    reviewer = staff(url, client, (ADMINISTRATOR,), delegations=["payroll.finalize"])
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK
    review = prepared_at(url, period)
    key = new_key()
    assert code(finalize(client, url, reviewer, period, key=key, at=review)) == OK
    assert code(read(client, reviewer, period)) == OK

    run_sql(
        url,
        "DELETE FROM user_delegations WHERE user_id = :u",
        {"u": reviewer.user.id},
    )

    assert code(finalize(client, url, reviewer, period, key=key, at=review)) == DENIED
    assert code(read(client, reviewer, period)) == DENIED
    assert code(return_period(client, reviewer, period)) == DENIED


def test_n7_a_refused_finalize_keeps_no_key(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    review = prepared_at(url, period)
    key = new_key()
    item = add_item(url, tutor.id, add_pay_type(url), "40.00")

    assert code(finalize(client, url, owner, period, key=key, at=review)) == (
        409,
        "payroll_period_stale",
    )
    assert (
        run_sql(
            url,
            "SELECT count(*) AS n FROM idempotency_keys WHERE key = :k",
            {"k": key},
        )[0].n
        == 0
    )

    run_sql(url, "DELETE FROM pay_items WHERE id = :i", {"i": item})

    assert code(finalize(client, url, owner, period, key=key, at=review)) == OK


# --- N8: the figures reviewed -----


@pytest.mark.parametrize(
    "at", ["2026-12-07T17:59:59+00:00", "2026-12-07T18:00:00.000001+00:00"]
)
def test_n8_another_prepared_at_is_outdated(
    module_seeded_url, client, owner, finance, tutor, at
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK

    assert code(finalize(client, url, owner, period, at=at)) == (
        409,
        "payroll_review_outdated",
    )
    assert status_of(url, period) == "prepared"


def test_n8_the_same_instant_in_another_zone_is_the_same_review(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK

    assert code(
        finalize(client, url, owner, period, at="2026-12-07T12:00:00-06:00")
    ) == (OK)


@pytest.mark.parametrize("at", ["2026-12-07T18:00:00", "ayer"])
def test_n8_the_value_must_be_an_aware_time(
    module_seeded_url, client, owner, finance, tutor, at
):
    url = module_seeded_url
    period = paid_tutor(url, tutor.id)
    assert code(prepare(client, finance, period)) == OK

    assert finalize(client, url, owner, period, at=at).status_code == 422


# --- N9: nothing of pay in side channels -----


def test_n9_no_amount_or_reason_in_any_side_channel(
    module_seeded_url, client, frozen, owner, finance, tutor, caplog
):
    """The lesson pays 110.00, a figure nothing else in this module writes."""
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    with caplog.at_level(logging.DEBUG):
        assert code(prepare(client, finance, period)) == OK
        assert code(return_period(client, owner, period, "Pagar 110.00 secreto")) == OK
        frozen.set(NOW + timedelta(seconds=1))
        assert code(prepare(client, finance, period)) == OK
        key = new_key()
        response = finalize(client, url, owner, period, key=key)
        assert code(response) == OK

    assert "110" not in response.text
    assert "110.00" not in caplog.text
    assert "secreto" not in caplog.text
    [stored] = run_sql(
        url, "SELECT response_body FROM idempotency_keys WHERE key = :k", {"k": key}
    )
    assert set(stored.response_body) == {"id", "status", "prepared_at", "finalized_at"}
    assert "110" not in str(stored.response_body)
    audit = run_sql(
        url,
        "SELECT count(*) AS n FROM audit_events WHERE details::text LIKE '%110.00%'"
        " OR details::text LIKE '%secreto%'",
    )
    assert audit[0].n == 0
    [notice] = notifications_of(url, tutor.id)
    assert "110" not in str(notice.params)


def test_n9_a_tiny_total_is_not_in_refusals_either(
    module_seeded_url, client, owner, finance, tutor
):
    url = module_seeded_url
    period = worked(url, client, owner, tutor)
    assert code(prepare(client, finance, period)) == OK
    add_item(url, tutor.id, add_pay_type(url), "7.77")

    response = finalize(client, url, owner, period)

    assert code(response)[1] == "payroll_period_stale"
    assert response.json()["details"] == {"added": 1, "removed": 0, "changed": 0}
    assert "7.77" not in response.text
    assert "110" not in response.text


def test_d2_order_of_the_refusals(module_seeded_url, client, tutor):
    """Each check hides the next, in the binding order (#110 D2)."""
    url = module_seeded_url
    reviewer = staff(
        url, client, (ADMINISTRATOR, FINANCE_STAFF), delegations=["payroll.finalize"]
    )
    add_terms(url, tutor.id)
    add_rate(url, tutor.id)
    older = add_period_row(url, tutor.id, "11-02", "11-08")
    period = add_period_row(url, tutor.id, "11-09", "11-15", first=False)
    assert code(prepare(client, reviewer, period)) == OK

    # The preparer comes before an outdated review and the order.
    assert (
        code(finalize(client, url, reviewer, period, at="2026-01-01T00:00:00+00:00"))
        == SELF
    )

    other = staff(url, client, (ADMINISTRATOR,), delegations=["payroll.finalize"])
    # An outdated review comes before the order.
    assert code(
        finalize(client, url, other, period, at="2026-01-01T00:00:00+00:00")
    ) == (
        409,
        "payroll_review_outdated",
    )
    add_item(url, tutor.id, add_pay_type(url), "5.00", on="11-10")

    # The order comes before drift.
    response = finalize(client, url, other, period)
    assert code(response) == (409, "payroll_period_out_of_order")
    assert response.json()["details"] == {
        "earliest_period_id": str(older),
        "status": "open",
    }
